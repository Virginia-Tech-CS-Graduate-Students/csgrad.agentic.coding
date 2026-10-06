import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LANGSMITH_TRACING"] = "false"


@pytest.fixture
def workspace(tmp_path):
    project = Path(__file__).resolve().parents[1]
    shutil.copytree(project / "config", tmp_path / "config")
    shutil.copytree(project / "prompts", tmp_path / "prompts")
    config = tmp_path / "config/workbench.toml"
    config.write_text(
        config.read_text().replace("cycle_pause_seconds = 0.5", "cycle_pause_seconds = 0")
    )
    return tmp_path


@pytest.fixture
def controller(workspace):
    from four_agent_workbench.execution.simulated import SimulatedRunner
    from four_agent_workbench.models.mock import MockModel
    from four_agent_workbench.orchestration import RunController

    instance = RunController(
        workspace,
        mock=True,
        model_factory=lambda _: MockModel(delay=0),
        execution_factory=lambda: SimulatedRunner(delay=0),
    )
    yield instance
    instance.close()
