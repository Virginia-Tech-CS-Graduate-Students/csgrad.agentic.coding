from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import threading
from pathlib import Path, PurePosixPath
from typing import Any

from .domain import WorkbenchError


def relative_name(value: str) -> str:
    if not value or "\\" in value or ":" in value or value.startswith("/"):
        raise WorkbenchError("Artifact paths must be portable relative paths")
    pieces = value.split("/")
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(10)),
        *(f"LPT{i}" for i in range(10)),
    }
    for part in pieces:
        if (
            part.casefold() in {"", ".", "..", ".env", ".git", ".runtime", "_manifest.json"}
            or part.endswith((" ", "."))
            or len(part) > 120
            or part.split(".")[0].upper() in reserved
            or re.search(r'[\x00-\x1f<>:"|?*]', part)
        ):
            raise WorkbenchError("Unsafe or reserved artifact path")
    if len(value) > 220:
        raise WorkbenchError("Artifact relative path exceeds 220 characters")
    return PurePosixPath(value).as_posix()


def is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def secure_path(root: Path, relative: str, *, artifact: bool = False) -> Path:
    if artifact:
        relative = relative_name(relative)
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or ".." in parts or "\\" in relative:
        raise WorkbenchError("Path escapes the selected workspace")
    current = root
    for part in parts:
        current = current / part
        if is_link(current):
            raise WorkbenchError("Symlinks and junctions are not permitted in managed paths")
    if not current.resolve().is_relative_to(root.resolve()):
        raise WorkbenchError("Path escapes the selected workspace")
    return current


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


class CancellationGate:
    """Linearizes acceptance with cancellation; never holds the lock over model/tool work."""

    def __init__(self):
        self.lock = threading.RLock()
        self.stopped = threading.Event()

    def check(self) -> None:
        if self.stopped.is_set():
            raise asyncio.CancelledError("Run cancelled")

    def cancel(self) -> None:
        with self.lock:
            self.stopped.set()


class Redactor:
    def __init__(self, secrets: list[str] | None = None):
        self.secrets = sorted({s for s in secrets or [] if s}, key=len, reverse=True)

    def contains_secret(self, text: str) -> bool:
        return any(s in text for s in self.secrets)

    def text(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        return re.sub(r"(?i)(Bearer\s+)[^\s\"']+", r"\1[REDACTED]", text)

    def clean(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if isinstance(value, tuple):
            return [self.clean(v) for v in value]
        if isinstance(value, dict):
            return {
                str(k): "[REDACTED]"
                if str(k).lower() in {"authorization", "api_key", "password", "secret", "token"}
                else self.clean(v)
                for k, v in value.items()
            }
        return value


class StreamRedactor:
    """Hold back possible secret prefixes, including secrets split across network chunks."""

    def __init__(self, redactor: Redactor):
        self.redactor = redactor
        self.pending = ""

    def feed(self, chunk: str, final: bool = False) -> str:
        self.pending += chunk
        if final:
            result, self.pending = self.redactor.text(self.pending), ""
            return result
        # Redact complete matches first; retain the longest suffix that might become a secret.
        clean = self.redactor.text(self.pending)
        keep = 0
        for secret in self.redactor.secrets:
            for length in range(1, min(len(secret), len(clean) + 1)):
                if clean.endswith(secret[:length]):
                    keep = max(keep, length)
        # Headers are never supplied, but prevent partial Bearer text from exposing a suffix.
        bearer = re.search(r"(?i)Bearer\s+\S*$", clean)
        if bearer:
            keep = max(keep, len(clean) - bearer.start())
        self.pending = clean[-keep:] if keep else ""
        return clean[:-keep] if keep else clean


class WorkspaceLock:
    def __init__(self, root: Path):
        self.path = secure_path(root, ".runtime/workspace.lock")
        self.stream = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        self.stream.seek(0)
        if self.path.stat().st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
        try:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            raise WorkbenchError("Another workbench is already using this workspace") from exc
        return self

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None
