from __future__ import annotations

import asyncio
import json

from ..domain import WorkbenchError


class MockModel:
    """Scripted fixture model, deliberately independent of any API key or vision semantics."""

    def __init__(self, *, delay=0.02, scenario="success"):
        self.delay, self.scenario = delay, scenario

    async def complete(self, messages, *, context, preview, gate):
        del messages
        activity = context["activity"]
        turn = context["turn"]
        if self.scenario == "provider_error" and activity == "develop":
            raise WorkbenchError("Simulated provider failure for cancellation testing")
        if self.scenario == "slow":
            for _ in range(1000):
                gate.check()
                preview("Simulated model stream chunk.\n")
                await asyncio.sleep(0.1)
        actions = []

        def write(path, content):
            actions.append({"tool": "write_file", "args": {"path": path, "content": content}})

        finish = {
            "status": "completed",
            "summary": "Scripted Hello World fixture completed.",
            "requirement_ids": ["REQ-001"],
            "known_limitations": [
                "Mock model: fixed fixture, not an interpretation of the vision."
            ],
        }
        if activity == "define_requirements":
            write(
                "requirements.json",
                json.dumps(
                    {
                        "goal": "Demonstrate the four-agent pipeline with a Hello World fixture.",
                        "scope": "A Python console program prints Hello World! and exits successfully.",
                        "requirements": [
                            {
                                "id": "REQ-001",
                                "description": "Print the greeting.",
                                "acceptance_criteria": [
                                    "stdout is Hello World! followed by a newline; exit code is zero."
                                ],
                            }
                        ],
                        "assumptions": [
                            "This is a scripted mock fixture; the supplied vision is not evaluated."
                        ],
                    },
                    indent=2,
                ),
            )
            write(
                "change_log.md",
                "# Changes\n\nREQ-001: preserve the fixture greeting requirement.\n",
            )
        elif activity == "develop":
            write(
                "main.py",
                'def main():\n    print("Hello World!")\n\n\nif __name__ == "__main__":\n    main()\n',
            )
            write(
                "README.md",
                "# Hello World fixture\n\nRun `python main.py`. No third-party product dependencies.\n",
            )
            write(
                "implementation_notes.md",
                "# Implementation\n\nREQ-001 is implemented by main.py. Independent verification belongs to Tester.\n",
            )
        elif activity == "prepare_tests":
            write(
                "test_plan.md",
                "# Test plan\n\nTEST-001 verifies REQ-001 via a subprocess, checking output and exit status.\n",
            )
            write(
                "test_cases.md",
                "# Cases\n\nTEST-001 / REQ-001: run main.py; expect Hello World! and exit zero.\n",
            )
            expected = "Wrong greeting!" if self.scenario == "test_failure" else "Hello World!"
            write(
                "test_greeting.py",
                (
                    "import os\nimport subprocess\nimport sys\nfrom pathlib import Path\n\n"
                    "def test_greeting():\n"
                    "    product = Path(os.environ['PRODUCT_ROOT'])\n"
                    "    result = subprocess.run([sys.executable, str(product / 'main.py')],\n"
                    "                            capture_output=True, text=True, timeout=5)\n"
                    "    assert result.returncode == 0\n"
                    f"    assert result.stdout == {expected!r} + '\\n'\n"
                ),
            )
        elif activity == "execute_tests":
            if not context.get("has_evidence"):
                actions.append({"tool": "run_pytest", "args": {}})
                finish = None
            else:
                outcome = context.get("evidence_outcome", "unknown")
                write(
                    "test_report.md",
                    f"# Test interpretation\n\nFramework-recorded outcome: **{outcome}**.\n\nSee evidence/latest.json for execution kind, command, hashes and counts. Simulated results are not verification.\n",
                )
        elif activity == "final_synthesis":
            outcome = context.get("upstream_test_outcome", "unverified")
            write(
                "feature_brief.md",
                f"# A small Hello World program\n\nA Python console fixture prints a greeting. Run `python main.py` from the current product revision.\n\nVerification status: **{outcome}**. This scripted demonstration does not establish capabilities outside REQ-001.\n",
            )
            write(
                "claims_and_evidence.md",
                f"# Claims and evidence\n\nThe greeting is implemented in main.py (REQ-001).\n\nCurrent test outcome: {outcome}. Use the exact requirements, product and test-results revisions in handoff.json.\n",
            )
        else:
            raise WorkbenchError(f"Mock fixture does not implement activity {activity}")
        response = json.dumps({"actions": actions, "finish": finish})
        preview(f"Scripted mock: {activity}, turn {turn}.\n")
        for offset in range(0, len(response), 120):
            gate.check()
            preview(response[offset : offset + 120])
            await asyncio.sleep(self.delay)
        return response

    async def close(self):
        pass
