"""Trusted PID 1 wrapper. Generated Python runs only in a bounded child process."""

import json
import os
import signal
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    spec = json.loads(Path("/job.json").read_text())
    mode = spec["mode"]
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
        "PYTHONPATH": "/deps:/product",
        "PRODUCT_ROOT": "/product",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PIP_CONFIG_FILE": "/dev/null",
    }
    if mode == "install":
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--no-cache-dir",
            "--only-binary=:all:",
            "--target=/deps",
            "--index-url",
            spec["index"],
            *spec["packages"],
        ]
    elif mode == "pytest":
        command = [
            sys.executable,
            "-m",
            "pytest",
            "/tests",
            "-q",
            "--import-mode=importlib",
            "-p",
            "no:cacheprovider",
            "--junitxml=/tmp/pytest-results.xml",
        ]
    elif mode == "syntax":
        command = [
            sys.executable,
            "-c",
            "import ast,pathlib; files=list(pathlib.Path('/product').rglob('*.py')); "
            "[ast.parse(p.read_text(),filename=str(p)) for p in files]; print(f'Parsed {len(files)} Python files')",
        ]
    elif mode == "python":
        command = [sys.executable, "/product/" + spec["args"][0], *spec["args"][1:]]
    else:
        raise ValueError("Unknown execution mode")
    started = now()
    child = subprocess.Popen(
        command,
        cwd="/product",
        env=environment,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    captured = bytearray()

    def drain():
        while chunk := child.stdout.read(4096):
            captured.extend(chunk)
            if len(captured) > 131072:
                del captured[:-131072]
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()

    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    timed_out = False
    try:
        exit_code = child.wait(timeout=spec["timeout"])
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()
        exit_code = 124
    thread.join(timeout=2)
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    outcome = "passed" if exit_code == 0 else "failed"
    if mode == "pytest":
        report = Path("/tmp/pytest-results.xml")
        try:
            data = report.read_bytes()
            if len(data) > 1048576 or b"<!DOCTYPE" in data.upper():
                raise ValueError("Unsafe report")
            for case in ET.fromstring(data).iter("testcase"):
                if case.find("failure") is not None:
                    counts["failed"] += 1
                elif case.find("error") is not None:
                    counts["errors"] += 1
                elif case.find("skipped") is not None:
                    counts["skipped"] += 1
                else:
                    counts["passed"] += 1
            if exit_code not in {0, 1}:
                outcome = "no_tests" if exit_code == 5 else "execution_error"
            elif not sum(counts.values()):
                outcome = "no_tests"
            elif exit_code == 0 and (counts["failed"] or counts["errors"]):
                outcome = "execution_error"
            elif exit_code == 1 and not (counts["failed"] or counts["errors"]):
                outcome = "execution_error"
            elif counts["passed"] == 0 and counts["skipped"] == sum(counts.values()):
                outcome = "skipped"
        except (OSError, ValueError, ET.ParseError):
            outcome = "execution_error"
    if timed_out:
        outcome = "timeout"
    result = {
        "command": command,
        "started_at": started,
        "finished_at": now(),
        "exit_code": exit_code,
        "outcome": outcome,
        "counts": counts,
        "output": captured.decode("utf-8", errors="replace"),
    }
    if mode == "install":
        installed = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--path=/deps", "--format=json"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        try:
            result["packages"] = [
                f"{p['name']}=={p['version']}" for p in json.loads(installed.stdout)
            ]
        except ValueError:
            result["packages"] = []
    Path("/out/result.json").write_text(json.dumps(result))
    # Exiting PID 1 also terminates descendants that attempted to outlive their parent.


if __name__ == "__main__":
    main()
