import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ACTIVE = ("uploading", "processing")


class BusyError(Exception):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, extension TEXT NOT NULL,
                    created_at TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
                    progress REAL, duration REAL, size INTEGER NOT NULL DEFAULT 0,
                    error TEXT, segments TEXT NOT NULL DEFAULT '[]'
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs ((1))
                    WHERE status IN ('uploading', 'processing');
                PRAGMA user_version=1;
            """)

    def create(self, name: str, extension: str) -> dict:
        job_id = uuid.uuid4().hex
        try:
            with self.connect() as db:
                db.execute("INSERT INTO jobs (id,name,extension,created_at,status,stage) VALUES (?,?,?,?,?,?)",
                           (job_id, name, extension, datetime.now(timezone.utc).isoformat(), "uploading", "uploading"))
        except sqlite3.IntegrityError as error:
            raise BusyError("A recording is already in progress. Wait for it to finish or cancel it.") from error
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["segments"] = json.loads(result["segments"])
        return result

    def list(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,name,extension,created_at,status,stage,progress,duration,size,error FROM jobs ORDER BY created_at DESC")]

    def update(self, job_id: str, active_only: bool = False, **values):
        allowed = {"status", "stage", "progress", "duration", "size", "error", "segments"}
        if not values or not values.keys() <= allowed:
            raise ValueError("Invalid job update")
        if "segments" in values:
            values["segments"] = json.dumps(values["segments"], ensure_ascii=False)
        clause = " AND status IN ('uploading','processing')" if active_only else ""
        try:
            with self.connect() as db:
                db.execute(f"UPDATE jobs SET {','.join(f'{key}=?' for key in values)} WHERE id=?{clause}", (*values.values(), job_id))
        except sqlite3.IntegrityError as error:
            raise BusyError("A recording is already in progress. Wait for it to finish or cancel it.") from error

    def delete(self, job_id: str):
        with self.connect() as db:
            db.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    def interrupt(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='interrupted',stage='interrupted',progress=NULL,error='The server stopped. Retry to start transcription again.' WHERE status IN ('uploading','processing')")
