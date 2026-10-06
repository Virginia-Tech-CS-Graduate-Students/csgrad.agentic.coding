import asyncio

from ..domain import ExecutionEvidence, now
from .common import validate_packages


class SimulatedRunner:
    """A labelled deterministic test double; never reports simulated work as passing tests."""

    simulated = True

    def __init__(self, *, scenario="success", delay=0.15):
        self.scenario, self.delay = scenario, delay
        self.active = set()

    async def preflight(self):
        return "Simulated execution (no Docker required)"

    async def recover(self):
        return None

    async def provision(self, packages, *, invocation_id, gate, preview, **kwargs):
        validate_packages(packages)
        gate.check()
        preview("Simulated dependency provisioning; no packages installed.\n")
        await asyncio.sleep(self.delay)
        gate.check()
        return packages

    async def execute(
        self,
        *,
        mode,
        product,
        tests,
        product_revision,
        test_hash,
        invocation_id,
        gate,
        preview,
        args=None,
        **kwargs,
    ):
        del product, tests, kwargs
        gate.check()
        self.active.add(invocation_id)
        started = now()
        try:
            preview(f"Simulated {mode} execution; this is not real test evidence.\n")
            await asyncio.sleep(self.delay)
            gate.check()
            failing = self.scenario == "test_failure"
            return ExecutionEvidence(
                kind="simulated",
                command=["SIMULATED", mode, *(args or [])],
                started_at=started,
                finished_at=now(),
                exit_code=1 if failing else 0,
                outcome="simulated_failure" if failing else "simulated_unverified",
                counts={"passed": 0, "failed": 0, "skipped": 0, "not_run": 1},
                product_revision=product_revision,
                test_hash=test_hash,
                image="none (simulation)",
                output="No product tests were executed.",
            )
        finally:
            self.active.discard(invocation_id)

    async def cleanup(self):
        if self.active:
            raise RuntimeError("Simulated tasks have not yet drained")
