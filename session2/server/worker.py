import logging
import os
import sys
import threading
import time
from pathlib import Path

import psutil

from source_to_transcript.engine import MediaError, transcribe
from .store import Store


def watch_parent(parent_pid: int):
    # Exit if the server is killed rather than shut down cleanly.
    while psutil.pid_exists(parent_pid):
        time.sleep(1)
    os._exit(1)


def main():
    db_path, job_id, model_dir, parent_pid = sys.argv[1:]
    threading.Thread(target=watch_parent, args=(int(parent_pid),), daemon=True).start()
    store = Store(Path(db_path))
    job = store.get(job_id)
    directory = Path(db_path).parent / "recordings" / job_id
    source = directory / ("source" + job["extension"])
    last_report = 0.0

    def progress(stage, percent, duration):
        nonlocal last_report
        if time.monotonic() - last_report < 0.4 and percent is not None:
            return
        last_report = time.monotonic()
        store.update(job_id, active_only=True, stage=stage, progress=percent, duration=duration)

    try:
        segments, duration = transcribe(source, Path(model_dir), progress, directory)
        store.update(job_id, active_only=True, status="completed", stage="completed", progress=100,
                     segments=segments, duration=duration, error=None)
    except Exception as error:
        logging.exception("Transcription worker failed for %s", job_id)
        if isinstance(error, MediaError):
            message = str(error)
        elif isinstance(error, OSError):
            message = "Cannot read or write local files. Check free disk space and file permissions, then retry."
        else:
            message = "The transcription engine stopped. Run setup.ps1 to check the model and dependencies, then retry."
        store.update(job_id, active_only=True, status="failed", stage="failed", progress=None, error=message)


if __name__ == "__main__":
    main()
