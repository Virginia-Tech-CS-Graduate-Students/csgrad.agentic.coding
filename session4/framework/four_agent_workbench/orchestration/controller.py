from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
from pathlib import Path

from langgraph.config import get_stream_writer

from ..agents import AgentRunner
from ..artifacts import ArtifactStore
from ..config import load_settings, resolve_models, secret_values
from ..domain import ArtifactRef, WorkbenchError, new_id, now
from ..events import EventBus, RecordStore
from ..execution import DockerRunner, SimulatedRunner
from ..models import ChatModel, MockModel
from ..prompts import PromptStore
from ..security import CancellationGate, Redactor, atomic_write, json_bytes, secure_path
from .graph import compile_workflow


class RunController:
    """One run owner, fresh state per cycle, and one externally callable Stop gate."""

    def __init__(
        self,
        root: Path,
        *,
        mock=False,
        real_tests=False,
        scenario="success",
        model_factory=None,
        execution_factory=None,
    ):
        self.root = root.resolve()
        self.settings = load_settings(root)
        self.mock, self.real_tests, self.scenario = mock, real_tests, scenario
        self.redactor = Redactor(secret_values(root, self.settings))
        self.records = RecordStore(root)
        self.bus = EventBus(self.records, self.redactor)
        self.store = ArtifactStore(root, self.settings, self.records, self.redactor)
        self.prompts = PromptStore(root, self.settings)
        self.store.recover()
        self.records.recover_interrupted()
        self.model_factory, self.execution_factory = model_factory, execution_factory
        self.gate = CancellationGate()
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._nodes: set[asyncio.Task] = set()
        self.execution = None
        self.run_id = ""
        self.cycle_id = ""
        self.status = "idle"
        self.completed = 0
        self.mailboxes: dict[str, dict] = {}

    @property
    def active(self):
        return self._task is not None or self.status in {"running", "stopping", "cleanup_blocked"}

    def validate_start(self) -> str | None:
        try:
            settings = load_settings(self.root)
            PromptStore(self.root, settings).snapshot(self.redactor)
            if not self.mock:
                resolve_models(self.root, settings)
            return None
        except (WorkbenchError, ValueError, OSError) as exc:
            return self.redactor.text(str(exc))[:1200]

    def request_stop(self):
        if self.status != "running":
            return
        # Callable directly from the UI thread: revoke acceptance before waiting on the loop.
        self.gate.cancel()
        self.status = "stopping"
        if self._task and self._loop:
            self._loop.call_soon_threadsafe(self._task.cancel)

    async def retry_cleanup(self):
        if self.status != "cleanup_blocked":
            return
        if self.execution:
            await self.execution.cleanup()
        self.status = "cancelled"
        record = self.records.get("run", self.run_id)
        if record:
            record.update(status="cancelled", finished_at=now())
            self.records.put("run", self.run_id, record)
        self.bus.emit(
            "run.terminated",
            run_id=self.run_id,
            cycle_id=self.cycle_id,
            status=self.status,
            payload={"completed": self.completed},
        )

    async def run(self, mode="single", count=1):
        if self.active:
            raise WorkbenchError("A run or cancellation cleanup is already active")
        if (
            mode not in {"single", "finite", "continuous"}
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
        ):
            raise WorkbenchError(
                "Choose single, finite or continuous mode with a positive integer count"
            )
        self._loop, self._task = asyncio.get_running_loop(), asyncio.current_task()
        self.gate = CancellationGate()
        self.run_id, self.cycle_id = new_id("run"), ""
        self.status, self.completed = "running", 0
        requested = 1 if mode == "single" else count if mode == "finite" else None
        record = {
            "id": self.run_id,
            "mode": mode,
            "requested": requested,
            "completed": 0,
            "status": "running",
            "created_at": now(),
            "mock_model": self.mock,
            "simulated_execution": self.mock and not self.real_tests,
        }
        clients = []
        cycle = None
        error = None
        try:
            self.records.put("run", self.run_id, record)
            self.bus.emit("run.started", run_id=self.run_id, status="running", payload=record)
            self.settings = load_settings(self.root)
            self.redactor = Redactor(secret_values(self.root, self.settings))
            self.bus.redactor = self.redactor
            self.store = ArtifactStore(self.root, self.settings, self.records, self.redactor)
            self.prompts = PromptStore(self.root, self.settings)
            snapshot = self.prompts.snapshot(self.redactor)
            semaphore = asyncio.Semaphore(self.settings.execution.max_concurrent_model_requests)
            if self.model_factory:
                models = {role: self.model_factory(role) for role in self.settings.agents}
            elif self.mock:
                models = {role: MockModel(scenario=self.scenario) for role in self.settings.agents}
            else:
                resolved = resolve_models(self.root, self.settings)
                models = {
                    role: ChatModel(value, self.settings.model, self.redactor, semaphore)
                    for role, value in resolved.items()
                }
            clients = list(models.values())
            if self.execution_factory:
                self.execution = self.execution_factory()
            elif self.mock and not self.real_tests:
                self.execution = SimulatedRunner(scenario=self.scenario)
            else:
                self.execution = DockerRunner(
                    self.root, self.settings.execution, self.redactor, self.run_id
                )
            await self.execution.preflight()
            await self.execution.recover()
            self.gate.check()
            atomic_write(
                secure_path(self.root, f".runtime/snapshots/{self.run_id}.json"),
                json_bytes(self.settings.model_dump()),
            )
            # LangSmith tracing is explicitly disabled even if the parent shell enabled it.
            os.environ["LANGCHAIN_TRACING_V2"] = "false"
            os.environ["LANGSMITH_TRACING"] = "false"
            agent_runner = AgentRunner(
                self.settings,
                self.store,
                self.records,
                self.bus,
                models,
                self.execution,
                self.gate,
                self.redactor,
            )
            cycle_context = {}

            def factory(step):
                async def node(state):
                    task = asyncio.current_task()
                    self._nodes.add(task)
                    try:
                        self.gate.check()
                        if state["cycle_id"] != self.cycle_id:
                            raise WorkbenchError(
                                "Attempted to mix graph states from different cycles"
                            )
                        inputs = {}
                        for alias, binding in self.settings.workflow.inputs.get(
                            step.id, {}
                        ).items():
                            kind, source = binding.split(".", 1)
                            raw = (
                                state["results"].get(source)
                                if kind == "current"
                                else cycle_context["baseline"].get(source)
                            )
                            if kind == "current" and raw is None:
                                raise WorkbenchError(
                                    f"Missing current-cycle prerequisite: {source}"
                                )
                            if raw:
                                inputs[alias] = (
                                    raw
                                    if isinstance(raw, ArtifactRef)
                                    else ArtifactRef.model_validate(raw)
                                )
                        baseline = (
                            inputs.get("prepared_tests")
                            if step.activity == "execute_tests"
                            else self.store.current(step.agent)
                        )
                        ref = await agent_runner.invoke(
                            step,
                            run_id=self.run_id,
                            cycle_id=self.cycle_id,
                            snapshot=cycle_context["snapshot"],
                            inputs=inputs,
                            baseline=baseline,
                            stream_writer=get_stream_writer(),
                        )
                        self.gate.check()
                        for delivery in self.settings.workflow.deliveries:
                            if delivery.from_step != step.id:
                                continue
                            for target in delivery.to:
                                self._deliver(step, target, delivery.port, ref)
                        cycle_context["results"][step.id] = ref
                        return {"results": {step.id: ref.model_dump()}}
                    finally:
                        self._nodes.discard(task)

                return node

            graph = compile_workflow(self.settings, factory)
            ordinal = 0
            while requested is None or self.completed < requested:
                self.gate.check()
                ordinal += 1
                self.cycle_id = f"{self.run_id}-c{ordinal:04d}"
                snapshot = self.prompts.snapshot(self.redactor)
                baseline = self.store.baseline()
                for ref in baseline.values():
                    if ref:
                        self.store.manifest(ref)
                self.mailboxes = {role: {} for role in self.settings.agents}
                cycle_context.clear()
                cycle_context.update(snapshot=snapshot, baseline=baseline, results={})
                cycle = {
                    "id": self.cycle_id,
                    "run_id": self.run_id,
                    "ordinal": ordinal,
                    "status": "running",
                    "started_at": now(),
                    "baseline": {k: v.model_dump() if v else None for k, v in baseline.items()},
                    "activities": [s.id for s in self.settings.workflow.steps],
                }
                atomic_write(
                    secure_path(self.root, f".runtime/snapshots/{self.cycle_id}.json"),
                    json_bytes(snapshot),
                )
                self.records.put("cycle", self.cycle_id, cycle)
                self.bus.emit(
                    "cycle.started",
                    run_id=self.run_id,
                    cycle_id=self.cycle_id,
                    status="running",
                    payload={
                        "ordinal": ordinal,
                        "completed": self.completed,
                        "requested": requested,
                    },
                )
                async for _ in graph.astream(
                    {"cycle_id": self.cycle_id, "results": {}},
                    config={"recursion_limit": max(100, len(self.settings.workflow.steps) * 4)},
                    stream_mode=["updates", "custom"],
                    version="v2",
                ):
                    self.gate.check()
                self.gate.check()
                expected_deliveries = sum(len(d.to) for d in self.settings.workflow.deliveries)
                actual_deliveries = sum(len(mailbox) for mailbox in self.mailboxes.values())
                if (
                    len(cycle_context["results"]) != len(self.settings.workflow.steps)
                    or actual_deliveries != expected_deliveries
                    or self._nodes
                    or self.execution.active
                ):
                    raise WorkbenchError(
                        "Graph ended without every required activity, handoff, and resource completing"
                    )
                outcome = json.loads(
                    self.store.read(
                        cycle_context["results"]["execute_tests"], "evidence/latest.json"
                    )
                )["outcome"]
                with self.gate.lock:
                    self.gate.check()
                    self.completed += 1
                    cycle.update(status="completed", finished_at=now(), test_outcome=outcome)
                    self.records.put("cycle", self.cycle_id, cycle)
                    record["completed"] = self.completed
                    self.records.put("run", self.run_id, record)
                self.bus.emit(
                    "cycle.completed",
                    run_id=self.run_id,
                    cycle_id=self.cycle_id,
                    status="completed",
                    payload={"completed": self.completed, "test_outcome": outcome},
                )
                self.store.prune()
                cycle = None
                if requested is None or self.completed < requested:
                    await asyncio.sleep(self.settings.execution.cycle_pause_seconds)
            self.status = "completed"
        except asyncio.CancelledError:
            self.gate.cancel()
            self.status = "cancelled"
        except Exception as exc:
            self.gate.cancel()
            self.status = "failed"
            error = self.redactor.text(str(exc))[:2000] or type(exc).__name__
        finally:
            cleanup = asyncio.create_task(self._finalize(record, cycle, clients, error))
            # A cancellation callback may arrive after a gate check already raised CancelledError.
            # Repeated cancellation must never interrupt cleanup or prematurely enable Start.
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    self.gate.cancel()
            await cleanup
        return record

    async def _finalize(self, record, cycle, clients, error):
        for task in tuple(self._nodes):
            task.cancel()
        if self._nodes:
            await asyncio.gather(*tuple(self._nodes), return_exceptions=True)
        if cycle and cycle["status"] == "running":
            cycle.update(
                status="cancelled" if self.status == "cancelled" else "failed", finished_at=now()
            )
            try:
                self.records.put("cycle", cycle["id"], cycle)
                self.bus.emit(
                    "cycle.terminated",
                    run_id=self.run_id,
                    cycle_id=cycle["id"],
                    status=cycle["status"],
                )
            except (OSError, sqlite3.Error):
                error = "Runtime metadata could not be saved; check disk space and permissions."
        try:
            if self.execution:
                await self.execution.cleanup()
        except (WorkbenchError, TimeoutError) as exc:
            self.status = "cleanup_blocked"
            error = self.redactor.text(str(exc))
        await asyncio.gather(*(client.close() for client in clients), return_exceptions=True)
        record.update(status=self.status, completed=self.completed, finished_at=now(), error=error)
        try:
            self.records.put("run", self.run_id, record)
            self.bus.emit(
                "run.terminated",
                run_id=self.run_id,
                cycle_id=self.cycle_id,
                status=self.status,
                payload={"completed": self.completed, "error": error},
            )
        except (OSError, sqlite3.Error):
            if self.status != "cleanup_blocked":
                self.status = "failed"
            error = "Runtime metadata could not be saved; check disk space and permissions."
            record.update(status=self.status, error=error)
            self.bus.emit(
                "run.terminated",
                transient=True,
                run_id=self.run_id,
                cycle_id=self.cycle_id,
                status=self.status,
                payload={"completed": self.completed, "error": error},
            )
        finally:
            self._task = None

    def _deliver(self, step, target, port, reference):
        invocation = f"{self.cycle_id}-{step.id}"
        handoff_id = f"{invocation}-{port}-{target}"
        with self.gate.lock:
            self.gate.check()
            mailbox = self.mailboxes[target]
            if handoff_id in mailbox:
                if mailbox[handoff_id]["artifact"] != reference.model_dump():
                    raise WorkbenchError("Conflicting duplicate delivery")
                return
            record = {
                "id": handoff_id,
                "run_id": self.run_id,
                "cycle_id": self.cycle_id,
                "source_invocation": invocation,
                "destination": target,
                "edge_id": f"{step.agent}->{target}",
                "kind": port,
                "artifact": reference.model_dump(),
                "status": "delivered",
                "delivered_at": now(),
            }
            self.records.put("handoff", handoff_id, record)
            mailbox[handoff_id] = record
            self.bus.emit(
                "handoff.delivered",
                run_id=self.run_id,
                cycle_id=self.cycle_id,
                agent_id=step.agent,
                invocation_id=invocation,
                status="delivered",
                payload={
                    "edge_id": record["edge_id"],
                    "kind": port,
                    "artifact_refs": [reference.model_dump()],
                },
            )

    def close(self):
        self.records.close()


class BackgroundLoop:
    def __init__(self):
        self.ready = threading.Event()
        self.loop = None
        self.thread = threading.Thread(target=self._run, name="workbench-asyncio", daemon=True)
        self.thread.start()
        self.ready.wait()

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        self.loop.run_forever()
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.run_until_complete(self.loop.shutdown_default_executor())
        self.loop.close()

    def submit(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
