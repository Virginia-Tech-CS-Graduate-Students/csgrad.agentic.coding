from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

from ..domain import WorkbenchError
from ..security import secure_path


def validate_packages(packages: list[str]) -> list[str]:
    if len(packages) > 100:
        raise WorkbenchError("At most 100 dependency declarations are allowed")
    result = []
    for value in packages:
        if not isinstance(value, str) or len(value) > 500:
            raise WorkbenchError("Invalid dependency declaration")
        try:
            requirement = Requirement(value)
        except InvalidRequirement as exc:
            raise WorkbenchError(
                "Dependencies must be package requirements, not pip options or paths"
            ) from exc
        if requirement.url or requirement.name.lower().replace("_", "-") in {
            "pip",
            "pytest",
            "setuptools",
            "wheel",
        }:
            raise WorkbenchError("Dependencies cannot replace runner tooling or use direct URLs")
        result.append(str(requirement))
    return sorted(set(result))


def read_packages(*roots: Path) -> list[str]:
    declarations = []
    for root in roots:
        path = secure_path(root, "requirements.txt", artifact=True)
        if path.exists():
            if path.stat().st_size > 32768:
                raise WorkbenchError("requirements.txt is too large")
            declarations.extend(
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
    return validate_packages(declarations)
