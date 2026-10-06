import asyncio
import json

import httpx
import pytest
from four_agent_workbench.config import ModelSettings, ResolvedModel
from four_agent_workbench.domain import WorkbenchError
from four_agent_workbench.models.chat import ChatModel
from four_agent_workbench.security import CancellationGate, Redactor


def model(handler, **settings):
    return ChatModel(
        ResolvedModel(
            endpoint="https://fixture.example/api/v1", api_key="secret-canary", model="fixture"
        ),
        ModelSettings(**settings),
        Redactor(["secret-canary"]),
        asyncio.Semaphore(2),
        transport=httpx.MockTransport(handler),
    )


async def test_literal_endpoint_and_streaming_fallback():
    requests, excerpts = [], []

    def handler(request):
        requests.append(request)
        assert str(request.url) == "https://fixture.example/api/v1"
        if json.loads(request.content)["stream"]:
            return httpx.Response(400, json={"error": "stream unsupported"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "public secret-canary"}, "finish_reason": "stop"}
                ]
            },
        )

    client = model(handler)
    try:
        text = await client.complete(
            [], context={}, preview=excerpts.append, gate=CancellationGate()
        )
        assert text == "public secret-canary"
        assert len(requests) == 2
        assert "secret-canary" not in "".join(excerpts)
        assert not client.streaming
    finally:
        await client.close()


async def test_malformed_and_auth_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "secret-canary"})

    client = model(handler)
    try:
        with pytest.raises(WorkbenchError, match="authentication") as exc:
            await client.complete([], context={}, preview=lambda _: None, gate=CancellationGate())
        assert "secret-canary" not in str(exc.value)
        assert len(calls) == 1
    finally:
        await client.close()


async def test_retry_wait_is_cancellable():
    retrying = asyncio.Event()
    client = model(lambda _: httpx.Response(429, headers={"retry-after": "30"}))
    gate = CancellationGate()

    def preview(text):
        if "retry" in text:
            retrying.set()

    try:
        task = asyncio.create_task(client.complete([], context={}, preview=preview, gate=gate))
        await asyncio.wait_for(retrying.wait(), 2)
        gate.cancel()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.5)
    finally:
        await client.close()


async def test_stream_reads_only_public_content():
    data = (
        "data: "
        + json.dumps({"choices": [{"delta": {"reasoning_content": "PRIVATE", "content": "hello"}}]})
        + "\n\n"
    )
    data += 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
    client = model(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)
    )
    excerpts = []
    try:
        text = await client.complete(
            [], context={}, preview=excerpts.append, gate=CancellationGate()
        )
        assert text == "hello"
        assert "PRIVATE" not in "".join(excerpts)
    finally:
        await client.close()
