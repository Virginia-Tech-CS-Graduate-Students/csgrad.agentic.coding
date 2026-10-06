import copy

import pytest
from four_agent_workbench.config import Settings, load_settings, resolve_models
from four_agent_workbench.domain import WorkbenchError
from four_agent_workbench.execution.common import validate_packages
from four_agent_workbench.orchestration.graph import merge_results
from pydantic import ValidationError


def test_template_and_overrides(workspace, monkeypatch):
    for key in (
        "LLM_ENDPOINT_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "OPENAI_API_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_API_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    (workspace / ".env").write_text(
        "OPENAI_API_BASE_URL=https://service.example/api/v1\nOPENAI_API_KEY=canary-secret\nOPENAI_API_MODEL=fixture\n"
    )
    settings = load_settings(workspace)
    models = resolve_models(workspace, settings)
    assert models["tester"].endpoint == "https://service.example/api/v1"
    assert "canary-secret" not in repr(models["tester"])
    monkeypatch.setenv("LLM_MODEL", "conflicting")
    with pytest.raises(WorkbenchError, match="Conflicting"):
        resolve_models(workspace, settings)


@pytest.mark.parametrize(
    "change", ["cycle", "barrier", "missing_input", "duplicate", "role", "edge", "credential"]
)
def test_rejects_invalid_workflows(workspace, change):
    data = copy.deepcopy(load_settings(workspace).model_dump())
    if change == "cycle":
        data["workflow"]["steps"][0]["after"] = ["marketing"]
    elif change == "barrier":
        data["workflow"]["steps"][3]["after"] = ["develop"]
    elif change == "missing_input":
        del data["workflow"]["inputs"]["marketing"]["test_results"]
    elif change == "duplicate":
        data["workflow"]["steps"].append(data["workflow"]["steps"][1])
    elif change == "role":
        data["agents"]["tester"]["output_dir"] = "framework_tests"
    elif change == "edge":
        data["workflow"]["deliveries"][0]["to"] = ["tester", "marketing"]
    else:
        data["agents"]["tester"]["model"] = {"endpoint_env": "TESTER_ENDPOINT"}
    with pytest.raises(ValidationError):
        Settings.model_validate(data)


def test_merge_disjoint_and_duplicate_results():
    assert merge_results({"develop": 1}, {"prepare": 2}) == {"develop": 1, "prepare": 2}
    assert merge_results({"develop": 1}, {"develop": 1}) == {"develop": 1}
    with pytest.raises(WorkbenchError, match="duplicate"):
        merge_results({"develop": 1}, {"develop": 2})


@pytest.mark.parametrize(
    "value",
    [
        "--index-url=https://evil.example",
        "thing @ https://evil.example/a.whl",
        "../local",
        "pytest==0",
        "-r other.txt",
    ],
)
def test_dependency_boundary(value):
    with pytest.raises(WorkbenchError):
        validate_packages([value])


def test_allowed_wheel_specifications():
    assert validate_packages(["requests>=2", "requests>=2"]) == ["requests>=2"]
