from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.config import Settings
from server.jobs import InstanceLock
from server.store import BusyError, Store
from conftest import ManualManager


def submit(client, name="lecture.mp3", content=b"test recording"):
    return client.post("/api/jobs", content=content, headers={"X-Filename": quote(name)})


def test_full_lifecycle(client, app):
    response = submit(client, "講義 and notes.MP3")
    assert response.status_code == 202
    job = response.json()
    assert job["name"] == "講義 and notes.MP3"
    assert client.get("/api/jobs").json()[0]["id"] == job["id"]
    assert submit(client).status_code == 409
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 409
    segments = [{"start": 0.5, "end": 3.75, "text": "A real transcript."}, {"start": 6.125, "end": 8, "text": "Second segment."}]
    app.state.store.update(job["id"], status="completed", stage="completed", segments=segments, duration=9)
    assert client.get(f"/api/jobs/{job['id']}").json()["segments"] == segments
    txt = client.get(f"/api/jobs/{job['id']}/download/txt")
    assert txt.text == "[00:00:00] A real transcript.\n\n[00:00:06] Second segment.\n"
    assert "filename*=UTF-8''" in txt.headers["content-disposition"]
    srt = client.get(f"/api/jobs/{job['id']}/download/srt").text
    assert "1\n00:00:00,500 --> 00:00:03,750\nA real transcript." in srt
    assert "2\n00:00:06,125 --> 00:00:08,000\nSecond segment." in srt
    assert client.get(f"/api/jobs/{job['id']}/download/original").content == b"test recording"
    directory = app.state.manager.directory(job["id"])
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 204
    assert not directory.exists()
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


@pytest.mark.parametrize("name,content,expected", [("bad.wav", b"123", 415), ("bad.mp3", b"", 400), ("bad.mp4", b"x" * 1025, 413), ("bad\x00.mp3", b"123", 400)])
def test_rejected_uploads_leave_no_data(client, app, name, content, expected):
    assert submit(client, name, content).status_code == expected
    assert client.get("/api/jobs").json() == []
    assert not list(app.state.manager.settings.data_dir.glob("recordings/*"))


def test_chunked_size_limit(client):
    response = client.post("/api/jobs", content=iter([b"a" * 600, b"b" * 600]), headers={"X-Filename": "test.mp3"})
    assert response.status_code == 413
    assert client.get("/api/jobs").json() == []


def test_cancel_retry_and_restart(client, app):
    job = submit(client).json()
    route = f"/api/jobs/{job['id']}"
    assert client.post(route + "/cancel").json()["status"] == "cancelled"
    assert client.post(route + "/retry").json()["status"] == "processing"
    assert client.post(route + "/retry").status_code == 409
    app.state.store.interrupt()
    assert client.get(route).json()["status"] == "interrupted"
    assert client.post(route + "/retry").status_code == 202


def test_browser_guards_and_unknown_routes(client):
    assert client.post("/api/jobs", headers={"X-Local-Request": ""}).status_code == 403
    assert client.post("/api/jobs", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/jobs", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/missing").status_code == 404
    assert client.get("/api/jobs/../../etc/passwd/download/original").status_code == 404
    preflight = client.options("/api/jobs", headers={"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-Local-Request,X-Filename"})
    assert preflight.status_code == 200


def test_retry_conflict_does_not_replace_other_job(client):
    first = submit(client).json()
    client.post(f"/api/jobs/{first['id']}/cancel")
    second = submit(client).json()
    assert client.post(f"/api/jobs/{first['id']}/retry").status_code == 409
    assert client.get(f"/api/jobs/{second['id']}").json()["status"] == "processing"
    assert client.get(f"/api/jobs/{first['id']}").json()["status"] == "cancelled"


def test_disk_error_cleans_up(client, app, monkeypatch):
    from pathlib import Path
    original = Path.open
    def fail(path, *args, **kwargs):
        if path.suffix == ".part":
            raise OSError("Disk is full")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail)
    assert submit(client).status_code == 507
    assert app.state.store.list() == []


def test_persistence_and_interrupted_upload_cleanup(tmp_path):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings, manager_factory=ManualManager)
    with TestClient(app, headers={"X-Local-Request": "1"}) as client:
        completed = submit(client).json()
        app.state.store.update(completed["id"], status="completed", segments=[{"start": 0, "end": 1, "text": "Saved."}])
        interrupted = submit(client).json()
        app.state.store.interrupt()
        partial = app.state.store.create("partial.mp4", ".mp4")
        directory = app.state.manager.directory(partial["id"])
        directory.mkdir()
        (directory / "upload.part").write_bytes(b"incomplete")
    with TestClient(create_app(settings, manager_factory=ManualManager)) as client:
        assert client.get(f"/api/jobs/{completed['id']}").json()["segments"][0]["text"] == "Saved."
        assert client.get(f"/api/jobs/{interrupted['id']}").json()["status"] == "interrupted"
        assert client.get(f"/api/jobs/{partial['id']}").status_code == 404
        assert not directory.exists()


def test_concurrent_submissions_have_one_winner(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    store.initialize()
    def create(_):
        try:
            return store.create("test.mp3", ".mp3")["id"]
        except BusyError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, range(4)))
    assert sum(result is not None for result in results) == 1


def test_instance_lock(tmp_path):
    first = InstanceLock(tmp_path / "lock")
    try:
        with pytest.raises((RuntimeError, OSError)):
            InstanceLock(tmp_path / "lock")
    finally:
        first.close()


def test_cancel_stops_interpreter_and_launcher(tmp_path):
    import subprocess
    import sys
    import time
    import psutil
    from server.jobs import JobManager
    settings = Settings(data_dir=tmp_path)
    store = Store(settings.db_path)
    manager = JobManager(settings, store)
    manager.startup()
    process = None
    try:
        job = store.create("test.mp3", ".mp3")
        manager.directory(job["id"]).mkdir(parents=True)
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        manager.workers[job["id"]] = process
        time.sleep(0.3)
        descendants = psutil.Process(process.pid).children(recursive=True)
        manager.cancel(job["id"])
        assert process.poll() is not None
        assert all(not child.is_running() for child in descendants)
        assert store.get(job["id"])["status"] == "cancelled"
    finally:
        manager.shutdown()
        if process and process.poll() is None:
            process.kill()
