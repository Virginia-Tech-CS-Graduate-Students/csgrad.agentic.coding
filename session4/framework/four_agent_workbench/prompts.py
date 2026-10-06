from __future__ import annotations

import threading
from pathlib import Path

from .config import Settings
from .domain import ConflictError, WorkbenchError, digest
from .security import Redactor, atomic_write, secure_path


class PromptStore:
    def __init__(self, root: Path, settings: Settings):
        self.root, self.settings = root, settings
        self.lock = threading.RLock()

    def filename(self, role: str) -> str:
        return (
            self.settings.vision_path
            if role == "vision"
            else self.settings.agents[role].prompt_path
        )

    def load(self, role: str) -> tuple[str, str]:
        try:
            with self.lock:
                data = secure_path(self.root, self.filename(role)).read_bytes()
                return data.decode("utf-8"), digest(data)
        except (OSError, UnicodeError) as exc:
            raise WorkbenchError(f"Cannot read {self.filename(role)}: {exc}") from exc

    def save(self, role: str, text: str, expected_hash: str, *, overwrite=False) -> str:
        with self.lock:
            _, actual = self.load(role)
            if actual != expected_hash and not overwrite:
                raise ConflictError(
                    "The file changed outside this editor. Reload or explicitly overwrite."
                )
            try:
                atomic_write(secure_path(self.root, self.filename(role)), text.encode("utf-8"))
            except OSError as exc:
                raise WorkbenchError(f"Cannot save {self.filename(role)}: {exc}") from exc
        return digest(text.encode())

    def snapshot(self, redactor: Redactor) -> dict:
        roles = ["vision", *self.settings.agents]
        with self.lock:
            for _ in range(3):
                first = {role: self.load(role) for role in roles}
                second = {role: self.load(role) for role in roles}
                if first == second:
                    if any(not value[0].strip() for value in first.values()):
                        raise WorkbenchError(
                            "Save a nonempty vision and all four role prompts before starting"
                        )
                    if any(redactor.contains_secret(value[0]) for value in first.values()):
                        raise WorkbenchError(
                            "A prompt contains a configured credential; remove it before running"
                        )
                    return {
                        role: {"text": text, "sha256": sha} for role, (text, sha) in first.items()
                    }
        raise WorkbenchError(
            "Prompt files changed during snapshot capture; finish saving and retry"
        )
