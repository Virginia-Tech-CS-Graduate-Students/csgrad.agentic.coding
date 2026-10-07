import asyncio
import json

import httpx
import pytest
from four_agent_workbench.config import ModelSettings, ResolvedModel
from four_agent_workbench.domain import WorkbenchError
from four_agent_workbench.models.chat import ChatModel
from four_agent_workbench.security import CancellationGate, Redactor


def model(handler, *, output_mode=None, **settings):
    return ChatModel(
        ResolvedModel(
            endpoint="https://fixture.example/api/v1", api_key="secret-canary", model="fixture"
        ),
        ModelSettings(**settings),
        Redactor(["secret-canary"]),
        asyncio.Semaphore(2),
        transport=httpx.MockTransport(handler),
        output_mode=output_mode,
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


@pytest.mark.parametrize("mode", ["auto", "on", "off"])
async def test_json_output_modes_request_option_and_report_mode(mode):
    requests, events = [], []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = model(handler, json_output=mode, streaming="off", output_mode=events.append)
    try:
        assert (
            await client.complete([], context={}, preview=lambda _: None, gate=CancellationGate())
            == "{}"
        )
        if mode == "off":
            assert "response_format" not in requests[0]
        else:
            assert requests[0]["response_format"] == {"type": "json_object"}
        assert events == [
            {
                "requested": mode,
                "effective": "text" if mode == "off" else "json_object",
                "reason": "configured",
            }
        ]
    finally:
        await client.close()


@pytest.mark.parametrize(
    "error",
    [
        {"error": "response_format is not supported"},
        {"error": {"param": "response_format", "code": "unsupported_parameter"}},
        {"error": {"message": "This model does not support json_object"}},
        {"error": {"param": None, "message": "response_format is not supported"}},
        {"detail": "Unknown parameter: response_format"},
    ],
)
async def test_explicit_json_rejection_falls_back_once_and_is_remembered(error):
    requests, events, previews = [], [], []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if "response_format" in body:
            return httpx.Response(400, json=error)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = model(handler, streaming="off", output_mode=events.append)
    try:
        for _ in range(2):
            await client.complete([], context={}, preview=previews.append, gate=CancellationGate())
        assert len(requests) == 3
        assert ["response_format" in r for r in requests] == [True, False, False]
        assert (
            events[1:]
            == [{"requested": "auto", "effective": "text", "reason": "unsupported_response_format"}]
            * 2
        )
        assert "Provider declined JSON output" in "".join(previews)
    finally:
        await client.close()


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, {"error": "response_format must be an object"}),
        (422, {"error": "Invalid schema for response_format"}),
        (400, {"error": "Unsupported model selected for request with response_format=json_object"}),
        (400, {"error": "Context limit exceeded", "request": {"response_format": "unsupported"}}),
        (
            400,
            {
                "error": {
                    "param": "temperature",
                    "message": "unsupported parameter",
                    "response_format": "json_object",
                }
            },
        ),
        (401, {"error": "response_format unsupported"}),
        (403, {"error": "response_format unsupported"}),
        (404, {"error": "response_format unsupported"}),
        (200, {"error": "response_format unsupported"}),
    ],
)
async def test_unrelated_or_success_response_errors_never_downgrade_json(status, body):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    client = model(handler, streaming="off")
    try:
        with pytest.raises(WorkbenchError):
            await client.complete([], context={}, preview=lambda _: None, gate=CancellationGate())
        assert len(calls) == 1
        assert client.json_output
    finally:
        await client.close()


async def test_json_on_requires_support_without_downgrade():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(422, json={"error": "json_object not supported"})

    client = model(handler, json_output="on")
    try:
        with pytest.raises(WorkbenchError, match="json_output=on requires"):
            await client.complete([], context={}, preview=lambda _: None, gate=CancellationGate())
        assert len(calls) == 1
        assert client.json_output
    finally:
        await client.close()


@pytest.mark.parametrize("first", ["stream", "response_format"])
async def test_capability_negotiation_is_independent_and_bounded(first):
    requests = []
    order = [first, "stream" if first == "response_format" else "response_format"]

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        for option in order:
            if body.get(option):
                return httpx.Response(
                    400, json={"error": {"param": option, "code": "unsupported_parameter"}}
                )
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = model(handler, retries=0)
    try:
        await client.complete([], context={}, preview=lambda _: None, gate=CancellationGate())
        assert len(requests) == 3
        assert requests[0]["stream"] and "response_format" in requests[0]
        assert not requests[-1]["stream"] and "response_format" not in requests[-1]
        assert bool(requests[1].get(order[1]))
    finally:
        await client.close()


async def test_repeated_capability_rejection_does_not_loop():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": "response_format unsupported"})

    client = model(handler, streaming="off")
    try:
        with pytest.raises(WorkbenchError, match="HTTP 400"):
            await client.complete([], context={}, preview=lambda _: None, gate=CancellationGate())
        assert len(calls) == 2
    finally:
        await client.close()


async def test_json_and_stream_fallback_share_overall_deadline():
    requests = []

    async def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(400, json={"error": "response_format unsupported"})
        if len(requests) == 2:
            return httpx.Response(400, json={"error": "stream unsupported"})
        await asyncio.sleep(10)
        return httpx.Response(200)

    client = model(handler, request_timeout_seconds=0.1)
    try:
        with pytest.raises(WorkbenchError, match="overall deadline"):
            await asyncio.wait_for(
                client.complete([], context={}, preview=lambda _: None, gate=CancellationGate()), 1
            )
        assert len(requests) == 3
    finally:
        await client.close()


async def test_stream_rejection_does_not_disable_json_when_error_mentions_both():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if body["stream"]:
            return httpx.Response(
                400, json={"error": "response_format accepts json_object; streaming unsupported"}
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = model(handler)
    try:
        await client.complete([], context={}, preview=lambda _: None, gate=CancellationGate())
        assert len(requests) == 2
        assert all(r["response_format"] == {"type": "json_object"} for r in requests)
    finally:
        await client.close()


def test_json_mode_defaults_and_invalid_configuration():
    from pydantic import ValidationError

    assert ModelSettings().json_output == "auto"
    with pytest.raises(ValidationError, match="json_output must"):
        ModelSettings(json_output="sometimes")
