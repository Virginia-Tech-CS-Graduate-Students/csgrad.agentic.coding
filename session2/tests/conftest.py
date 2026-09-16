import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.config import Settings
from server.jobs import JobManager


class ManualManager(JobManager):
    """Exercise the real API/storage without launching an expensive model."""
    def start(self, job_id):
        self.store.update(job_id, status="processing", stage="transcribing", error=None, segments=[])


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(data_dir=tmp_path, max_bytes=1024), manager_factory=ManualManager)


@pytest.fixture
def client(app):
    with TestClient(app, headers={"X-Local-Request": "1"}) as client:
        yield client
