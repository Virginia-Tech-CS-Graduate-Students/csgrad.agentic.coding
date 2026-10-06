from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import Field, SecretStr, model_validator

from ..domain import EDGES, PORTS, ROLES, StrictModel, WorkbenchError


class ModelSettings(StrictModel):
    adapter: str = "openai_chat"
    endpoint_kind: str = "full_url"
    endpoint_env: str = "LLM_ENDPOINT_URL"
    api_key_env: str = "LLM_API_KEY"
    model_env: str = "LLM_MODEL"
    streaming: str = "auto"
    connect_timeout_seconds: float = Field(default=10, gt=0)
    first_token_timeout_seconds: float = Field(default=60, gt=0)
    stream_idle_timeout_seconds: float = Field(default=30, gt=0)
    response_timeout_seconds: float = Field(default=180, gt=0)
    request_timeout_seconds: float = Field(default=240, gt=0)
    retries: int = Field(default=2, ge=0, le=5)

    @model_validator(mode="after")
    def supported(self):
        if self.adapter != "openai_chat" or self.endpoint_kind != "full_url":
            raise ValueError("Version one requires openai_chat with endpoint_kind=full_url")
        if self.streaming not in {"auto", "on", "off"}:
            raise ValueError("streaming must be auto, on, or off")
        return self


class ModelOverride(StrictModel):
    model_env: str | None = None
    endpoint_env: str | None = None
    api_key_env: str | None = None

    @model_validator(mode="after")
    def explicit_credentials(self):
        if self.endpoint_env and not self.api_key_env:
            raise ValueError("An endpoint override must name its own api_key_env")
        return self


class AgentSettings(StrictModel):
    display_name: str
    prompt_path: str
    output_dir: str
    model: ModelOverride = Field(default_factory=ModelOverride)


class Step(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    agent: str
    activity: str
    after: list[str] = Field(default_factory=list)
    contract: str


class Delivery(StrictModel):
    from_step: str
    port: str
    to: list[str] = Field(min_length=1)


class Workflow(StrictModel):
    steps: list[Step] = Field(min_length=5, max_length=32)
    deliveries: list[Delivery]
    inputs: dict[str, dict[str, str]]


class ExecutionSettings(StrictModel):
    runner: str = "docker"
    product_language: str = "python"
    test_runner: str = "pytest"
    image: str = "four-agent-runner:0.1"
    dependency_policy: str = "agent_selected_wheels"
    package_index: str = "https://pypi.org/simple"
    max_concurrent_model_requests: int = Field(default=2, ge=1, le=4)
    max_model_turns: int = Field(default=8, ge=1, le=40)
    max_tool_calls: int = Field(default=24, ge=1, le=200)
    invocation_timeout_seconds: float = Field(default=900, gt=0)
    test_timeout_seconds: float = Field(default=120, gt=0)
    dependency_timeout_seconds: float = Field(default=300, gt=0)
    max_test_executions: int = Field(default=2, ge=1, le=5)
    context_char_budget: int = Field(default=48000, ge=4000)
    cpus: float = Field(default=2, gt=0)
    memory_mb: int = Field(default=1024, ge=128)
    pids_limit: int = Field(default=128, ge=16)
    cycle_pause_seconds: float = Field(default=0.5, ge=0, le=60)

    @model_validator(mode="after")
    def supported(self):
        if (self.runner, self.product_language, self.test_runner, self.dependency_policy) != (
            "docker",
            "python",
            "pytest",
            "agent_selected_wheels",
        ):
            raise ValueError("Version one supports Docker, Python, pytest, and wheels only")
        parsed = urlsplit(self.package_index)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query:
            raise ValueError("package_index must be a credential-free HTTPS index URL")
        return self


class ArtifactSettings(StrictModel):
    retain_cycles: int = Field(default=10, ge=1)
    max_file_bytes: int = Field(default=1048576, ge=1024)
    max_revision_bytes: int = Field(default=20971520, ge=1024)
    max_files: int = Field(default=200, ge=1)


class Settings(StrictModel):
    schema_version: int = 1
    vision_path: str = "prompts/vision.md"
    model: ModelSettings = Field(default_factory=ModelSettings)
    agents: dict[str, AgentSettings]
    workflow: Workflow
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    artifacts: ArtifactSettings = Field(default_factory=ArtifactSettings)

    @model_validator(mode="after")
    def valid_graph(self):
        if self.schema_version != 1 or set(self.agents) != set(ROLES):
            raise ValueError("Configuration must define schema version 1 and exactly four roles")
        for role, agent in self.agents.items():
            if agent.output_dir != ROLES[role][1]:
                raise ValueError(f"{role} must write to {ROLES[role][1]}")
            if not re.fullmatch(r"prompts/[a-zA-Z0-9_.-]+\.md", agent.prompt_path):
                raise ValueError("Role prompts must be Markdown files directly inside prompts/")
        if self.vision_path != "prompts/vision.md":
            raise ValueError("The vision is sourced from prompts/vision.md")
        steps = {s.id: s for s in self.workflow.steps}
        if len(steps) != len(self.workflow.steps):
            raise ValueError("Activity IDs must be unique")
        expected = {
            "requirements": ("system_engineer", "define_requirements", "requirements_v1"),
            "develop": ("software_engineer", "develop", "implementation_v1"),
            "prepare_tests": ("tester", "prepare_tests", "test_plan_v1"),
            "execute_tests": ("tester", "execute_tests", "test_results_v1"),
            "marketing": ("marketing", "final_synthesis", "marketing_v1"),
        }
        for key, expected_values in expected.items():
            s = steps.get(key)
            if s is None or (s.agent, s.activity, s.contract) != expected_values:
                raise ValueError(f"Required activity {key} must retain its role/activity/contract")
        ancestors: dict[str, set[str]] = {}

        def visit(key: str, visiting: set[str]) -> set[str]:
            if key in ancestors:
                return ancestors[key]
            if key in visiting or key not in steps:
                raise ValueError("Workflow contains a cycle or a missing dependency")
            s = steps[key]
            if s.agent not in ROLES or s.contract not in PORTS:
                raise ValueError(f"Unsupported role/contract in {key}")
            if len(s.after) != len(set(s.after)):
                raise ValueError(f"Duplicate dependency in {key}")
            result = set(s.after)
            for parent in s.after:
                result |= visit(parent, visiting | {key})
            ancestors[key] = result
            return result

        for key in steps:
            visit(key, set())
        if [s.id for s in steps.values() if not s.after] != ["requirements"]:
            raise ValueError("requirements must be the sole entry activity")
        for key in ("develop", "prepare_tests"):
            if steps[key].after != ["requirements"]:
                raise ValueError("Development and test preparation must fan out from requirements")
        if not {"develop", "prepare_tests"} <= set(steps["execute_tests"].after):
            raise ValueError("execute_tests requires an explicit two-source barrier")
        if not {"requirements", "develop", "execute_tests"} <= set(steps["marketing"].after):
            raise ValueError("Marketing must wait for all three current-cycle sources")
        for a in steps.values():
            for b in steps.values():
                if a.id < b.id and a.agent == b.agent:
                    if a.id not in ancestors[b.id] and b.id not in ancestors[a.id]:
                        raise ValueError("Activities sharing a role must have explicit ordering")
        for key, bindings in self.workflow.inputs.items():
            if key not in steps:
                raise ValueError(f"Unknown input destination {key}")
            for binding in bindings.values():
                kind, sep, source = binding.partition(".")
                if not sep or kind not in {"previous", "current"}:
                    raise ValueError(f"Invalid input binding {binding}")
                if kind == "current" and source not in ancestors[key]:
                    raise ValueError(f"{key} reads {source} without waiting for it")
                if kind == "previous" and source not in {
                    "requirements",
                    "product",
                    "tests",
                    "test_results",
                    "ads",
                }:
                    raise ValueError(f"Unknown baseline {source}")
        required_inputs = {
            "develop": {"requirements": "current.requirements"},
            "prepare_tests": {"requirements": "current.requirements"},
            "execute_tests": {
                "requirements": "current.requirements",
                "product": "current.develop",
                "prepared_tests": "current.prepare_tests",
            },
            "marketing": {
                "requirements": "current.requirements",
                "implementation": "current.develop",
                "test_plan": "current.prepare_tests",
                "test_results": "current.execute_tests",
            },
        }
        for key, bindings in required_inputs.items():
            if not bindings.items() <= self.workflow.inputs.get(key, {}).items():
                raise ValueError(f"Missing required current-cycle input bindings for {key}")
        edges, seen = set(), set()
        for d in self.workflow.deliveries:
            if d.from_step not in steps or d.port != PORTS[steps[d.from_step].contract]:
                raise ValueError("Invalid delivery source or output port")
            for target in d.to:
                edge = (steps[d.from_step].agent, target)
                identity = (d.from_step, d.port, target)
                if edge not in EDGES or identity in seen:
                    raise ValueError("Unknown or duplicate handoff")
                edges.add(edge)
                seen.add(identity)
        if edges != set(EDGES):
            raise ValueError("All six visible handoff relationships are required")
        for port, source in (("test_plan", "prepare_tests"), ("test_results", "execute_tests")):
            if (source, port, "marketing") not in seen:
                raise ValueError("Marketing must receive both test plans and final test results")
        return self


class ResolvedModel(StrictModel):
    endpoint: str
    api_key: SecretStr
    model: str


def load_settings(root: Path) -> Settings:
    try:
        with (root / "config/workbench.toml").open("rb") as stream:
            return Settings.model_validate(tomllib.load(stream))
    except (OSError, ValueError) as exc:
        raise WorkbenchError(f"Cannot load config/workbench.toml: {exc}") from exc


def environment(root: Path) -> dict[str, str]:
    values = {k: v for k, v in dotenv_values(root / ".env", interpolate=False).items() if v}
    # Explicit process environment takes precedence over the .env file.
    values.update(os.environ)
    return values


def secret_values(root: Path, settings: Settings | None = None) -> list[str]:
    values = environment(root)
    names = {k for k in values if re.search(r"KEY|TOKEN|SECRET|PASSWORD", k, re.I)}
    if settings:
        names.add(settings.model.api_key_env)
        names.update(a.model.api_key_env for a in settings.agents.values() if a.model.api_key_env)
    return [values[name] for name in names if values.get(name)]


def resolve_models(root: Path, settings: Settings) -> dict[str, ResolvedModel]:
    env = environment(root)
    aliases = {
        "LLM_ENDPOINT_URL": "OPENAI_API_BASE_URL",
        "LLM_API_KEY": "OPENAI_API_KEY",
        "LLM_MODEL": "OPENAI_API_MODEL",
    }

    def required(name: str) -> str:
        legacy = aliases.get(name)
        value, old = env.get(name), env.get(legacy) if legacy else None
        if value and old and value != old:
            raise WorkbenchError(f"Conflicting values for {name} and {legacy}; keep one")
        result = value or old
        if not result or result.startswith("replace-with-"):
            raise WorkbenchError(f"Set {name} in .env, or start with --mock")
        return result

    result = {}
    for role, agent in settings.agents.items():
        override = agent.model
        endpoint = required(override.endpoint_env or settings.model.endpoint_env)
        url = urlsplit(endpoint)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.fragment:
            raise WorkbenchError(
                f"Invalid complete model endpoint for {role}; use HTTP(S), no user info"
            )
        result[role] = ResolvedModel(
            endpoint=endpoint,
            api_key=required(override.api_key_env or settings.model.api_key_env),
            model=required(override.model_env or settings.model.model_env),
        )
    return result
