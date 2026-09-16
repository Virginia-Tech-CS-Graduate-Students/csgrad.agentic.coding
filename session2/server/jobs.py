import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import psutil

from source_to_transcript.engine import ROOT
from .config import Settings
from .store import Store


class InstanceLock:
    """Only one service may own a data directory, including during recovery."""
    def __init__(self, path: Path):
        self.file = path.open("a+b")
        try:
            if self.file.tell() == 0:
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("Local Transcript is already using this data directory.") from None

    def close(self):
        self.file.close()


class JobManager:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.lock = threading.RLock()
        self.workers: dict[str, subprocess.Popen] = {}
        self.guard = None

    def directory(self, job_id: str) -> Path:
        # IDs come from stored UUIDs, never file names supplied by the client.
        if len(job_id) != 32 or any(char not in "0123456789abcdef" for char in job_id):
            raise ValueError("Invalid recording identifier")
        path = (self.settings.data_dir / "recordings" / job_id).resolve()
        if not path.is_relative_to(self.settings.data_dir.resolve()):
            raise ValueError("Recording path is outside the data directory")
        return path

    def startup(self):
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.guard = InstanceLock(self.settings.data_dir / ".server.lock")
        self.store.initialize()
        self.store.interrupt()
        for job in self.store.list():
            directory = self.directory(job["id"])
            for pattern in ("*.part", "*.pcm"):
                for path in directory.glob(pattern):
                    path.unlink(missing_ok=True)
            if job["status"] == "interrupted" and not (directory / ("source" + job["extension"])).exists():
                self.remove(job["id"])

    def start(self, job_id: str):
        with self.lock:
            self.store.update(job_id, status="processing", stage="decoding", progress=None, error=None, segments=[])
            log_path = self.directory(job_id) / "worker.log"
            try:
                with log_path.open("ab") as log:
                    env = {**os.environ, "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
                    process = subprocess.Popen(
                        [sys.executable, "-m", "server.worker", str(self.settings.db_path), job_id,
                         str(self.settings.model_dir), str(os.getpid())], cwd=ROOT, env=env,
                        stdout=log, stderr=log,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                self.workers[job_id] = process
                threading.Thread(target=self._watch, args=(job_id, process), daemon=True).start()
            except OSError:
                self.store.update(job_id, status="failed", stage="failed", error="The worker could not start. Check free disk space and run setup.ps1.")

    def _watch(self, job_id, process):
        process.wait()
        with self.lock:
            if self.workers.get(job_id) is not process:
                return
            self.workers.pop(job_id, None)
            self.store.update(job_id, active_only=True, status="failed", stage="failed", progress=None,
                              error="The transcription worker exited unexpectedly. Retry the recording.")
            for path in self.directory(job_id).glob("*.pcm"):
                path.unlink(missing_ok=True)

    def cancel(self, job_id: str, interrupted=False):
        with self.lock:
            # Change status before termination so a late worker update cannot revive the job.
            state = "interrupted" if interrupted else "cancelled"
            self.store.update(job_id, active_only=True, status=state, stage=state, progress=None,
                              error="The server stopped. Retry to start transcription again." if interrupted else None)
            process = self.workers.pop(job_id, None)
            if process:
                # Windows virtualenv python.exe is a launcher with a child interpreter.
                # Stopping only the launcher would leave inference running.
                try:
                    root = psutil.Process(process.pid)
                    descendants = root.children(recursive=True)
                    for child in reversed(descendants):
                        try:
                            child.kill()
                        except psutil.NoSuchProcess:
                            pass
                    try:
                        root.kill()
                    except psutil.NoSuchProcess:
                        pass
                    psutil.wait_procs(descendants, timeout=5)
                except psutil.NoSuchProcess:
                    pass
                process.wait(timeout=10)
            for path in self.directory(job_id).glob("*.pcm"):
                path.unlink(missing_ok=True)

    def remove(self, job_id: str):
        with self.lock:
            self.cancel(job_id)
            directory = self.directory(job_id)
            if directory.exists():
                shutil.rmtree(directory)
            self.store.delete(job_id)

    def shutdown(self):
        with self.lock:
            for job_id in list(self.workers):
                self.cancel(job_id, interrupted=True)
            if self.guard:
                self.guard.close()
