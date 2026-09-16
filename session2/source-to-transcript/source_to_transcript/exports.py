from collections.abc import Sequence


def timestamp(seconds: float, milliseconds: bool = False) -> str:
    total = max(0, round(seconds * 1000))
    hours, remainder = divmod(total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    value = f"{hours:02}:{minutes:02}:{secs:02}"
    return f"{value},{ms:03}" if milliseconds else value


def export_text(segments: Sequence[dict]) -> str:
    return "\n\n".join(f"[{timestamp(s['start'])}] {s['text']}" for s in segments) + ("\n" if segments else "")


def export_srt(segments: Sequence[dict]) -> str:
    return "\n\n".join(
        f"{index}\n{timestamp(s['start'], True)} --> {timestamp(s['end'], True)}\n{s['text']}"
        for index, s in enumerate(segments, 1)
    ) + ("\n" if segments else "")
