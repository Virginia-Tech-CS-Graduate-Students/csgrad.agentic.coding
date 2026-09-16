"""Client for the transcription API described in specifications.md."""

from urllib.parse import quote

import requests

FORMATS = ("mp3", "wav", "m4a", "mp4", "webm")
MAX_BYTES = 500 * 1024 * 1024


class APIError(Exception):
    """An actionable error that can be shown to the user."""


def validate_file(name, size):
    if name.rsplit(".", 1)[-1].lower() not in FORMATS:
        return "Choose an MP3, WAV, M4A, MP4, or WebM file."
    if size == 0:
        return "This file is empty. Choose a recording that contains audio."
    if size > MAX_BYTES:
        return "This file exceeds 500 MiB. Choose a smaller recording."
    return None


class TranscriptionAPI:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/") + "/api/v1/transcriptions"

    def request(self, method, path="", **kwargs):
        try:
            response = requests.request(
                method, self.base + path, timeout=(10, 120 if method == "POST" else 20), **kwargs
            )
        except requests.RequestException as exc:
            message = (
                "The upload could not be confirmed. It may already have reached the server. "
                "Check the backend before submitting again."
                if method == "POST" else
                "Could not reach the transcription service. Check the connection and retry."
            )
            raise APIError(message) from exc
        if not response.ok:
            try:
                message = response.json()["error"]["message"]
                if not isinstance(message, str):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                message = f"The service returned an error (HTTP {response.status_code}). Please retry."
            raise APIError(message)
        return response

    def job(self, response):
        try:
            data = response.json()
            assert isinstance(data, dict)
            assert isinstance(data.get("id"), str) and data["id"]
            assert data.get("status") in {"queued", "processing", "completed", "failed"}
            if data["status"] == "completed":
                assert isinstance(data.get("transcript"), dict)
                assert isinstance(data["transcript"].get("text"), str)
                assert data["transcript"]["text"].strip()
            if data["status"] == "failed":
                assert isinstance(data.get("error"), dict)
                assert isinstance(data["error"].get("message"), str)
            return data
        except (ValueError, AssertionError, TypeError) as exc:
            raise APIError("The service returned an unexpected response. Check the backend API and retry.") from exc

    def upload(self, file):
        file.seek(0)
        return self.job(self.request("POST", files={"file": (file.name, file, file.type)}))

    def status(self, job_id):
        data = self.job(self.request("GET", "/" + quote(job_id, safe="")))
        if data["id"] != job_id:
            raise APIError("The service returned a different job. Please retry the status check.")
        return data

    def download(self, job_id):
        response = self.request("GET", "/" + quote(job_id, safe="") + "/transcript")
        if response.headers.get("Content-Type", "").split(";")[0].strip() != "text/plain":
            raise APIError("The service did not return a text file. Please retry the download.")
        return response.content
