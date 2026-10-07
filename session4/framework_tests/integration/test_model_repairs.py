import json

import pytest
from four_agent_workbench.agents import AgentRunner
from four_agent_workbench.domain import WorkbenchError
from four_agent_workbench.models.mock import MockModel


def requirements_action(response):
    return next(
        action["args"]
        for action in response["actions"]
        if action["tool"] == "write_file" and action["args"]["path"] == "requirements.json"
    )


def invalid_requirements(response):
    args = requirements_action(response)
    document = json.loads(args["content"])
    document["goal"] = "A greeting\nwith an unescaped newline"
    args["content"] = json.dumps(document).replace("\\n", "\n")
    return json.dumps(response)


@pytest.mark.parametrize("errors", [("response", "artifact"), ("artifact", "response")])
@pytest.mark.parametrize("malformed_response", ["not valid action JSON", '{"actions": []}'])
async def test_response_and_artifact_errors_get_independent_repairs(
    controller, errors, malformed_response
):
    calls = []

    class RepairModel(MockModel):
        async def complete(self, messages, **kwargs):
            response = await super().complete(messages, **kwargs)
            if kwargs["context"]["activity"] != "define_requirements":
                return response
            calls.append(messages)
            if len(calls) <= len(errors):
                if errors[len(calls) - 1] == "response":
                    return malformed_response
                return invalid_requirements(json.loads(response))
            return response

    controller.model_factory = lambda _: RepairModel(delay=0)
    result = await controller.run()

    assert result["status"] == "completed", result
    assert len(calls) == 3
    assert "valid JSON" in json.dumps(calls[-1])
    assert "Correct the staged artifact contract" in json.dumps(calls[-1])
    assert len(controller.records.records("invocation")) == 5
    corrections = [
        event.payload["kind"]
        for event in controller.records.recent_events(run_id=result["id"])
        if event.type == "invocation.correction_requested"
    ]
    assert corrections == list(errors)


async def test_structured_requirements_preserve_escaping(controller):
    goal = 'Print "Hello Nathan!"\nKeep a tab:\tand a literal backslash: \\n'

    class StructuredModel(MockModel):
        async def complete(self, messages, **kwargs):
            response = json.loads(await super().complete(messages, **kwargs))
            if kwargs["context"]["activity"] == "define_requirements":
                args = requirements_action(response)
                args["content"] = json.loads(args["content"])
                args["content"]["goal"] = goal
            return json.dumps(response)

    controller.model_factory = lambda _: StructuredModel(delay=0)
    result = await controller.run()

    assert result["status"] == "completed", result
    ref = controller.store.current("system_engineer")
    document = json.loads(controller.store.read(ref, "requirements.json"))
    assert document["goal"] == goal


@pytest.mark.parametrize("initial_response_error", [False, True])
async def test_repeated_artifact_errors_remain_bounded_and_preserve_baseline(
    controller, initial_response_error
):
    assert (await controller.run())["status"] == "completed"
    baseline = controller.store.current("system_engineer")
    calls = []

    class BrokenArtifactModel(MockModel):
        async def complete(self, messages, **kwargs):
            assert kwargs["context"]["activity"] == "define_requirements"
            calls.append(messages)
            if initial_response_error and len(calls) == 1:
                return "not valid action JSON"
            return invalid_requirements(json.loads(await super().complete(messages, **kwargs)))

    controller.model_factory = lambda _: BrokenArtifactModel(delay=0)
    result = await controller.run()

    assert result["status"] == "failed"
    assert "Artifact contract still invalid" in result["error"]
    assert len(calls) == 2 + int(initial_response_error)
    assert controller.store.current("system_engineer") == baseline
    events = controller.records.recent_events(run_id=result["id"])
    assert not any(event.type in {"artifact.committed", "handoff.delivered"} for event in events)


async def test_structured_requirements_still_require_a_valid_schema(controller):
    calls = []

    class InvalidSchemaModel(MockModel):
        async def complete(self, messages, **kwargs):
            calls.append(messages)
            response = json.loads(await super().complete(messages, **kwargs))
            requirements_action(response)["content"] = {"goal": "Missing scope and requirements"}
            return json.dumps(response)

    controller.model_factory = lambda _: InvalidSchemaModel(delay=0)
    result = await controller.run()

    assert result["status"] == "failed"
    assert "Artifact contract still invalid" in result["error"]
    assert len(calls) == 2
    assert controller.store.current("system_engineer") is None


async def test_fenced_responses_complete_without_using_correction(controller):
    class FencedModel(MockModel):
        async def complete(self, messages, **kwargs):
            response = await super().complete(messages, **kwargs)
            fence = "`" * 3
            return f"{fence}json\n{response}\n{fence}"

    controller.model_factory = lambda _: FencedModel(delay=0)
    result = await controller.run()
    assert result["status"] == "completed", result
    assert not any(
        e.type in {"invocation.correction_requested", "invocation.response_rejected"}
        for e in controller.records.recent_events(run_id=result["id"])
    )


async def test_repair_includes_rejected_response_and_records_precise_diagnostics(controller):
    secret = "rejected-response-secret-canary"
    (controller.root / ".env").write_text(f"LLM_API_KEY={secret}\n")
    calls = []

    class RepairModel(MockModel):
        async def complete(self, messages, **kwargs):
            if kwargs["context"]["activity"] == "define_requirements":
                calls.append(messages)
                if len(calls) == 1:
                    return json.dumps({"actions": [{"tool": secret}]})
            return await super().complete(messages, **kwargs)

    controller.model_factory = lambda _: RepairModel(delay=0)
    result = await controller.run()
    assert result["status"] == "completed", result
    assert len(calls) == 2
    assert calls[1][-2]["role"] == "assistant"
    assert "[REDACTED]" in calls[1][-2]["content"]
    assert calls[1][-1]["role"] == "user"
    assert '"loc": ["actions", 0, "tool"]' in calls[1][-1]["content"]
    events = controller.records.recent_events(run_id=result["id"])
    rejected = [e for e in events if e.type == "invocation.response_rejected"]
    assert len(rejected) == 1
    assert rejected[0].agent_id == "system_engineer"
    assert rejected[0].payload["category"] == "schema"
    assert secret not in json.dumps(calls)
    assert secret not in json.dumps([e.model_dump() for e in events])


async def test_invalid_envelope_never_executes_even_its_valid_actions(controller):
    assert (await controller.run())["status"] == "completed"
    baseline = controller.store.current("system_engineer")
    calls = []

    class BrokenModel(MockModel):
        async def complete(self, messages, **kwargs):
            calls.append(messages)
            return json.dumps(
                {
                    "actions": [
                        {"tool": "write_file", "args": {"path": "unexpected.txt", "content": "no"}}
                    ],
                    "finish": {"status": "unsupported", "summary": "invalid"},
                }
            )

    controller.model_factory = lambda _: BrokenModel(delay=0)
    result = await controller.run()
    assert result["status"] == "failed"
    assert len(calls) == 2
    assert "System Engineer" in result["error"]
    assert "schema validation after one correction" in result["error"]
    assert "finish.status" in result["error"]
    assert controller.store.current("system_engineer") == baseline
    events = controller.records.recent_events(run_id=result["id"])
    assert [e.payload["attempt"] for e in events if e.type == "invocation.response_rejected"] == [
        1,
        2,
    ]
    assert not any(
        e.type in {"tool.started", "artifact.committed", "handoff.delivered"} for e in events
    )
    assert not list(controller.root.rglob("unexpected.txt"))


def test_correction_is_reserved_and_old_history_groups_are_indivisible(controller):
    runner = AgentRunner(
        controller.settings,
        controller.store,
        controller.records,
        controller.bus,
        {},
        None,
        controller.gate,
        controller.redactor,
    )
    step = controller.settings.workflow.steps[0]
    snapshot = controller.prompts.snapshot(controller.redactor)
    stage = controller.store.stage(step.agent, "context-test", None)
    base = runner._context(step, snapshot, {}, stage, [], {})
    correction = [
        {"role": "assistant", "content": "Rejected response"},
        {"role": "user", "content": "Required correction request"},
    ]
    old_group = [
        {"role": "assistant", "content": "x" * 500},
        {"role": "user", "content": "Older correction"},
    ]

    def size(messages):
        return sum(len(m["content"]) for m in messages)

    controller.settings.execution.context_char_budget = size(base) + size(correction) + 50
    messages = runner._context(step, snapshot, {}, stage, [old_group], {}, correction=correction)
    assert messages == base + correction
    assert size(messages) <= controller.settings.execution.context_char_budget

    controller.settings.execution.context_char_budget = size(base) + size(correction) - 1
    with pytest.raises(WorkbenchError, match="Required correction context exceeds"):
        runner._context(step, snapshot, {}, stage, [], {}, correction=correction)


async def test_model_turn_limit_still_bounds_response_correction(controller):
    config = controller.root / "config/workbench.toml"
    config.write_text(config.read_text().replace("max_model_turns = 8", "max_model_turns = 1"))
    calls = []

    class BrokenModel(MockModel):
        async def complete(self, messages, **kwargs):
            calls.append(messages)
            return "invalid"

    controller.model_factory = lambda _: BrokenModel(delay=0)
    result = await controller.run()
    assert result["status"] == "failed"
    assert len(calls) == 1
    assert "model-turn limit" in result["error"]
    assert controller.store.current("system_engineer") is None


async def test_cancellation_after_rejection_prevents_correction_and_publication(controller):
    calls = []
    emit = controller.bus.emit

    def stop_on_rejection(kind, **kwargs):
        event = emit(kind, **kwargs)
        if kind == "invocation.response_rejected":
            controller.request_stop()
        return event

    controller.bus.emit = stop_on_rejection

    class BrokenModel(MockModel):
        async def complete(self, messages, **kwargs):
            calls.append(messages)
            return "invalid"

    controller.model_factory = lambda _: BrokenModel(delay=0)
    result = await controller.run()
    assert result["status"] == "cancelled"
    assert len(calls) == 1
    assert controller.store.current("system_engineer") is None


@pytest.mark.parametrize("as_text", [False, True])
async def test_scope_object_rejects_whole_batch_and_preserves_earlier_stage(controller, as_text):
    calls = []

    class ScopeRepairModel(MockModel):
        async def complete(self, messages, **kwargs):
            raw = await super().complete(messages, **kwargs)
            if kwargs["context"]["activity"] != "define_requirements":
                return raw
            calls.append(messages)
            response = json.loads(raw)
            if len(calls) == 1:
                response["actions"].append(
                    {
                        "tool": "write_file",
                        "args": {"path": "earlier.txt", "content": "preserve me"},
                    }
                )
                response["finish"] = None
                return json.dumps(response)
            if len(calls) == 2:
                content = json.loads(requirements_action(response)["content"])
                content["scope"] = {"in_scope": ["Console greeting"], "out_of_scope": ["GUI"]}
                requirements_action(response)["content"] = (
                    json.dumps(content) if as_text else content
                )
                response["actions"].insert(
                    0, {"tool": "delete_file", "args": {"path": "earlier.txt"}}
                )
                response["actions"].insert(
                    1,
                    {
                        "tool": "write_file",
                        "args": {"path": "must_not_run.txt", "content": "bad batch"},
                    },
                )
                return json.dumps(response)
            assert messages[-2]["role"] == "assistant"
            assert "out_of_scope" in messages[-2]["content"]
            assert '"loc": ["scope"]' in messages[-1]["content"]
            assert "scope are strings" in messages[-1]["content"]
            earlier = list(controller.root.rglob("earlier.txt"))
            assert len(earlier) == 1 and earlier[0].read_text() == "preserve me"
            assert not list(controller.root.rglob("must_not_run.txt"))
            # Only the first turn's three writes may have run; the rejected batch ran nothing.
            events = controller.records.recent_events(run_id=controller.run_id)
            assert len([e for e in events if e.type == "tool.started"]) == 3
            return raw

    controller.model_factory = lambda _: ScopeRepairModel(delay=0)
    result = await controller.run()
    assert result["status"] == "completed", result
    assert len(calls) == 3
    ref = controller.store.current("system_engineer")
    assert controller.store.read(ref, "earlier.txt") == "preserve me"
    assert isinstance(json.loads(controller.store.read(ref, "requirements.json"))["scope"], str)
    events = controller.records.recent_events(run_id=result["id"])
    rejected = [e for e in events if e.type == "invocation.artifact_rejected"]
    assert len(rejected) == 1
    assert rejected[0].payload["path"] == "requirements.json"
    assert rejected[0].payload["action_index"] == 2
    assert rejected[0].payload["errors"][0]["loc"] == ["scope"]
    assert rejected[0].payload["errors"][0]["type"] == "string_type"


@pytest.mark.parametrize("preflight_first", [False, True])
async def test_requirements_preflight_and_final_validation_share_artifact_allowance(
    controller, preflight_first
):
    calls = []

    class BrokenModel(MockModel):
        async def complete(self, messages, **kwargs):
            calls.append(messages)
            response = json.loads(await super().complete(messages, **kwargs))
            if (len(calls) == 1) == preflight_first:
                content = json.loads(requirements_action(response)["content"])
                content["scope"] = {}
                requirements_action(response)["content"] = content
            else:
                # Missing required artifact is detected at finish, after valid tool execution.
                response["actions"] = []
            return json.dumps(response)

    controller.model_factory = lambda _: BrokenModel(delay=0)
    result = await controller.run()
    assert result["status"] == "failed"
    assert "Artifact contract still invalid" in result["error"]
    assert len(calls) == 2
    assert controller.store.current("system_engineer") is None
    events = controller.records.recent_events(run_id=result["id"])
    assert [e.payload["kind"] for e in events if e.type == "invocation.correction_requested"] == [
        "artifact"
    ]


async def test_json_fallback_event_is_durable_and_scoped_to_role_and_cycle(controller, monkeypatch):
    import httpx
    from four_agent_workbench.config import ResolvedModel
    from four_agent_workbench.models.chat import ChatModel
    from four_agent_workbench.orchestration import controller as module

    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if "response_format" in requests[-1]:
            return httpx.Response(400, json={"error": "response_format unsupported"})
        content = json.dumps(
            {
                "actions": [],
                "finish": {"status": "blocked", "summary": "Stop this offline fixture."},
            }
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    def client(*args, **kwargs):
        return ChatModel(*args, **kwargs, transport=httpx.MockTransport(handler))

    controller.mock = False
    controller.model_factory = None
    monkeypatch.setattr(module, "ChatModel", client)
    monkeypatch.setattr(
        module,
        "resolve_models",
        lambda *_: {
            role: ResolvedModel(
                endpoint="https://fixture.example/chat", api_key="offline-key", model="fixture"
            )
            for role in controller.settings.agents
        },
    )
    result = await controller.run()
    assert result["status"] == "failed" and "Stop this offline fixture" in result["error"]
    assert len(requests) == 2
    events = [
        e
        for e in controller.records.recent_events(run_id=result["id"])
        if e.type == "model.output_mode"
    ]
    assert len(events) == 2
    assert all(e.agent_id == "system_engineer" and e.cycle_id for e in events)
    assert [e.payload["effective"] for e in events] == ["json_object", "text"]
    assert events[-1].payload["reason"] == "unsupported_response_format"
