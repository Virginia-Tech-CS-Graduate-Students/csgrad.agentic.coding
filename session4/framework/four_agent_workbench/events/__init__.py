from __future__ import annotations

import json
import sqlite3
import threading
from collections import deque
from pathlib import Path
from typing import Any

from ..domain import Event, now
from ..security import Redactor, secure_path


class RecordStore:
    def __init__(self, root: Path):
        path = secure_path(root, ".runtime/state.sqlite3")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        with self.connection:
            self.connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(kind, id));
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
                    cycle_id TEXT, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS journal (
                    revision_id TEXT PRIMARY KEY, agent_id TEXT, state TEXT, data TEXT);
            """)

    def put(self, kind: str, identifier: str, data: dict[str, Any]):
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO records VALUES (?, ?, ?)",
                (kind, identifier, json.dumps(data)),
            )

    def get(self, kind: str, identifier: str) -> dict | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT data FROM records WHERE kind=? AND id=?", (kind, identifier)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def records(self, kind: str) -> list[dict]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT data FROM records WHERE kind=?", (kind,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def append(self, event: Event) -> Event:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "INSERT INTO events(run_id,cycle_id,data) VALUES(?,?,?)",
                (event.run_id, event.cycle_id, event.model_dump_json()),
            )
            event.sequence = cursor.lastrowid
        return event

    def recent_events(self, limit=500, run_id: str | None = None) -> list[Event]:
        query = "SELECT sequence,data FROM events"
        args: list = []
        if run_id:
            query += " WHERE run_id=?"
            args.append(run_id)
        query += " ORDER BY sequence DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            rows = self.connection.execute(query, args).fetchall()
        return [
            Event.model_validate_json(data).model_copy(update={"sequence": seq})
            for seq, data in reversed(rows)
        ]

    def journal(self, revision: str, agent: str, state: str, data: dict):
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO journal VALUES(?,?,?,?)",
                (revision, agent, state, json.dumps(data)),
            )

    def pending_publications(self):
        with self.lock:
            return self.connection.execute(
                "SELECT revision_id,agent_id,data FROM journal WHERE state='prepared'"
            ).fetchall()

    def recover_interrupted(self):
        for kind in ("run", "cycle", "invocation"):
            for record in self.records(kind):
                if record.get("status") in {"running", "queued", "stopping"}:
                    record.update(status="interrupted", finished_at=now())
                    self.put(kind, record["id"], record)

    def close(self):
        with self.lock:
            self.connection.close()


class EventBus:
    """Durable lifecycle plus coalesced, disposable previews; no Qt dependency."""

    def __init__(self, records: RecordStore, redactor: Redactor):
        self.records, self.redactor = records, redactor
        self.lock = threading.Lock()
        self.lifecycle: deque[Event] = deque()
        self.previews: dict[str, Event] = {}

    def emit(self, type: str, *, transient=False, **values) -> Event:
        event = Event(type=type, **self.redactor.clean(values))
        if not transient:
            event = self.records.append(event)
        with self.lock:
            if transient:
                key = event.invocation_id or event.agent_id
                prior = self.previews.get(key)
                if prior and event.type == "preview":
                    event.payload["text"] = (
                        prior.payload.get("text", "") + event.payload.get("text", "")
                    )[-8192:]
                self.previews[key] = event
            else:
                self.lifecycle.append(event)
                # The durable event store remains authoritative if a UI is disconnected.
                if len(self.lifecycle) > 4096:
                    self.lifecycle.popleft()
        return event

    def drain(self, limit=256) -> list[Event]:
        with self.lock:
            result = []
            while self.lifecycle and len(result) < limit:
                result.append(self.lifecycle.popleft())
            result.extend(self.previews.values())
            self.previews.clear()
        return result
