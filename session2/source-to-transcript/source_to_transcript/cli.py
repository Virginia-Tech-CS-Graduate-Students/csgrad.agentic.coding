import argparse
import sys
from pathlib import Path

from .engine import DEFAULT_MODEL_DIR, transcribe
from .exports import export_text


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe an English MP3 or MP4 locally.")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, nargs="?")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    args = parser.parse_args()
    destination = args.destination or Path(str(args.source) + ".out")
    if destination.resolve() == args.source.resolve():
        parser.error("The destination must differ from the source recording.")
    try:
        segments, _ = transcribe(args.source, args.model_dir)
        destination.write_text(export_text(segments), encoding="utf-8")
        print(destination)
        if not segments:
            print("No speech detected.", file=sys.stderr)
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(f"Transcription failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
