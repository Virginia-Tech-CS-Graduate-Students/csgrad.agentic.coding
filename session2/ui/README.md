# Lecture to Transcript UI

Run from this folder in PowerShell using the existing environment:

```powershell
cd 'D:\Agentic Coding\csgrad.agentic.coding\session2\ui'
& 'D:\Miniconda3\envs\transcription\python.exe' -m streamlit run app.py
```

Open http://localhost:8501. Streamlit and requests are already installed in this environment. For another environment, install `requirements.txt` first.

The backend defaults to `http://localhost:8000`. Override it before starting:

```powershell
$env:TRANSCRIPT_API_URL = 'http://localhost:8000'
```

The backend must implement [specifications.md](specifications.md): multipart upload, job polling, and plain-text download. The UI does not perform transcription itself or substitute sample text when the backend is unavailable. Requests run on the Streamlit server, so browser CORS configuration is not required for this UI.

Supports MP3, WAV, M4A, MP4, and WebM up to 500 MiB. Status checks occur every two seconds while a job is active; connection failures pause polling and provide a retry button without resubmitting the upload. Keep the browser session open during processing. Starting another file clears local session results without deleting backend data. The backend remains responsible for validating actual media and managing retention.
