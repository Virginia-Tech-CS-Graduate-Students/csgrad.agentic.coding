import numpy as np
import av
import pytest

from source_to_transcript.engine import MediaError, transcribe
from source_to_transcript.exports import timestamp


def silent_mp3(path, seconds=1):
    with av.open(str(path), "w") as output:
        stream = output.add_stream("mp3", rate=16000)
        stream.layout = "mono"
        frame = av.AudioFrame.from_ndarray(np.zeros((1, int(seconds * 16000)), dtype=np.float32), format="fltp", layout="mono")
        frame.sample_rate = 16000
        for packet in stream.encode(frame):
            output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)


def test_corrupt_and_disguised_files(tmp_path):
    path = tmp_path / "bad.mp3"
    path.write_bytes(b"this is not audio")
    with pytest.raises(MediaError, match="decoded"):
        transcribe(path, work_dir=tmp_path)
    assert not list(tmp_path.glob("*.pcm"))
    silent_mp3(path)
    renamed = path.with_suffix(".mp4")
    path.rename(renamed)
    with pytest.raises(MediaError, match="contents"):
        transcribe(renamed, work_dir=tmp_path)


def test_duration_and_missing_model(tmp_path):
    path = tmp_path / "silence.mp3"
    silent_mp3(path, 2)
    with pytest.raises(MediaError, match="two-hour"):
        transcribe(path, model_dir=tmp_path, max_seconds=1, work_dir=tmp_path)
    with pytest.raises(MediaError, match="model is missing"):
        transcribe(path, model_dir=tmp_path, work_dir=tmp_path)
    assert not list(tmp_path.glob("*.pcm"))


def test_mp4_without_audio(tmp_path):
    path = tmp_path / "silent-video.mp4"
    with av.open(str(path), "w") as output:
        stream = output.add_stream("mpeg4", rate=24)
        stream.width = 32
        stream.height = 32
        frame = av.VideoFrame.from_ndarray(np.zeros((32, 32, 3), dtype=np.uint8), format="rgb24")
        for packet in stream.encode(frame):
            output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)
    with pytest.raises(MediaError, match="no audio track"):
        transcribe(path, model_dir=tmp_path, work_dir=tmp_path)


def test_subtitle_rounding():
    assert timestamp(59.9996, True) == "00:01:00,000"
    assert timestamp(3661.2, True) == "01:01:01,200"
