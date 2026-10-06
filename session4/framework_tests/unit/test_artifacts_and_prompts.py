import asyncio
import json

import pytest
from four_agent_workbench.domain import ConflictError, WorkbenchError
from four_agent_workbench.security import CancellationGate, Redactor, StreamRedactor, secure_path


@pytest.mark.parametrize(
    "name",
    [
        "../.env",
        "/etc/passwd",
        "C:/Windows/file",
        "x\\y",
        "NUL.py",
        "x/CON",
        "file:stream",
        "case.",
        "x//y",
        ".env",
        ".ENV",
        "_MANIFEST.JSON",
        "x/../y",
    ],
)
def test_portable_paths_cannot_escape(tmp_path, name):
    with pytest.raises(WorkbenchError):
        secure_path(tmp_path, name, artifact=True)


def test_links_are_rejected(tmp_path):
    (tmp_path / "outside").mkdir()
    try:
        (tmp_path / "link").symlink_to(tmp_path / "outside", target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation unavailable on this host")
    with pytest.raises(WorkbenchError):
        secure_path(tmp_path, "link/file.py", artifact=True)


def commit(controller, stage, gate):
    return controller.store.commit(
        agent="software_engineer",
        stage=stage,
        run_id="run-test",
        cycle_id="run-test-c1",
        invocation_id="invoke-test",
        contract="implementation_v1",
        inputs={},
        summary="fixture",
        port="implementation",
        gate=gate,
    )


def test_cancelled_publication_preserves_last_accepted(controller):
    store = controller.store
    stage = store.stage("software_engineer", "first", None)
    store.write(stage, "main.py", "print('first')\n")
    first = commit(controller, stage, CancellationGate())
    second = store.stage("software_engineer", "second", first)
    store.write(second, "main.py", "print('second')\n")
    gate = CancellationGate()
    gate.cancel()
    with pytest.raises(asyncio.CancelledError):
        commit(controller, second, gate)
    assert store.current("software_engineer") == first
    assert store.read(first, "main.py") == "print('first')\n"


def test_file_sets_and_external_mutation(controller):
    store = controller.store
    stage = store.stage("software_engineer", "files", None)
    store.write(stage, "main.py", "print('ok')\n")
    store.write(stage, "lib/util.py", "VALUE = 1\n")
    ref = commit(controller, stage, CancellationGate())
    assert set(store.manifest(ref).files) == {"main.py", "lib/util.py"}
    (store.revision_path(ref) / "main.py").write_text("external mutation")
    with pytest.raises(WorkbenchError, match="modified"):
        store.manifest(ref)


def test_secret_and_case_collision_rejected(controller):
    store = controller.store
    store.redactor = Redactor(["secret-canary-value"])
    stage = store.stage("software_engineer", "secrets", None)
    with pytest.raises(WorkbenchError, match="secret"):
        store.write(stage, "main.py", "key='secret-canary-value'")
    store.write(stage, "Main.py", "pass\n")
    with pytest.raises(WorkbenchError, match="collide"):
        store.write(stage, "main.py", "pass\n")


def test_stream_redaction_at_every_secret_split():
    secret = "sk-test-CREDENTIAL-canary"
    for boundary in range(1, len(secret)):
        stream = StreamRedactor(Redactor([secret]))
        text = stream.feed("before " + secret[:boundary])
        text += stream.feed(secret[boundary:] + " after")
        text += stream.feed("", final=True)
        assert secret not in text
        assert text == "before [REDACTED] after"


def test_prompt_save_conflicts_and_snapshot(controller):
    prompts = controller.prompts
    text, sha = prompts.load("tester")
    (controller.root / prompts.filename("tester")).write_text("external text")
    with pytest.raises(ConflictError):
        prompts.save("tester", text + " draft", sha)
    assert prompts.load("tester")[0] == "external text"
    prompts.save("tester", text + " saved", sha, overwrite=True)
    snapshot = prompts.snapshot(Redactor())
    assert snapshot["tester"]["text"].endswith("saved")
    saved = json.dumps(snapshot)
    assert "sha256" in saved


def test_crash_reconciles_pointer_publication(controller, monkeypatch):
    store = controller.store
    stage = store.stage("software_engineer", "crash", None)
    store.write(stage, "main.py", "pass\n")
    original = controller.records.journal

    def fail_finalization(revision, agent, state, data):
        if state == "accepted":
            raise OSError("simulated process failure after pointer replacement")
        return original(revision, agent, state, data)

    monkeypatch.setattr(controller.records, "journal", fail_finalization)
    with pytest.raises(OSError):
        commit(controller, stage, CancellationGate())
    current = store.current("software_engineer")
    assert current is not None
    monkeypatch.setattr(controller.records, "journal", original)
    store.recover()
    assert not controller.records.pending_publications()
    assert controller.records.get("revision", current.revision_id)
