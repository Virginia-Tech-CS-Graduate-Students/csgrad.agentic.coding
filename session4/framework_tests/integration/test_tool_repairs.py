import itertools
import json

import pytest
from four_agent_workbench.models.mock import MockModel


def file_action(path, *, tool="write_file", content="unaccepted content"):
    args = {"path": path}
    if tool == "write_file":
        args["content"] = content
    return {"tool": tool, "args": args}


def events_for(controller, run):
    return controller.records.recent_events(run_id=run["id"])


@pytest.mark.parametrize("tool", ["write_file", "delete_file"])
@pytest.mark.parametrize(
    "path",
    [
        "handoff.md",
        "HANDOFF.JSON",
        "dependencies.resolved.json",
        "evidence",
        "EVIDENCE/latest.json",
        "handoff.md/nested.txt",
        "HANDOFF.JSON/nested.txt",
        "dependencies.resolved.json/nested.txt",
    ],
)
async def test_entire_batch_is_rejected_before_tools_and_can_be_corrected(controller, tool, path):
    calls = []

    class RepairModel(MockModel):
        async def complete(self, messages, **kwargs):
            raw = await super().complete(messages, **kwargs)
            if kwargs["context"]["activity"] != "define_requirements":
                return raw
            calls.append(messages)
            if len(calls) == 1:
                response = json.loads(raw)
                # Three allowed writes precede the fourth, protected-file operation.
                response["actions"].append(file_action("must_not_execute.txt"))
                response["actions"].append(file_action(path, tool=tool))
                return json.dumps(response)
            events = controller.records.recent_events(run_id=controller.run_id)
            assert not any(e.type in {"tool.started", "artifact.committed"} for e in events)
            assert not list(controller.root.rglob("must_not_execute.txt"))
            assert messages[-2]["role"] == "assistant"
            assert path in messages[-2]["content"]
            assert messages[-1]["role"] == "user"
            assert "before any tools executed" in messages[-1]["content"]
            return raw

    controller.model_factory = lambda _: RepairModel(delay=0)
    result = await controller.run()
    assert result["status"] == "completed", result
    assert len(calls) == 2
    rejected = [e for e in events_for(controller, result) if e.type == "invocation.tool_rejected"]
    assert len(rejected) == 1
    assert rejected[0].agent_id == "system_engineer"
    assert rejected[0].payload == {
        "action_index": 3,
        "tool": tool,
        "path": path,
        "reason": "framework_owned_path",
        "attempt": 1,
    }
    invocation = controller.records.get("invocation", rejected[0].invocation_id)
    assert invocation["tool_calls"] == 2  # Only the corrected requirements and change-log writes.
    ref = controller.store.current("system_engineer")
    assert "must_not_execute.txt" not in controller.store.manifest(ref).files
    assert controller.store.read(ref, "handoff.md").startswith("# Handoff")


@pytest.mark.parametrize(
    ("activity", "role", "writes"),
    [
        ("develop", "software_engineer", 3),
        ("prepare_tests", "tester", 3),
        ("final_synthesis", "marketing", 2),
    ],
)
async def test_protected_path_preflight_prevents_other_tools_from_running(
    controller, activity, role, writes
):
    calls = 0

    class RepairModel(MockModel):
        async def complete(self, messages, **kwargs):
            nonlocal calls
            raw = await super().complete(messages, **kwargs)
            if kwargs["context"]["activity"] == activity:
                calls += 1
                if calls == 1:
                    response = json.loads(raw)
                    execution_actions = (
                        [
                            {"tool": "install_dependencies", "args": {"packages": []}},
                            {"tool": "run_check", "args": {"kind": "syntax"}},
                        ]
                        if activity == "develop"
                        else []
                    )
                    response["actions"] = [
                        *execution_actions,
                        {"tool": "list_artifacts", "args": {}},
                        *response["actions"],
                        file_action("handoff.md"),
                    ]
                    return json.dumps(response)
            return raw

    controller.model_factory = lambda _: RepairModel(delay=0)
    result = await controller.run()
    assert result["status"] == "completed", result
    assert calls == 2
    started = [
        e.payload["tool"]
        for e in events_for(controller, result)
        if e.agent_id == role
        and e.type == "tool.started"
        and e.invocation_id.endswith(
            {
                "develop": "-develop",
                "prepare_tests": "-prepare_tests",
                "final_synthesis": "-marketing",
            }[activity]
        )
    ]
    assert started == ["write_file"] * writes


async def test_repeated_protected_operations_preserve_baseline_and_name_the_failure(controller):
    assert (await controller.run())["status"] == "completed"
    baseline = controller.store.current("system_engineer")
    original_handoff = controller.store.read(baseline, "handoff.md")
    calls = []

    class BrokenModel(MockModel):
        async def complete(self, messages, **kwargs):
            calls.append(messages)
            return json.dumps({"actions": [file_action("handoff.md", tool="delete_file")]})

    controller.model_factory = lambda _: BrokenModel(delay=0)
    result = await controller.run()
    assert result["status"] == "failed"
    assert len(calls) == 2
    for fragment in ("System Engineer", "delete_file", "handoff.md", "after one tool correction"):
        assert fragment in result["error"]
    assert controller.store.current("system_engineer") == baseline
    assert controller.store.read(baseline, "handoff.md") == original_handoff
    events = events_for(controller, result)
    assert [e.payload["attempt"] for e in events if e.type == "invocation.tool_rejected"] == [1, 2]
    assert not any(
        e.type in {"tool.started", "artifact.committed", "handoff.delivered"} for e in events
    )
    assert not list((controller.root / "src/requirements/.staging").iterdir())


async def test_previously_staged_work_survives_rejected_batch(controller):
    calls = 0
    staged_before = {}

    class RepairModel(MockModel):
        async def complete(self, messages, **kwargs):
            nonlocal calls
            raw = await super().complete(messages, **kwargs)
            if kwargs["context"]["activity"] != "define_requirements":
                return raw
            calls += 1
            response = json.loads(raw)
            if calls == 1:
                response["finish"] = None
            elif calls == 2:
                stage = next((controller.root / "src/requirements/.staging").iterdir())
                staged_before.update({p.name: p.read_bytes() for p in stage.iterdir()})
                response["actions"] = [
                    file_action("change_log.md", content="must not overwrite earlier work"),
                    file_action("handoff.md"),
                ]
            else:
                stage = next((controller.root / "src/requirements/.staging").iterdir())
                assert {p.name: p.read_bytes() for p in stage.iterdir()} == staged_before
                response["actions"] = []  # Finish using the already-valid earlier turn.
            return json.dumps(response)

    controller.model_factory = lambda _: RepairModel(delay=0)
    result = await controller.run()
    assert result["status"] == "completed", result
    assert calls == 3
    ref = controller.store.current("system_engineer")
    assert controller.store.read(ref, "change_log.md").encode() == staged_before["change_log.md"]


async def test_rejected_batch_preserves_existing_test_evidence_without_rerun(controller):
    calls = 0
    earlier_evidence = None
    earlier_test = None

    class RepairModel(MockModel):
        async def complete(self, messages, **kwargs):
            nonlocal calls, earlier_evidence, earlier_test
            raw = await super().complete(messages, **kwargs)
            if kwargs["context"]["activity"] != "execute_tests":
                return raw
            calls += 1
            if calls == 1:
                return raw  # Execute pytest once to produce authoritative evidence.
            assert kwargs["context"]["has_evidence"]
            stage = next((controller.root / "src/tests/.staging").iterdir())
            if calls == 2:
                earlier_evidence = (stage / "evidence/latest.json").read_bytes()
                earlier_test = (stage / "test_greeting.py").read_bytes()
                return json.dumps(
                    {
                        "actions": [
                            file_action("test_greeting.py", content="must not invalidate evidence"),
                            file_action("evidence/latest.json"),
                        ]
                    }
                )
            assert (stage / "evidence/latest.json").read_bytes() == earlier_evidence
            assert (stage / "test_greeting.py").read_bytes() == earlier_test
            return raw  # Report against the existing evidence, with no rerun.

    controller.model_factory = lambda _: RepairModel(delay=0)
    result = await controller.run()
    assert result["status"] == "completed", result
    assert calls == 3
    runs = [
        e
        for e in events_for(controller, result)
        if e.type == "tool.started" and e.payload["tool"] == "run_pytest"
    ]
    assert len(runs) == 1
    ref = controller.store.current("tester")
    assert controller.store.read(ref, "evidence/latest.json").encode() == earlier_evidence


@pytest.mark.parametrize("errors", list(itertools.permutations(("response", "artifact", "tool"))))
async def test_each_correction_kind_has_an_independent_bounded_allowance(controller, errors):
    calls = 0

    class RepairModel(MockModel):
        async def complete(self, messages, **kwargs):
            nonlocal calls
            raw = await super().complete(messages, **kwargs)
            if kwargs["context"]["activity"] != "define_requirements":
                return raw
            calls += 1
            if calls > len(errors):
                return raw
            kind = errors[calls - 1]
            if kind == "response":
                return "invalid JSON"
            response = json.loads(raw)
            if kind == "tool":
                response["actions"].append(file_action("handoff.md"))
            else:
                response["actions"][0]["args"]["content"] = '{"goal":"Missing required fields"}'
            return json.dumps(response)

    controller.model_factory = lambda _: RepairModel(delay=0)
    result = await controller.run()
    assert result["status"] == "completed", result
    assert calls == 4
    assert [
        e.payload["kind"]
        for e in events_for(controller, result)
        if e.type == "invocation.correction_requested"
    ] == list(errors)


@pytest.mark.parametrize(
    "path",
    ["../outside.txt", "/tmp/forbidden.txt", "evidence/../outside.txt", ".env", "x\\y", None, 123],
)
async def test_unsafe_paths_do_not_get_tool_correction_even_after_protected_action(
    controller, path
):
    calls = 0

    class BrokenModel(MockModel):
        async def complete(self, messages, **kwargs):
            nonlocal calls
            calls += 1
            return json.dumps({"actions": [file_action("handoff.md"), file_action(path)]})

    controller.model_factory = lambda _: BrokenModel(delay=0)
    result = await controller.run()
    assert result["status"] == "failed"
    assert calls == 1
    assert not any(
        e.type in {"tool.started", "invocation.correction_requested", "invocation.tool_rejected"}
        for e in events_for(controller, result)
    )


async def test_secret_content_is_fatal_even_when_batch_also_requests_protected_path(controller):
    secret = "artifact-content-secret-canary"
    (controller.root / ".env").write_text(f"LLM_API_KEY={secret}\n")

    class BrokenModel(MockModel):
        async def complete(self, messages, **kwargs):
            return json.dumps(
                {
                    "actions": [
                        file_action("handoff.md"),
                        file_action("requirements.json", content={"goal": secret}),
                    ]
                }
            )

    controller.model_factory = lambda _: BrokenModel(delay=0)
    result = await controller.run()
    assert result["status"] == "failed"
    assert "configured secret" in result["error"]
    events = events_for(controller, result)
    assert not any(e.type in {"tool.started", "invocation.correction_requested"} for e in events)
    assert secret not in json.dumps([e.model_dump() for e in events])


async def test_rejected_path_is_redacted_in_diagnostics_and_correction(controller):
    secret = "path-secret-canary"
    path = "evidence/" + secret + ".json"
    (controller.root / ".env").write_text(f"LLM_API_KEY={secret}\n")
    calls = []

    class BrokenModel(MockModel):
        async def complete(self, messages, **kwargs):
            calls.append(messages)
            return json.dumps({"actions": [file_action(path)]})

    controller.model_factory = lambda _: BrokenModel(delay=0)
    result = await controller.run()
    assert result["status"] == "failed"
    assert len(calls) == 2
    events = events_for(controller, result)
    rejected = [e for e in events if e.type == "invocation.tool_rejected"]
    assert len(rejected) == 2
    assert all(e.payload["path"] == "evidence/[REDACTED].json" for e in rejected)
    assert secret not in json.dumps([calls, result, [e.model_dump() for e in events]])


@pytest.mark.parametrize("stop", [False, True])
async def test_tool_correction_obeys_model_turn_budget_and_cancellation(controller, stop):
    calls = 0
    if stop:
        emit = controller.bus.emit

        def stop_on_rejection(kind, **kwargs):
            event = emit(kind, **kwargs)
            if kind == "invocation.tool_rejected":
                controller.request_stop()
            return event

        controller.bus.emit = stop_on_rejection
    else:
        config = controller.root / "config/workbench.toml"
        config.write_text(config.read_text().replace("max_model_turns = 8", "max_model_turns = 1"))

    class BrokenModel(MockModel):
        async def complete(self, messages, **kwargs):
            nonlocal calls
            calls += 1
            return json.dumps({"actions": [file_action("handoff.md")]})

    controller.model_factory = lambda _: BrokenModel(delay=0)
    result = await controller.run()
    assert result["status"] == ("cancelled" if stop else "failed")
    assert calls == 1
    if not stop:
        assert "model-turn limit" in result["error"]
    assert not any(
        e.type in {"tool.started", "artifact.committed", "handoff.delivered"}
        for e in events_for(controller, result)
    )


async def test_unrelated_tool_failures_do_not_receive_tool_correction(controller):
    calls = 0

    class BrokenModel(MockModel):
        async def complete(self, messages, **kwargs):
            nonlocal calls
            calls += 1
            return json.dumps({"actions": [file_action("missing.txt", tool="delete_file")]})

    controller.model_factory = lambda _: BrokenModel(delay=0)
    result = await controller.run()
    assert result["status"] == "failed"
    assert "existing owned file" in result["error"]
    assert calls == 1
    assert not any(
        e.type == "invocation.correction_requested" for e in events_for(controller, result)
    )


async def test_symlink_paths_are_fatal_before_protected_path_correction(controller):
    outside = controller.root / "outside"
    outside.mkdir()
    calls = 0

    class BrokenModel(MockModel):
        async def complete(self, messages, **kwargs):
            nonlocal calls
            calls += 1
            stage = next((controller.root / "src/requirements/.staging").iterdir())
            try:
                (stage / "link").symlink_to(outside, target_is_directory=True)
            except OSError:
                pytest.skip("Symlink creation unavailable on this host")
            return json.dumps(
                {"actions": [file_action("handoff.md"), file_action("link/escape.txt")]}
            )

    controller.model_factory = lambda _: BrokenModel(delay=0)
    result = await controller.run()
    assert result["status"] == "failed"
    assert "Symlinks" in result["error"]
    assert calls == 1
    assert not list(outside.iterdir())
    assert not any(
        e.type in {"tool.started", "invocation.tool_rejected", "invocation.correction_requested"}
        for e in events_for(controller, result)
    )
