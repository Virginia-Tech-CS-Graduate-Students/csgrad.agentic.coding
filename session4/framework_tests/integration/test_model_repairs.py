import json

import pytest
from four_agent_workbench.models.mock import MockModel


def requirements_action(response):
    return next(
        action["args"]
        for action in response["actions"]
        if action["tool"] == "write_file" and action["args"]["path"] == "requirements.json"
    )


def invalid_requirements(response):
    args = requirements_action(response)
    document = json.loads(args["content"])
    document["goal"] = "A greeting\nwith an unescaped newline"
    args["content"] = json.dumps(document).replace("\\n", "\n")
    return json.dumps(response)


@pytest.mark.parametrize("errors", [("response", "artifact"), ("artifact", "response")])
@pytest.mark.parametrize("malformed_response", ["not valid action JSON", '{"actions": []}'])
async def test_response_and_artifact_errors_get_independent_repairs(
    controller, errors, malformed_response
):
    calls = []

    class RepairModel(MockModel):
        async def complete(self, messages, **kwargs):
            response = await super().complete(messages, **kwargs)
            if kwargs["context"]["activity"] != "define_requirements":
                return response
            calls.append(messages)
            if len(calls) <= len(errors):
                if errors[len(calls) - 1] == "response":
                    return malformed_response
                return invalid_requirements(json.loads(response))
            return response

    controller.model_factory = lambda _: RepairModel(delay=0)
    result = await controller.run()

    assert result["status"] == "completed", result
    assert len(calls) == 3
    assert "valid JSON" in json.dumps(calls[-1])
    assert "Correct the staged artifact contract" in json.dumps(calls[-1])
    assert len(controller.records.records("invocation")) == 5
    corrections = [
        event.payload["kind"]
        for event in controller.records.recent_events(run_id=result["id"])
        if event.type == "invocation.correction_requested"
    ]
    assert corrections == list(errors)


async def test_structured_requirements_preserve_escaping(controller):
    goal = 'Print "Hello Nathan!"\nKeep a tab:\tand a literal backslash: \\n'

    class StructuredModel(MockModel):
        async def complete(self, messages, **kwargs):
            response = json.loads(await super().complete(messages, **kwargs))
            if kwargs["context"]["activity"] == "define_requirements":
                args = requirements_action(response)
                args["content"] = json.loads(args["content"])
                args["content"]["goal"] = goal
            return json.dumps(response)

    controller.model_factory = lambda _: StructuredModel(delay=0)
    result = await controller.run()

    assert result["status"] == "completed", result
    ref = controller.store.current("system_engineer")
    document = json.loads(controller.store.read(ref, "requirements.json"))
    assert document["goal"] == goal


@pytest.mark.parametrize("initial_response_error", [False, True])
async def test_repeated_artifact_errors_remain_bounded_and_preserve_baseline(
    controller, initial_response_error
):
    assert (await controller.run())["status"] == "completed"
    baseline = controller.store.current("system_engineer")
    calls = []

    class BrokenArtifactModel(MockModel):
        async def complete(self, messages, **kwargs):
            assert kwargs["context"]["activity"] == "define_requirements"
            calls.append(messages)
            if initial_response_error and len(calls) == 1:
                return "not valid action JSON"
            return invalid_requirements(json.loads(await super().complete(messages, **kwargs)))

    controller.model_factory = lambda _: BrokenArtifactModel(delay=0)
    result = await controller.run()

    assert result["status"] == "failed"
    assert "Artifact contract still invalid" in result["error"]
    assert len(calls) == 2 + int(initial_response_error)
    assert controller.store.current("system_engineer") == baseline
    events = controller.records.recent_events(run_id=result["id"])
    assert not any(event.type in {"artifact.committed", "handoff.delivered"} for event in events)


async def test_structured_requirements_still_require_a_valid_schema(controller):
    calls = []

    class InvalidSchemaModel(MockModel):
        async def complete(self, messages, **kwargs):
            calls.append(messages)
            response = json.loads(await super().complete(messages, **kwargs))
            requirements_action(response)["content"] = {"goal": "Missing scope and requirements"}
            return json.dumps(response)

    controller.model_factory = lambda _: InvalidSchemaModel(delay=0)
    result = await controller.run()

    assert result["status"] == "failed"
    assert "Artifact contract still invalid" in result["error"]
    assert len(calls) == 2
    assert controller.store.current("system_engineer") is None
