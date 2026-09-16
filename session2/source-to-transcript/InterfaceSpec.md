
# Transcription interface

```text
source-to-transcript <source file> [destination file] [--model-dir <directory>]
```

The source is an English MP3 or MP4 recording, up to 4 GiB and two hours of audio. The default destination is the complete source path with `.out` appended; `lecture.mp3` produces `lecture.mp3.out`. An explicit destination overrides that path. Output is UTF-8 text with segment start timestamps in `[HH:MM:SS]` form. A source and destination cannot refer to the same file.

The command exits with code 0 on success and 1 on a processing failure. Argument errors return 2. Progress and errors belong on stderr; stdout contains the output path. Silence produces an empty transcript and a “No speech detected” message on stderr.

On Windows, activate the project environment to use the installed console command. If application control blocks the generated executable, use the project PowerShell entry point or the equivalent module invocation:

```powershell
.\source-to-transcript.ps1 "C:\recordings\lecture.mp3"
.\.venv\Scripts\python.exe -m source_to_transcript.cli "C:\recordings\lecture.mp3" "lecture.txt"
```

Both the CLI and the web worker call `source_to_transcript.engine.transcribe`. It returns `(segments, duration_seconds)`, where each segment contains `start`, `end`, and `text`. Times are seconds in the original recording. The optional progress callback receives `(stage, percent_or_none, duration_or_none)`.

The model is downloaded during setup. Processing uses the cached `small.en` model on CPU and does not contact a transcription service.

