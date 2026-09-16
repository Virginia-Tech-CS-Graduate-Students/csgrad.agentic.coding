import os
from dataclasses import dataclass, field
from pathlib import Path

from source_to_transcript.engine import DEFAULT_MODEL_DIR, MAX_BYTES, ROOT


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("TRANSCRIPT_DATA_DIR", ROOT / "data")).resolve())
    model_dir: Path = field(default_factory=lambda: Path(os.environ.get("TRANSCRIPT_MODEL_DIR", DEFAULT_MODEL_DIR)).resolve())
    ui_dir: Path = ROOT / "ui" / "dist"
    max_bytes: int = MAX_BYTES
    allowed_origins: tuple[str, ...] = ("http://127.0.0.1:8765", "http://localhost:8765", "http://127.0.0.1:5173", "http://localhost:5173")

    def __post_init__(self):
        self.data_dir = self.data_dir.resolve()
        self.model_dir = self.model_dir.resolve()
        # Allow the configured local port without allowing arbitrary browser origins.
        port = int(os.environ.get("TRANSCRIPT_PORT", "8765"))
        self.allowed_origins += (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "transcripts.sqlite3"
