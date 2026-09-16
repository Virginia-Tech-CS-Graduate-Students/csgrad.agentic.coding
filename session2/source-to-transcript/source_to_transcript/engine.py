"""Decode to a disk-backed waveform, then transcribe with a cached local model."""
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import av
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_DIR = ROOT / "models" / "small.en"
MAX_SECONDS = 7200
MAX_BYTES = 4 * 1024**3
SAMPLE_RATE = 16000
MODEL_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")


class MediaError(ValueError):
    pass


def model_ready(model_dir: Path) -> bool:
    return all((model_dir / name).is_file() for name in MODEL_FILES)


def transcribe(
    source: Path,
    model_dir: Path = DEFAULT_MODEL_DIR,
    progress: Callable[[str, float | None, float | None], None] = lambda *_: None,
    work_dir: Path | None = None,
    max_seconds: float = MAX_SECONDS,
) -> tuple[list[dict], float]:
    if source.suffix.lower() not in {".mp3", ".mp4"}:
        raise MediaError("Choose an MP3 audio file or an MP4 video file.")
    if not source.is_file() or source.stat().st_size == 0:
        raise MediaError("The recording is empty or missing. Choose the original file again.")
    if source.stat().st_size > MAX_BYTES:
        raise MediaError("This recording exceeds the 4 GiB limit.")
    progress("decoding", None, None)
    handle, temporary = tempfile.mkstemp(suffix=".pcm", dir=work_dir)
    samples = 0
    waveform = None
    try:
        with os.fdopen(handle, "wb") as output:
            try:
                with av.open(str(source), options={"protocol_whitelist": "file"}) as media:
                    # Reject disguised files and never follow playlist/network URLs.
                    formats = set(media.format.name.split(","))
                    expected = {"mp3"} if source.suffix.lower() == ".mp3" else {"mov", "mp4"}
                    if not formats.intersection(expected):
                        raise MediaError("The file contents do not match its MP3 or MP4 extension.")
                    if not media.streams.audio:
                        raise MediaError("This recording has no audio track.")
                    stream = media.streams.audio[0]
                    duration = float(stream.duration * stream.time_base) if stream.duration else None
                    if duration and duration > max_seconds + 0.25:
                        raise MediaError("This recording exceeds the two-hour audio limit.")
                    resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
                    last_report = -1

                    def write_frames(frames):
                        nonlocal samples, last_report
                        for frame in frames:
                            array = frame.to_ndarray().ravel()
                            samples += len(array)
                            if samples > int((max_seconds + 0.25) * SAMPLE_RATE):
                                raise MediaError("This recording exceeds the two-hour audio limit.")
                            output.write(array.tobytes())
                            elapsed = samples / SAMPLE_RATE
                            if int(elapsed) // 10 != last_report:
                                last_report = int(elapsed) // 10
                                progress("decoding", min(99, elapsed / duration * 100) if duration else None, duration)

                    for frame in media.decode(stream):
                        write_frames(resampler.resample(frame))
                    write_frames(resampler.resample(None))
            except av.FFmpegError as error:
                raise MediaError("The audio cannot be decoded. The file may be damaged or use an unsupported codec.") from error
        if samples == 0:
            raise MediaError("No decodable audio was found in this recording.")
        duration = min(samples / SAMPLE_RATE, max_seconds)
        if not model_ready(model_dir):
            raise MediaError("The English model is missing. Run setup.ps1 to download it, then retry.")
        progress("loading_model", None, duration)
        from faster_whisper import WhisperModel

        model = WhisperModel(
            str(model_dir), device="cpu", compute_type="int8",
            cpu_threads=min(8, max(1, (os.cpu_count() or 2) - 1)),
            local_files_only=True,
        )
        waveform = np.memmap(temporary, dtype=np.float32, mode="r")
        progress("transcribing", 0, duration)
        iterator, _ = model.transcribe(
            waveform, language="en", task="transcribe", beam_size=5,
            word_timestamps=True,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
        )
        segments = []
        def append_segment(start, end, text):
            start = max(segments[-1]["end"] if segments else 0, min(duration, start))
            end = min(duration, max(start, end))
            if text.strip() and round(end, 3) > round(start, 3):
                segments.append({"start": round(start, 3), "end": round(end, 3), "text": text.strip()})

        for segment in iterator:
            # VAD joins distant speech spans for inference. Word alignment lets us
            # separate them again so a subtitle cannot stretch across hours of silence.
            group = []
            for word in segment.words or []:
                if group and (word.start - group[-1].end > 2 or word.end - group[0].start > 20):
                    append_segment(group[0].start, group[-1].end, "".join(item.word for item in group))
                    group = []
                group.append(word)
            if group:
                append_segment(group[0].start, group[-1].end, "".join(item.word for item in group))
            elif not segment.words:
                append_segment(segment.start, segment.end, segment.text)
            progress("transcribing", min(99, segment.end / duration * 100), duration)
        return segments, duration
    finally:
        if waveform is not None:
            waveform._mmap.close()
        Path(temporary).unlink(missing_ok=True)
