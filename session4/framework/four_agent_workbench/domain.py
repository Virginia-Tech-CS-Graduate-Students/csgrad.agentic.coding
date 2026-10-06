from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROLES = {
    "system_engineer": ("System Engineer", "src/requirements"),
    "software_engineer": ("Software Engineer", "src/product"),
    "tester": ("Tester", "src/tests"),
    "marketing": ("Marketing", "src/ads"),
}
EDGES = (
    ("system_engineer", "software_engineer"),
    ("system_engineer", "tester"),
    ("system_engineer", "marketing"),
    ("software_engineer", "tester"),
    ("software_engineer", "marketing"),
    ("tester", "marketing"),
)
PORTS = {
    "requirements_v1": "requirements",
    "implementation_v1": "implementation",
    "test_plan_v1": "test_plan",
    "test_results_v1": "test_results",
    "marketing_v1": "marketing",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class WorkbenchError(Exception):
    """An actionable, non-secret error safe to sanitize for presentation."""


class ConflictError(WorkbenchError):
    pass


class CleanupError(WorkbenchError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactRef(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    revision_id: str
    agent_id: str
    path: str
    manifest_hash: str


class FileEntry(StrictModel):
    size: int
    sha256: str


class ArtifactManifest(StrictModel):
    schema_version: int = 1
    revision_id: str
    agent_id: str
    run_id: str
    cycle_id: str
    invocation_id: str
    contract: str
    created_at: str = Field(default_factory=now)
    files: dict[str, FileEntry]
    inputs: dict[str, ArtifactRef] = Field(default_factory=dict)
    summary: str
    simulated: bool = False


class Requirement(StrictModel):
    id: str = Field(pattern=r"^REQ-[0-9]{3,}$")
    description: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def nonblank(self):
        if any(not value.strip() for value in self.acceptance_criteria):
            raise ValueError("Acceptance criteria must not be blank")
        return self


class RequirementsDocument(StrictModel):
    goal: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    requirements: list[Requirement] = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    superseded: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [r.id for r in self.requirements]
        if len(ids) != len(set(ids)) or set(ids) & self.superseded.keys():
            raise ValueError("Requirement IDs must be unique and not superseded")
        if any(not reason.strip() for reason in self.superseded.values()):
            raise ValueError("Superseded requirements must include a nonblank reason")
        return self


class Action(StrictModel):
    tool: Literal[
        "list_artifacts",
        "read_file",
        "write_file",
        "delete_file",
        "install_dependencies",
        "run_check",
        "run_pytest",
    ]
    args: dict[str, Any] = Field(default_factory=dict)


class Finish(StrictModel):
    status: Literal["completed", "no_change", "blocked", "failed"]
    summary: str = Field(min_length=1, max_length=8000)
    requirement_ids: list[str] = Field(default_factory=list)
    known_limitations: list[str] = Field(default_factory=list)


class ModelResponse(StrictModel):
    actions: list[Action] = Field(default_factory=list, max_length=24)
    finish: Finish | None = None

    @model_validator(mode="after")
    def has_work(self):
        if not self.actions and self.finish is None:
            raise ValueError("Return actions or a finish record")
        return self


class ExecutionEvidence(StrictModel):
    evidence_id: str = Field(default_factory=lambda: new_id("evidence"))
    kind: Literal["real", "simulated"]
    command: list[str]
    started_at: str
    finished_at: str
    exit_code: int
    outcome: str
    counts: dict[str, int] = Field(default_factory=dict)
    product_revision: str
    test_hash: str
    image: str
    output: str
    packages: list[str] = Field(default_factory=list)


class Event(StrictModel):
    schema_version: int = 1
    sequence: int = 0
    timestamp: str = Field(default_factory=now)
    type: str
    run_id: str = ""
    cycle_id: str = ""
    agent_id: str = ""
    invocation_id: str = ""
    status: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
