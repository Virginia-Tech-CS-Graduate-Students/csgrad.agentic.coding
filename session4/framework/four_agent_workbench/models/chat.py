from __future__ import annotations

import asyncio
import codecs
import json
import re
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime

import httpx

from ..config import ModelSettings, ResolvedModel
from ..domain import WorkbenchError
from ..security import CancellationGate, Redactor, StreamRedactor


class RetryableProviderError(WorkbenchError):
    def __init__(self, message: str, retry_after=0.0):
        super().__init__(message)
        self.retry_after = retry_after


class StreamingUnsupported(WorkbenchError):
    pass


class JsonOutputUnsupported(WorkbenchError):
    pass


def _unsupported_option(data: bytes, option: str) -> bool:
    """Recognize explicit capability rejections, never an echoed request's options."""
    try:
        body = json.loads(data)
    except ValueError:
        error = data.decode("utf-8", errors="replace")
    else:
        if not isinstance(body, dict):
            return False
        error = body.get("error", body.get("detail", ""))
    param, code = "", ""
    if isinstance(error, dict):
        param = str(error.get("param") or "").lower()
        code = str(error.get("code") or "").lower()
        message = str(error.get("message", "")).lower()
    elif isinstance(error, str):
        message = error.lower()
    else:
        return False
    names = (
        ("response_format", "json_object")
        if option == "response_format"
        else ("stream", "streaming")
    )
    if param and param not in names:
        return False
    unsupported = (
        r"unsupported|not supported|does not support|not support|unknown (?:parameter|field)|"
        r"unrecognized (?:parameter|field)|not permitted|not allowed"
    )
    if param in names:
        return code in {"unsupported_parameter", "unsupported_value"} or bool(
            re.search(unsupported, message)
        )
    name = rf"\b(?:{'|'.join(names)})\b"
    quote = r"['\"`]?"
    # Require a grammatical link to this option, not just two words somewhere in a body.
    patterns = [
        rf"{name}{quote}\s*[:=-]?\s*(?:(?:is|are)\s+)?(?:currently\s+)?"
        r"(?:unsupported|not supported|not permitted|not allowed)\b",
        rf"\b(?:unsupported|unknown|unrecognized)(?:\s+(?:parameter|field|option|value|type))?"
        rf"\s*[:=]?\s*{quote}{name}",
        rf"\b(?:does not support|do not support|doesn't support|cannot support)\s+"
        rf"(?:(?:the|parameter|field|option|value|type)\s+)*{quote}{name}",
    ]
    return any(re.search(pattern, message) for pattern in patterns)


class ChatModel:
    """Small compatible-chat transport; no SDK retries, native tools, or tracing."""

    def __init__(
        self,
        resolved: ResolvedModel,
        settings: ModelSettings,
        redactor: Redactor,
        semaphore: asyncio.Semaphore,
        transport: httpx.AsyncBaseTransport | None = None,
        output_mode: Callable[[dict], None] | None = None,
    ):
        self.resolved, self.settings, self.redactor = resolved, settings, redactor
        self.semaphore = semaphore
        self.streaming = settings.streaming != "off"
        self.json_output = settings.json_output != "off"
        self.output_mode = output_mode
        self.client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(
                connect=settings.connect_timeout_seconds,
                read=settings.response_timeout_seconds,
                write=30,
                pool=30,
            ),
        )

    async def complete(
        self,
        messages: list[dict],
        *,
        context: dict,
        preview: Callable[[str], None],
        gate: CancellationGate,
    ) -> str:
        del (
            context
        )  # Mock and live models share this interface; metadata never enters HTTP headers.
        gate.check()
        messages = self.redactor.clean(messages)
        try:
            async with self.semaphore:
                async with asyncio.timeout(self.settings.request_timeout_seconds):
                    attempts = 0
                    self._report_output_mode(
                        "configured"
                        if self.json_output or self.settings.json_output == "off"
                        else "unsupported_response_format"
                    )
                    while True:
                        gate.check()
                        if not self.streaming:
                            preview(
                                "Waiting for model response (streaming unavailable or disabled).\n"
                            )
                        try:
                            return await self._request(messages, preview, gate)
                        except JsonOutputUnsupported:
                            if self.settings.json_output != "auto" or not self.json_output:
                                raise WorkbenchError(
                                    "Provider does not support JSON output; json_output=on requires it"
                                )
                            self.json_output = False
                            self._report_output_mode("unsupported_response_format")
                            preview(
                                "Provider declined JSON output; retrying with text output and local validation.\n"
                            )
                        except StreamingUnsupported:
                            if self.settings.streaming != "auto" or not self.streaming:
                                raise WorkbenchError(
                                    "Provider does not support streaming; set streaming=off"
                                )
                            self.streaming = False
                            preview("Provider declined streaming; retrying without streaming.\n")
                        except (httpx.TransportError, RetryableProviderError, TimeoutError) as exc:
                            if attempts >= self.settings.retries:
                                raise WorkbenchError(
                                    "Model request failed after bounded retries; check service connectivity and limits"
                                ) from exc
                            delay = max(2**attempts, getattr(exc, "retry_after", 0))
                            delay = min(30, delay)
                            attempts += 1
                            preview(
                                f"Transient provider failure; retry {attempts} in {delay:g}s.\n"
                            )
                            await asyncio.sleep(delay)
        except TimeoutError as exc:
            raise WorkbenchError("Model request exceeded its overall deadline") from exc

    def _report_output_mode(self, reason: str):
        if self.output_mode:
            self.output_mode(
                {
                    "requested": self.settings.json_output,
                    "effective": "json_object" if self.json_output else "text",
                    "reason": reason,
                }
            )

    async def _request(self, messages, preview, gate) -> str:
        headers = {
            "Authorization": f"Bearer {self.resolved.api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        payload = {"model": self.resolved.model, "messages": messages, "stream": self.streaming}
        if self.json_output:
            payload["response_format"] = {"type": "json_object"}
        output: list[str] = []
        redactor = StreamRedactor(self.redactor)
        timeout = httpx.Timeout(
            connect=self.settings.connect_timeout_seconds,
            read=self.settings.first_token_timeout_seconds
            if self.streaming
            else self.settings.response_timeout_seconds,
            write=30,
            pool=30,
        )
        async with self.client.stream(
            "POST", self.resolved.endpoint, headers=headers, json=payload, timeout=timeout
        ) as response:
            if response.status_code >= 300:
                data = await self._bounded_body(response)
                # Inspect only to classify capability/context errors; never log provider bodies.
                body = data.decode("utf-8", errors="replace").lower()
                if response.status_code in {400, 422}:
                    if self.json_output and _unsupported_option(data, "response_format"):
                        raise JsonOutputUnsupported()
                    if self.streaming and _unsupported_option(data, "stream"):
                        raise StreamingUnsupported()
                if response.status_code in {401, 403}:
                    raise WorkbenchError(
                        "Model authentication was rejected; check the selected role's key"
                    )
                if response.status_code == 404:
                    raise WorkbenchError(
                        "Model endpoint returned 404. The configured URL is used literally; verify the complete chat endpoint"
                    )
                if response.status_code == 429 or response.status_code in {500, 502, 503, 504}:
                    value = response.headers.get("retry-after", "0")
                    try:
                        delay = float(value)
                    except ValueError:
                        try:
                            delay = parsedate_to_datetime(value).timestamp() - time.time()
                        except (ValueError, TypeError):
                            delay = 0
                    raise RetryableProviderError(f"Provider HTTP {response.status_code}", delay)
                if "context" in body or "token limit" in body:
                    raise WorkbenchError(
                        "Provider context limit exceeded; reduce context_char_budget or choose a larger-context model"
                    )
                raise WorkbenchError(
                    f"Provider rejected the request (HTTP {response.status_code}); verify its compatible-chat protocol"
                )
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raw = await self._bounded_body(response)
                try:
                    data = json.loads(raw)
                    choice = data["choices"][0]
                    content = choice["message"]["content"]
                    if choice.get("finish_reason") == "length":
                        raise WorkbenchError(
                            "Model output was truncated; request smaller file changes"
                        )
                    if not isinstance(content, str):
                        raise ValueError("Response content is not text")
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    raise WorkbenchError(
                        "Provider returned an invalid chat-completion response"
                    ) from exc
                gate.check()
                preview(self.redactor.text(content)[-8192:])
                return content
            iterator = self._bounded_lines(response).__aiter__()
            first_deadline = time.monotonic() + self.settings.first_token_timeout_seconds
            total, finished, received = 0, False, False
            while True:
                gate.check()
                timeout = (
                    self.settings.stream_idle_timeout_seconds
                    if received
                    else max(0.001, first_deadline - time.monotonic())
                )
                try:
                    line = await asyncio.wait_for(anext(iterator), timeout)
                except StopAsyncIteration:
                    break
                total += len(line.encode())
                if total > 2 * 1024 * 1024:
                    raise WorkbenchError("Model response exceeded the 2 MiB transport limit")
                if not line.startswith("data:"):
                    continue
                value = line[5:].strip()
                if value == "[DONE]":
                    finished = True
                    break
                try:
                    data = json.loads(value)
                    if not data.get("choices"):
                        continue  # Some compatible services emit usage-only chunks.
                    choice = data["choices"][0]
                    delta = choice.get("delta", {}).get("content")
                    if choice.get("finish_reason") == "length":
                        raise WorkbenchError("Model output was truncated; request smaller changes")
                    if choice.get("finish_reason"):
                        finished = True
                    if delta:
                        if not isinstance(delta, str):
                            raise ValueError("Non-text delta")
                        received = True
                        output.append(delta)
                        safe = redactor.feed(delta)
                        if safe:
                            preview(safe)
                except (ValueError, KeyError, TypeError, IndexError) as exc:
                    raise WorkbenchError(
                        "Malformed provider stream; no partial output was accepted"
                    ) from exc
            if not finished or not output:
                raise RetryableProviderError("Provider stream ended before completion")
            gate.check()
            preview(redactor.feed("", final=True))
        return "".join(output)

    async def _bounded_body(self, response: httpx.Response) -> bytes:
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > 2 * 1024 * 1024:
                raise WorkbenchError("Provider response exceeds 2 MiB")
        return bytes(data)

    async def _bounded_lines(self, response: httpx.Response):
        decoder = codecs.getincrementaldecoder("utf-8")()
        buffer, total = "", 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > 2 * 1024 * 1024:
                raise WorkbenchError("Provider stream exceeds 2 MiB")
            buffer += decoder.decode(chunk)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                yield line.rstrip("\r")
        buffer += decoder.decode(b"", final=True)
        if buffer:
            yield buffer.rstrip("\r")

    async def close(self):
        await self.client.aclose()
