# Local Transcript

Local Transcript converts English MP3 and MP4 recordings into timestamped text. React provides the interface; a Python service runs the speech model on your computer. Recordings and transcripts stay in a local library until you delete them.

## Run the app

Install **Python 3.14, 64-bit**, with the Windows Python launcher, and **Node.js 22.12 or later**. Microsoft Edge is used for browser tests. A separate FFmpeg installation is not required.

From this folder, run:

```powershell
.\setup.ps1
.\launch.ps1
```

If PowerShell blocks local scripts, run each with a process-scoped policy:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\launch.ps1
```

Setup creates `.venv`, installs the locked dependencies, builds the interface, and downloads the English model into `models/small.en`. Allow about 500 MB for the model, plus dependencies and your recordings. Setup requires internet access; subsequent transcription works offline.

The launcher opens **http://127.0.0.1:8765**. Keep its terminal open while processing; press Ctrl+C to stop the server. Use `launch.ps1 -NoBrowser` to leave browser opening to you. `setup.ps1 -SkipModel` is available for interface development.

## Use the workspace

Choose or drop one recording, then select **Create transcript**. The app accepts MP3 and MP4 files up to 4 GiB with up to two hours of audio. It shows upload progress separately from audio preparation and transcription. Transcription percentages are approximate, particularly when the recording contains silence.

Open a saved recording to copy plain text or download timestamped TXT and SRT subtitles. The original recording is also available for download. **Delete recording** removes the app's copy and its transcript; it does not remove your source file outside the app.

Only one job runs at a time. Closing or refreshing the browser leaves the job running. Cancelled and interrupted jobs retain the uploaded recording for manual retry. An incomplete upload is removed. Restarting the server marks unfinished jobs as interrupted.

The first version uses `faster-whisper` with `small.en`, CPU INT8 inference, voice activity detection, and word alignment for segment timestamps. Names and technical vocabulary may need correction outside the app. Speaker labels, translation, transcript editing, and playback are outside this version.

## Project structure

| Location | Responsibility |
| --- | --- |
| `ui/` | React and TypeScript interface, Vite build, Playwright tests |
| `server/` | FastAPI service, SQLite storage, worker lifecycle |
| `source-to-transcript/` | Shared transcription engine, exports, CLI contract |
| `data/` | Local SQLite library and retained recordings; ignored by Git |
| `models/` | Cached speech model; ignored by Git |
| `tests/` | Backend, media validation, and process lifecycle tests |

The server streams uploads to disk. Each worker decodes audio into a temporary disk-backed waveform, runs the model, and writes the results to SQLite. Cancel stops the worker process tree. The server clears temporary audio after completion, cancellation, or restart. Worker errors are recorded in each recording's `worker.log` inside the data directory.

The server binds to loopback. Browser requests use restricted origins and a required custom header for writes. Recordings use generated storage identifiers; user file names are display metadata. No cloud API key is needed.

## Development and API

Run the server in one terminal and Vite in another:

```powershell
.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8765 --workers 1
cd ui
npm.cmd run dev
```

Vite proxies `/api` to the Python server. `npm.cmd run build` checks TypeScript and builds the interface that the Python server serves. Run `npm.cmd run format` to format the UI source. Use one server process per data directory; an instance lock enforces that limit.

`TRANSCRIPT_DATA_DIR` and `TRANSCRIPT_MODEL_DIR` override local storage paths. If you change the server port, set `TRANSCRIPT_PORT` to the same value to allow that browser origin.

| Method and path | Result |
| --- | --- |
| `GET /api/health` | Model readiness and file limits |
| `POST /api/jobs` | Upload and start one recording; returns 202 |
| `GET /api/jobs` | Recording history without segment bodies |
| `GET /api/jobs/{id}` | Status, progress, metadata, and segments |
| `POST /api/jobs/{id}/cancel` | Stop an active job |
| `POST /api/jobs/{id}/retry` | Restart a failed, cancelled, or interrupted job |
| `DELETE /api/jobs/{id}` | Delete an inactive recording and its transcript |
| `GET /api/jobs/{id}/download/{format}` | Download `txt`, `srt`, or `original` |

Uploads use a raw binary body, `Content-Type: application/octet-stream`, and a percent-encoded file name in `X-Filename`. POST and DELETE requests require `X-Local-Request: 1`. Errors return a JSON `detail` string. A concurrent job returns 409, an oversized file returns 413, and unsupported extensions return 415. Media decoding errors appear on the accepted job as a failed status.

The command-line interface is documented in [InterfaceSpec.md](source-to-transcript/InterfaceSpec.md). The PowerShell wrapper avoids unsigned console-launcher restrictions on managed Windows computers.

## Verify changes

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .cache\pytest
```

The tests cover uploads, storage, exports, concurrency, cancellation, recovery, and invalid media without loading the speech model. Pytest owns its temporary directory; keep `--basetemp` inside `.cache`.

For real media and browser tests, fetch the public-domain JFK speech fixture used by the [Whisper test suite](https://github.com/openai/whisper/blob/main/tests/jfk.flac):

```powershell
New-Item -ItemType Directory -Path .cache\media -Force
Invoke-WebRequest https://raw.githubusercontent.com/openai/whisper/main/tests/jfk.flac -OutFile .cache\media\jfk.flac
.\.venv\Scripts\python.exe tools\verify_transcription.py --long
cd ui
npm.cmd test
```

The verification script creates MP3, MP4, silence, and two-hour stress fixtures, runs the real model offline, and records results in `artifacts/transcription-verification.json`. The two-hour fixture contains speech near the beginning, middle, and end with silence between excerpts. It checks duration and timestamp placement; its runtime is not a benchmark for two hours of continuous speech. Playwright uses an isolated library and server on port 8766 and writes desktop/mobile screenshots to `artifacts/`.

On the development machine, the two-hour stress fixture completed in about 44 seconds with peak process-tree memory of 1.85 GiB. Short speech fixtures used about 520 MiB. Processing speed and memory use vary with the recording and hardware.
