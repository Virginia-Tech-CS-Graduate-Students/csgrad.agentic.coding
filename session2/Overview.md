
We are writing an application that will allow a user to upload a video or audio file and generate a transcript from the file.

The project will be split into two parts:

ui/

source-to-transcript/


For these two parts we have two teams that are coding.  They are going to put their code here.


## source-to-transcript/ plan

We build the backend described in [`ui/specifications.md`](ui/specifications.md). That spec is the contract: endpoint paths, JSON fields, status values, limits, and error codes all follow it. Any change gets agreed with the ui/ team first.

### Rules we follow

- No new API integrations, accounts, databases, or deployment.
- Work only in `session2/source-to-transcript/`.
- Don't change the shared interface or add dependencies without agreeing with the ui/ team.
- Plan first; no implementation code until the plan is approved.
- Build the smallest working version first, then test and report what works, what failed, and what's untested.

### Method

We transcribe locally with OpenAI's Whisper model through the `faster-whisper` Python library, and serve the API with FastAPI.

- Free, no API keys or accounts. Needs internet once to download the model, then runs offline.
- Reads audio and video directly (`.mp3`, `.wav`, `.m4a`, `.mp4`, `.webm`); no separate ffmpeg install. Tested on an Apple Silicon Mac.
- `base` model (141 MB). Switch to `small` only if accuracy is visibly poor.
- Runs on the CPU. On a Mac there is no GPU support, so we set `compute_type="int8"`.
- FastAPI handles `multipart/form-data` uploads and CORS. Python's standard library can't do multipart cleanly (`cgi` is removed in Python 3.13).

Measured on an M4 MacBook (`base`, int8): an 8-second clip takes about 0.6 s, a 5-minute clip about 63 s (roughly 5x faster than real time), using about 1.1 GB of RAM.

Options we ruled out:

| Option | Why not |
|---|---|
| mlx-whisper | Apple Silicon only |
| whisper.cpp | Too much setup for the session |
| Cloud APIs (OpenAI, AssemblyAI, Google) | Paid and need accounts, which the workshop rules avoid |

### How it works

```
browser (ui/)
  POST /api/v1/transcriptions  (multipart, field "file")
      -> validate, save to temp file, create job, return 202 {status: "queued"}
      -> job waits in a queue
  background worker (one at a time)
      -> status "processing"
      -> faster-whisper (vad_filter=True) -> text
      -> status "completed" or "failed"; temp file deleted
  GET /api/v1/transcriptions/{id}             (UI polls every 2 s)
  GET /api/v1/transcriptions/{id}/transcript  (.txt download)
```

Design choices and why:

- **One worker with a queue.** The model runs on the CPU, so two jobs at once would each run slower. The queue also makes the `queued` status real.
- **Jobs kept in memory.** The spec allows it. A server restart loses jobs; the UI handles that with `JOB_NOT_FOUND`.
- **Model loaded once at startup**, not per request.
- **Errors always in the spec's format** `{"error": {"code", "message"}}`. FastAPI's default errors (`{"detail": ...}`, and 422 for a missing field) are replaced with custom handlers. No stack traces or file paths reach the UI.
- **Transcript text:** one Whisper segment per line.
- **Size check (500 MiB)** happens after the upload arrives. Acceptable for a local prototype.

How failures map to the spec:

| What happens | Result |
|---|---|
| No `file` field, or empty file | 400 `INVALID_REQUEST` |
| File over 500 MiB | 413 `FILE_TOO_LARGE` |
| Extension not in the supported list | 415 `UNSUPPORTED_MEDIA_TYPE` |
| Corrupt file, or video with no audio track | job `failed`, `INVALID_MEDIA` |
| No speech detected (silence) | job `failed`, `NO_SPEECH` |
| Any other error while transcribing | job `failed`, `TRANSCRIPTION_FAILED` |
| Unknown job ID | 404 `JOB_NOT_FOUND` |
| Download before done / after failure | 409 `TRANSCRIPT_NOT_READY` / `TRANSCRIPTION_FAILED` |

Without the silence filter, Whisper invents text for silent audio (it returned "You" on 10 s of silence). With `vad_filter=True` it returns nothing, which we report as `NO_SPEECH`. The filter did not cut real speech in our test.

### Folder layout

```
session2/source-to-transcript/
  app.py            # FastAPI endpoints, job store, worker queue
  transcriber.py    # loads Whisper once, turns a file into text
  requirements.txt  # pinned versions
  acceptance.sh     # curl-based acceptance tests (no extra test library)
  samples/          # short test clips (< 1 MB each)
  README.md         # setup and run instructions
```

### Setup

Requires Python 3.11 or newer (we use 3.12). The latest `av` and `onnxruntime` packages that faster-whisper depends on need 3.11+; on older Python, pip quietly installs old versions.

```sh
cd session2/source-to-transcript
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

The model downloads automatically on first run (about 141 MB). Run it once before the session so slow classroom Wi-Fi doesn't matter.

### Build steps and acceptance tests

Build the smallest working version first. Steps 1–2 give the ui/ team a working API to test against after about 10 minutes.

0. **Before the session:** venv, install packages, download the model.
   - `import fastapi, faster_whisper` works; the model loads with Wi-Fi off.
1. **Server skeleton.**
   - Unknown job ID returns 404 `JOB_NOT_FOUND` in the spec's error format.
   - OPTIONS request from the UI's address returns CORS headers for GET, POST, OPTIONS.
2. **Upload and jobs, with placeholder transcription.**
   - Uploading `speech.wav` returns 202 with a UUID `id`, `status: "queued"`, `transcript` and `error` both `null`.
   - Polling the job shows `completed` with placeholder text.
   - `filename` has directory parts removed.
3. **Real transcription.**
   - `speech.mp4` and `speech.webm` complete with text containing "Virginia Tech".
   - During a 5-minute clip the job shows `processing`; a second upload shows `queued`.
   - Job responses include `Cache-Control: no-store`.
4. **Failed jobs.**
   - `silent.wav` fails with `NO_SPEECH`.
   - `noaudio.mp4` and `corrupt.mp4` fail with `INVALID_MEDIA`, with no paths or stack traces in the message.
   - The temp upload folder is empty after each job ends.
5. **Download.**
   - Completed job: 200, `text/plain; charset=utf-8`, `attachment; filename="transcript-{id}.txt"`, body exactly equal to `transcript.text`.
   - Unfinished job: 409 `TRANSCRIPT_NOT_READY`. Failed job: 409 `TRANSCRIPTION_FAILED`. Unknown job: 404.
6. **Upload errors.**
   - No `file` field: 400. `empty.wav`: 400. `notes.txt`: 415. 501 MiB file: 413.
7. **Integrate with ui/.**
   - In the UI, one audio file and one video file go through upload, preview, and download.
   - Restarting our server mid-job shows the UI's `JOB_NOT_FOUND` recovery message.
8. **Report** what works, what failed, and what's untested.

After the first version works, in order: check media before accepting the upload (422 `INVALID_MEDIA`), reject multiple files or extra form fields (400), add a worker timeout, return 503 when the queue is full.

Known untested: a file exactly at 500 MiB, non-English transcripts, and setup on Windows or Linux.

### Open questions

- ui/ team: OK to add FastAPI, uvicorn, python-multipart, and faster-whisper?
- ui/ team: confirm our folder is `session2/source-to-transcript/`.
- ui/ team: what address and port does the UI run on (for CORS)?
- ui/ team: OK that pre-upload media checks (422) come after the first version?
- Facilitator: is local speech recognition allowed for this project?
