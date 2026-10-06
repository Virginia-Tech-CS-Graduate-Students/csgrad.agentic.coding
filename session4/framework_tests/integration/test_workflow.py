import asyncio
import json

import pytest
from four_agent_workbench.domain import WorkbenchError
from four_agent_workbench.execution.simulated import SimulatedRunner
from four_agent_workbench.models.mock import MockModel
from four_agent_workbench.orchestration import RunController


@pytest.mark.parametrize("count", [1, 4, 10])
async def test_exact_cycles_current_inputs_and_repeated_role(controller, count):
    result = await controller.run("finite", count)
    assert result["status"] == "completed", result
    assert result["completed"] == count
    invocations = controller.records.records("invocation")
    assert len(invocations) == 5 * count
    cycles = controller.records.records("cycle")
    assert len(cycles) == count
    for cycle in cycles:
        records = {r["step_id"]: r for r in invocations if r["cycle_id"] == cycle["id"]}
        assert records["execute_tests"]["started_at"] >= records["develop"]["finished_at"]
        assert records["execute_tests"]["started_at"] >= records["prepare_tests"]["finished_at"]
        assert records["marketing"]["inputs"]["test_results"] == records["execute_tests"]["output"]
        deliveries = [
            d for d in controller.records.records("handoff") if d["cycle_id"] == cycle["id"]
        ]
        assert len(deliveries) == 7
        assert len({d["edge_id"] for d in deliveries}) == 6
    evidence = json.loads(
        controller.store.read(controller.store.current("tester"), "evidence/latest.json")
    )
    assert evidence["kind"] == "simulated"
    assert evidence["counts"]["passed"] == 0


async def test_parallel_branches_use_an_explicit_barrier(workspace):
    both_started = asyncio.Event()
    seen = set()

    class BarrierMock(MockModel):
        async def complete(self, messages, **kwargs):
            activity = kwargs["context"]["activity"]
            if activity in {"develop", "prepare_tests"}:
                seen.add(activity)
                if len(seen) == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), 3)
            if activity == "execute_tests":
                assert seen == {"develop", "prepare_tests"}
            return await super().complete(messages, **kwargs)

    controller = RunController(
        workspace,
        mock=True,
        model_factory=lambda _: BarrierMock(delay=0),
        execution_factory=lambda: SimulatedRunner(delay=0),
    )
    try:
        result = await controller.run()
        assert result["status"] == "completed", result
    finally:
        controller.close()


async def test_stop_revokes_late_model_result_and_restart_is_fresh(workspace):
    entered = asyncio.Event()

    class LateMock(MockModel):
        async def complete(self, messages, **kwargs):
            entered.set()
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                # Deliberately noncooperative provider: a late result must still be fenced.
                return '{"actions":[],"finish":{"status":"completed","summary":"late"}}'

    controller = RunController(
        workspace,
        mock=True,
        model_factory=lambda _: LateMock(),
        execution_factory=lambda: SimulatedRunner(delay=0),
    )
    try:
        task = asyncio.create_task(controller.run("continuous"))
        await asyncio.wait_for(entered.wait(), 3)
        first_run = controller.run_id
        controller.request_stop()
        result = await asyncio.wait_for(task, 3)
        assert result["status"] == "cancelled"
        assert result["completed"] == 0
        assert not controller.records.records("handoff")
        assert controller.store.current("system_engineer") is None
        controller.model_factory = lambda _: MockModel(delay=0)
        second = await controller.run()
        assert second["id"] != first_run
        assert second["status"] == "completed", second
    finally:
        controller.close()


async def test_saved_prompts_apply_together_on_next_cycle(workspace):
    first_started = asyncio.Event()
    release = asyncio.Event()
    visions = []

    class SnapshotMock(MockModel):
        async def complete(self, messages, **kwargs):
            if kwargs["context"]["activity"] == "define_requirements":
                visions.append(messages[1]["content"])
                if len(visions) == 1:
                    first_started.set()
                    await release.wait()
            return await super().complete(messages, **kwargs)

    controller = RunController(
        workspace,
        mock=True,
        model_factory=lambda _: SnapshotMock(delay=0),
        execution_factory=lambda: SimulatedRunner(delay=0),
    )
    try:
        task = asyncio.create_task(controller.run("finite", 2))
        await asyncio.wait_for(first_started.wait(), 3)
        _, sha = controller.prompts.load("vision")
        controller.prompts.save("vision", "A revised vision", sha)
        release.set()
        result = await task
        assert result["status"] == "completed", result
        assert "Hello World" in visions[0]
        assert "A revised vision" in visions[1]
    finally:
        controller.close()


async def test_product_failures_still_reach_marketing(workspace):
    controller = RunController(
        workspace,
        mock=True,
        model_factory=lambda _: MockModel(delay=0, scenario="test_failure"),
        execution_factory=lambda: SimulatedRunner(delay=0, scenario="test_failure"),
    )
    try:
        result = await controller.run("finite", 2)
        assert result["status"] == "completed", result
        text = controller.store.read(controller.store.current("marketing"), "feature_brief.md")
        assert "simulated_failure" in text
    finally:
        controller.close()


async def test_provider_failure_stops_downstream(controller):
    controller.model_factory = lambda _: MockModel(delay=0, scenario="provider_error")
    result = await controller.run("finite", 4)
    assert result["status"] == "failed"
    assert result["completed"] == 0
    assert controller.store.current("marketing") is None
    assert controller.store.current("system_engineer") is not None


async def test_continuous_stop_during_execution_and_overlap_rejection(workspace):
    executing = asyncio.Event()

    class WaitingRunner(SimulatedRunner):
        async def execute(self, **kwargs):
            self.active.add(kwargs["invocation_id"])
            executing.set()
            try:
                await asyncio.sleep(100)
            finally:
                self.active.clear()

    controller = RunController(
        workspace,
        mock=True,
        model_factory=lambda _: MockModel(delay=0),
        execution_factory=WaitingRunner,
    )
    try:
        task = asyncio.create_task(controller.run("continuous"))
        await asyncio.wait_for(executing.wait(), 3)
        with pytest.raises(WorkbenchError, match="already active"):
            await controller.run()
        controller.request_stop()
        result = await asyncio.wait_for(task, 3)
        assert result["status"] == "cancelled"
        assert result["completed"] == 0
        assert not controller.execution.active
    finally:
        controller.close()
