import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import ClientDisconnect

from source_to_transcript.engine import model_ready
from source_to_transcript.exports import export_srt, export_text
from .config import Settings
from .jobs import JobManager
from .store import ACTIVE, BusyError, Store


def create_app(settings: Settings | None = None, manager_factory=JobManager) -> FastAPI:
    settings = settings or Settings()
    store = Store(settings.db_path)
    manager = manager_factory(settings, store)

    @asynccontextmanager
    async def lifespan(app):
        manager.startup()
        try:
            yield
        finally:
            manager.shutdown()

    app = FastAPI(title="Local Transcript", lifespan=lifespan)
    app.state.store = store
    app.state.manager = manager
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins),
                       allow_methods=["GET", "POST", "DELETE"],
                       allow_headers=["Content-Type", "X-Filename", "X-Local-Request"])

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.url.path.startswith("/api"):
            if origin and origin not in settings.allowed_origins:
                return JSONResponse({"detail": "This browser origin is not allowed."}, status_code=403)
            if request.method in {"POST", "DELETE"} and request.headers.get("x-local-request") != "1":
                return JSONResponse({"detail": "Missing local request header."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(BusyError)
    async def busy_handler(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(OSError)
    async def disk_handler(request, error):
        logging.error("Local file operation failed: %s", error)
        return JSONResponse({"detail": "Cannot access local storage. Check free disk space and file permissions."}, status_code=507)

    def get_job(job_id: str):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "Recording not found.")
        return job

    @app.get("/api/health")
    def health():
        return {"app": "local-transcript", "model_ready": model_ready(settings.model_dir),
                "model": "small.en", "max_bytes": settings.max_bytes, "max_seconds": 7200}

    @app.get("/api/jobs")
    def list_jobs():
        return store.list()

    @app.get("/api/jobs/{job_id}")
    def detail(job_id: str):
        return get_job(job_id)

    @app.post("/api/jobs", status_code=202)
    async def upload(request: Request):
        name = unquote(request.headers.get("x-filename", "")).replace("\\", "/").split("/")[-1]
        if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
            raise HTTPException(400, "Choose a recording with a valid file name.")
        extension = Path(name).suffix.lower()
        if extension not in {".mp3", ".mp4"}:
            raise HTTPException(415, "Choose an MP3 audio file or an MP4 video file.")
        length = request.headers.get("content-length")
        if length:
            try:
                size = int(length)
            except ValueError:
                raise HTTPException(400, "Invalid upload size.") from None
            if size < 0:
                raise HTTPException(400, "Invalid upload size.")
            if size > settings.max_bytes:
                raise HTTPException(413, "This recording exceeds the 4 GiB limit.")
        job = store.create(name, extension)
        directory = manager.directory(job["id"])
        retained = False
        try:
            directory.mkdir(parents=True)
            temporary = directory / "upload.part"
            size = 0
            with temporary.open("wb") as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > settings.max_bytes:
                        raise HTTPException(413, "This recording exceeds the 4 GiB limit.")
                    current = store.get(job["id"])
                    if current is None or current["status"] != "uploading":
                        raise HTTPException(409, "Upload cancelled.")
                    # A disk write must not block cancellation or status requests.
                    await asyncio.to_thread(output.write, chunk)
            if size == 0:
                raise HTTPException(400, "The recording is empty. Choose a file containing audio.")
            with manager.lock:
                if get_job(job["id"])["status"] != "uploading":
                    raise HTTPException(409, "Upload cancelled.")
                temporary.replace(directory / ("source" + extension))
                store.update(job["id"], size=size)
                retained = True
                manager.start(job["id"])
            return get_job(job["id"])
        except ClientDisconnect:
            raise HTTPException(499, "Upload disconnected. Select the file to try again.") from None
        finally:
            if not retained:
                manager.remove(job["id"])

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        get_job(job_id)
        manager.cancel(job_id)
        return get_job(job_id)

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    def retry(job_id: str):
        with manager.lock:
            job = get_job(job_id)
            if job["status"] not in {"failed", "interrupted", "cancelled"}:
                raise HTTPException(409, "Only failed, interrupted, or cancelled recordings can be retried.")
            if not (manager.directory(job_id) / ("source" + job["extension"])).is_file():
                raise HTTPException(409, "The original recording is missing. Upload it again.")
            manager.start(job_id)
        return get_job(job_id)

    @app.delete("/api/jobs/{job_id}", status_code=204)
    def delete(job_id: str):
        job = get_job(job_id)
        if job["status"] in ACTIVE:
            raise HTTPException(409, "Cancel this recording before deleting it.")
        manager.remove(job_id)
        return Response(status_code=204)

    @app.get("/api/jobs/{job_id}/download/{format}")
    def download(job_id: str, format: str):
        job = get_job(job_id)
        if format == "original":
            path = manager.directory(job_id) / ("source" + job["extension"])
            if not path.is_file():
                raise HTTPException(404, "The original recording is missing.")
            return FileResponse(path, filename=job["name"], media_type="application/octet-stream")
        if format not in {"txt", "srt"}:
            raise HTTPException(404, "Choose a TXT, SRT, or original recording download.")
        if job["status"] != "completed":
            raise HTTPException(409, "The transcript is not ready to download.")
        from urllib.parse import quote
        filename = Path(job["name"]).stem + "." + format
        text = export_text(job["segments"]) if format == "txt" else export_srt(job["segments"])
        return Response(text, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"})

    # Keep unknown API routes as JSON 404s rather than returning the SPA shell.
    @app.api_route("/api/{path:path}", methods=["GET", "POST", "DELETE"])
    def missing_api(path: str):
        raise HTTPException(404, "API endpoint not found.")

    if settings.ui_dir.exists():
        app.mount("/", StaticFiles(directory=settings.ui_dir, html=True), name="ui")
    else:
        @app.get("/")
        def missing_ui():
            return JSONResponse({"detail": "The interface has not been built. Run setup.ps1 first."}, status_code=503)
    return app


app = create_app()
