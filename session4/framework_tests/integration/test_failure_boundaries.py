import json

import pytest
from four_agent_workbench.domain import WorkbenchError
from four_agent_workbench.models.mock import MockModel


async def test_malformed_responses_have_one_repair_and_no_publication(controller):
    calls = []

    class BrokenModel(MockModel):
        async def complete(self, messages, **kwargs):
            calls.append(messages)
            return "not a structured response"

    controller.model_factory = lambda _: BrokenModel(delay=0)
    result = await controller.run()
    assert result["status"] == "failed"
    assert len(calls) == 2
    assert "valid JSON" in calls[-1][-1]["content"]
    assert not controller.records.records("handoff")
    assert controller.store.current("system_engineer") is None


async def test_secret_canary_cannot_reach_context_artifacts_or_durable_events(controller):
    secret = "test-credential-CANARY-79"
    (controller.root / ".env").write_text(f"LLM_API_KEY={secret}\n")
    seen = []

    class LeakingModel(MockModel):
        async def complete(self, messages, **kwargs):
            seen.append(json.dumps(messages))
            kwargs["preview"](f"A response tried to emit {secret}")
            return json.dumps(
                {
                    "actions": [
                        {
                            "tool": "write_file",
                            "args": {
                                "path": "leaked.txt",
                                "content": secret,
                            },
                        }
                    ]
                }
            )

    controller.model_factory = lambda _: LeakingModel(delay=0)
    result = await controller.run()
    assert result["status"] == "failed"
    assert all(secret not in value for value in seen)
    assert secret not in json.dumps(result)
    assert secret not in json.dumps([event.model_dump() for event in controller.bus.drain()])
    assert secret not in json.dumps(
        [event.model_dump() for event in controller.records.recent_events()]
    )
    assert not list((controller.root / "src").rglob("leaked.txt"))


async def test_continuous_stops_after_completed_cycle_without_starting_another(controller):
    # Each run recreates its ArtifactStore; observe the durable cycle event instead.
    emit = controller.bus.emit

    def stop_after_second(kind, **kwargs):
        event = emit(kind, **kwargs)
        if kind == "cycle.completed" and kwargs["payload"]["completed"] == 2:
            controller.request_stop()
        return event

    controller.bus.emit = stop_after_second
    result = await controller.run("continuous")
    assert result["status"] == "cancelled"
    assert result["completed"] == 2
    assert len(controller.records.records("cycle")) == 2
    assert len(controller.records.records("invocation")) == 10
    assert all(cycle["status"] == "completed" for cycle in controller.records.records("cycle"))


async def test_retention_keeps_ten_cycles_and_current_evidence(controller):
    result = await controller.run("finite", 12)
    assert result["status"] == "completed"
    revisions = controller.records.records("revision")
    retained_cycles = set()
    for revision in revisions:
        exists = (controller.root / revision["reference"]["path"]).exists()
        assert exists != revision.get("pruned", False)
        if exists:
            retained_cycles.add(revision["cycle_id"])
    assert len(retained_cycles) == 10
    for reference in controller.store.baseline().values():
        if reference:
            controller.store.manifest(reference)


async def test_failed_cleanup_blocks_new_run_until_retry(controller, monkeypatch):
    await controller.run()

    async def broken_cleanup():
        raise WorkbenchError("Docker is temporarily unavailable")

    runner = controller.execution
    real_cleanup = runner.cleanup
    monkeypatch.setattr(runner, "cleanup", broken_cleanup)
    controller.execution_factory = lambda: runner
    result = await controller.run()
    assert result["status"] == "cleanup_blocked"
    assert controller.active
    with pytest.raises(WorkbenchError, match="already active"):
        await controller.run()
    monkeypatch.setattr(runner, "cleanup", real_cleanup)
    await controller.retry_cleanup()
    assert not controller.active
