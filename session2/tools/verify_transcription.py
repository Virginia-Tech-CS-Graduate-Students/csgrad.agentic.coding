"""Create real-media fixtures and measure offline transcription.

Fetch tests/jfk.flac from https://github.com/openai/whisper into .cache/media first.
The speech is a public-domain US presidential address. No recordings are uploaded.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import av
import numpy as np
import psutil

from source_to_transcript.engine import ROOT, transcribe

MEDIA = ROOT / ".cache" / "media"


def read_sample():
    arrays = []
    resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
    with av.open(str(MEDIA / "jfk.flac")) as source:
        for frame in source.decode(audio=0):
            arrays.extend(item.to_ndarray().ravel() for item in resampler.resample(frame))
        arrays.extend(item.to_ndarray().ravel() for item in resampler.resample(None))
    return np.concatenate(arrays)


def make_audio(path, sample, seconds=None, video=False):
    count = len(sample) if seconds is None else int(seconds * 16000)
    with av.open(str(path), "w") as output:
        audio = output.add_stream("aac" if path.suffix == ".mp4" else "mp3", rate=16000)
        audio.layout = "mono"
        if video:
            picture = output.add_stream("mpeg4", rate=1)
            picture.width, picture.height = 160, 90
        for offset in range(0, count, 16000):
            size = min(16000, count - offset)
            chunk = np.zeros(size, dtype=np.float32)
            # Long stress fixture has three speech excerpts separated by silence.
            for start in ([0, 3600 * 16000, 7180 * 16000] if seconds and seconds >= 7200 else [0]):
                lo, hi = max(offset, start), min(offset + size, start + len(sample))
                if hi > lo:
                    chunk[lo - offset:hi - offset] = sample[lo - start:hi - start]
            frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="fltp", layout="mono")
            frame.sample_rate, frame.pts = 16000, offset
            for packet in audio.encode(frame):
                output.mux(packet)
            if video:
                pixels = np.full((90, 160, 3), (68, 82, 136), dtype=np.uint8)
                image = av.VideoFrame.from_ndarray(pixels, format="rgb24")
                image.pts = offset // 16000
                for packet in picture.encode(image):
                    output.mux(packet)
        for packet in audio.encode(None):
            output.mux(packet)
        if video:
            for packet in picture.encode(None):
                output.mux(packet)


def run_one(path):
    segments, duration = transcribe(path, work_dir=MEDIA)
    text = " ".join(segment["text"] for segment in segments)
    if "silence" in path.name:
        assert not segments, segments
    else:
        assert "country" in text.lower(), text
        assert segments and all(segment["end"] > segment["start"] >= 0 for segment in segments)
        assert all(a["end"] <= b["start"] for a, b in zip(segments, segments[1:]))
    if "two-hour" in path.name:
        assert 7199 <= duration <= 7200
        assert any(3590 < segment["start"] < 3620 for segment in segments)
        assert any(segment["start"] > 7170 for segment in segments)
        assert all(segment["end"] - segment["start"] < 30 for segment in segments)
    print(json.dumps({"file": path.name, "duration_seconds": duration, "segments": segments}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--long", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--single", type=Path)
    args = parser.parse_args()
    if args.single:
        run_one(args.single)
        return
    MEDIA.mkdir(parents=True, exist_ok=True)
    sample = read_sample()
    paths = [MEDIA / "speech.mp3", MEDIA / "speech.mp4", MEDIA / "silence.mp3"]
    make_audio(paths[0], sample)
    make_audio(paths[1], sample, video=True)
    make_audio(paths[2], np.zeros(16000 * 3, dtype=np.float32))
    if args.long:
        paths.append(MEDIA / "two-hour.mp3")
        make_audio(paths[-1], sample, seconds=7200)
    if args.prepare_only:
        print("Prepared " + ", ".join(path.name for path in paths))
        return
    results = []
    for path in paths:
        start = time.monotonic()
        output_path = MEDIA / (path.name + ".result.json")
        with output_path.open("w", encoding="utf-8") as output:
            process = subprocess.Popen([sys.executable, __file__, "--single", str(path)], stdout=output,
                                       env={**os.environ, "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"})
            peak = 0
            observed = psutil.Process(process.pid)
            while process.poll() is None:
                try:
                    processes = [observed, *observed.children(recursive=True)]
                    peak = max(peak, sum(item.memory_info().rss for item in processes if item.is_running()))
                except psutil.NoSuchProcess:
                    pass
                time.sleep(0.2)
        if process.returncode:
            raise RuntimeError(f"Real transcription failed for {path.name}")
        result = json.loads(output_path.read_text(encoding="utf-8"))
        result.update(elapsed_seconds=round(time.monotonic() - start, 2), peak_memory_mib=round(peak / 1024**2, 1))
        results.append(result)
        print(json.dumps(result), flush=True)
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    (artifacts / "transcription-verification.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
