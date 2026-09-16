from source_to_transcript.engine import DEFAULT_MODEL_DIR, MODEL_FILES, model_ready


def main():
    if model_ready(DEFAULT_MODEL_DIR):
        print(f"English model is ready: {DEFAULT_MODEL_DIR}")
        return
    from huggingface_hub import snapshot_download

    snapshot_download(
        "Systran/faster-whisper-small.en", local_dir=DEFAULT_MODEL_DIR,
        allow_patterns=list(MODEL_FILES) + ["preprocessor_config.json"],
    )
    if not model_ready(DEFAULT_MODEL_DIR):
        raise RuntimeError("Model download is incomplete. Run setup.ps1 again.")
    print(f"English model downloaded: {DEFAULT_MODEL_DIR}")


if __name__ == "__main__":
    main()
