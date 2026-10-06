import asyncio
import os
import subprocess

import pytest
from four_agent_workbench.config import load_settings
from four_agent_workbench.domain import WorkbenchError
from four_agent_workbench.execution.docker import DockerRunner
from four_agent_workbench.models.mock import MockModel
from four_agent_workbench.orchestration import RunController
from four_agent_workbench.security import CancellationGate, Redactor

pytestmark = pytest.mark.docker


@pytest.fixture(autouse=True)
def require_docker():
    if os.environ.get("FAW_DOCKER_TESTS") != "1":
        pytest.skip("Set FAW_DOCKER_TESTS=1 to execute Docker integration checks")
    result = subprocess.run(
        ["docker", "image", "inspect", "four-agent-runner:0.1"], capture_output=True, timeout=15
    )
    if result.returncode:
        pytest.fail("Docker integration was requested, but the runner image is unavailable")


@pytest.mark.parametrize("scenario,expected", [("success", "passed"), ("test_failure", "failed")])
async def test_real_pytest_evidence(workspace, scenario, expected):
    controller = RunController(
        workspace,
        mock=True,
        real_tests=True,
        scenario=scenario,
        model_factory=lambda _: MockModel(delay=0, scenario=scenario),
    )
    try:
        result = await controller.run()
        assert result["status"] == "completed", result
        import json

        evidence = json.loads(
            controller.store.read(controller.store.current("tester"), "evidence/latest.json")
        )
        assert evidence["kind"] == "real"
        assert evidence["outcome"] == expected
        assert evidence["counts"][expected] == 1
        assert not controller.execution.active
    finally:
        controller.close()


async def test_stop_kills_container_and_child_process(workspace):
    runner = DockerRunner(workspace, load_settings(workspace).execution, Redactor(), "test-cancel")
    await runner.preflight()
    product, tests = workspace / "product_fixture", workspace / "tests_fixture"
    product.mkdir()
    tests.mkdir()
    (product / "main.py").write_text("pass\n")
    (tests / "test_child.py").write_text(
        "import subprocess,sys,time\n"
        "def test_child():\n"
        "    subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(100)'])\n"
        "    print('CHILD_STARTED', flush=True)\n"
        "    time.sleep(100)\n"
    )
    # Pytest captures output by default; wait for the active container's running state instead.
    gate = CancellationGate()
    task = asyncio.create_task(
        runner.execute(
            mode="pytest",
            product=product,
            tests=tests,
            product_revision="fixture",
            test_hash="fixture",
            invocation_id="cancel",
            gate=gate,
            preview=lambda _: None,
        )
    )
    try:
        for _ in range(100):
            if runner.active:
                name = next(iter(runner.active))
                code, output = await runner._command(
                    ["inspect", name, "--format", "{{.State.Running}}"]
                )
                if code == 0 and output.strip() == "true":
                    break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("Container did not start")
        # Confirm pytest's spawned Python child exists before cancelling.
        for _ in range(100):
            code, output = await runner._command(["top", name, "-eo", "pid,args"])
            if "time.sleep(100)" in output:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail(f"Test child did not start; last Docker process inspection: {output}")
        started = asyncio.get_running_loop().time()
        gate.cancel()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await runner.cleanup()
        assert asyncio.get_running_loop().time() - started < 5
        code, _ = await runner._command(["inspect", name])
        assert code != 0
        assert not runner.active
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await runner.cleanup()


@pytest.mark.parametrize(
    "source,expected",
    [
        (
            "import pytest\n@pytest.mark.skip(reason='fixture limitation')\ndef test_skip(): pass\n",
            "skipped",
        ),
        ("def helper(): pass\n", "no_tests"),
        ("import time\ndef test_timeout(): time.sleep(20)\n", "timeout"),
    ],
)
async def test_skipped_missing_and_timed_out_tests_are_distinct(workspace, source, expected):
    settings = load_settings(workspace).execution.model_copy(update={"test_timeout_seconds": 1})
    runner = DockerRunner(workspace, settings, Redactor())
    await runner.preflight()
    product, tests = workspace / "product_fixture", workspace / "tests_fixture"
    product.mkdir()
    tests.mkdir()
    (product / "main.py").write_text("pass\n")
    (tests / "test_case.py").write_text(source)
    arguments = dict(
        mode="pytest",
        product=product,
        tests=tests,
        product_revision="fixture",
        test_hash="fixture",
        invocation_id="classify",
        gate=CancellationGate(),
        preview=lambda _: None,
    )
    try:
        if expected == "skipped":
            evidence = await runner.execute(**arguments)
            assert evidence.outcome == "skipped"
            assert evidence.counts["skipped"] == 1
            assert evidence.counts["passed"] == 0
        else:
            with pytest.raises(WorkbenchError, match=expected):
                await runner.execute(**arguments)
        assert not runner.active
    finally:
        await runner.cleanup()


async def test_wheels_are_isolated_and_readonly_inputs_work_with_private_permissions(workspace):
    runner = DockerRunner(workspace, load_settings(workspace).execution, Redactor())
    await runner.preflight()
    product, tests = workspace / "product_fixture", workspace / "tests_fixture"
    product.mkdir(mode=0o700)
    tests.mkdir(mode=0o700)
    (product / "main.py").write_text(
        "from packaging.version import Version\ndef version(): return str(Version('1.0'))\n"
    )
    (product / "main.py").chmod(0o600)
    (product / "requirements.txt").write_text("packaging==26.2\n")
    (tests / "test_version.py").write_text(
        "import os\nfrom pathlib import Path\nimport main\n"
        "def test_version():\n"
        "    assert main.version() == '1.0'\n"
        "    assert 'LLM_API_KEY' not in os.environ\n"
        "    try: Path('/product/main.py').write_text('changed')\n"
        "    except OSError: pass\n"
        "    else: raise AssertionError('product must be readonly')\n"
    )
    try:
        evidence = await runner.execute(
            mode="pytest",
            product=product,
            tests=tests,
            product_revision="fixture",
            test_hash="fixture",
            invocation_id="dependency",
            gate=CancellationGate(),
            preview=lambda _: None,
        )
        assert evidence.outcome == "passed"
        assert "packaging==26.2" in evidence.packages
        assert "def version()" in (product / "main.py").read_text()
        assert not runner.volumes
        assert not runner.active
    finally:
        await runner.cleanup()
