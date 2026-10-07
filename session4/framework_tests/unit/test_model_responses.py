import json

import pytest
from four_agent_workbench.agents.responses import (
    correction_messages,
    parse_response,
    response_diagnostics,
    schema_context,
    tool_correction_messages,
)
from four_agent_workbench.domain import ModelResponse, RequirementsDocument
from four_agent_workbench.security import Redactor
from pydantic import ValidationError

VALID = json.dumps({"actions": [{"tool": "list_artifacts", "args": {}}], "finish": None})
FENCE = "`" * 3


@pytest.mark.parametrize("language", [None, "", "json", "JSON"])
def test_plain_and_single_fenced_json(language):
    raw = VALID if language is None else f"{FENCE}{language}\n{VALID}\n{FENCE}"
    response = parse_response(" \r\n" + raw + "\r\n ")
    assert response.actions[0].tool == "list_artifacts"


@pytest.mark.parametrize(
    "raw",
    [
        "Here are the actions: " + VALID,
        VALID + "\nDone.",
        VALID + "\n" + VALID,
        f"{FENCE}json\n{VALID}\n{FENCE}\n{FENCE}json\n{VALID}\n{FENCE}",
        f"{FENCE}python\n{VALID}\n{FENCE}",
        f"{FENCE}json\n{VALID}",
        '{"actions":[{"tool":"write_file","args":{"content":"one\ntwo"}}]}',
        '{"actions":[{"tool":"shell","args":{}}]}',
        '{"actions":[],"summary":"Wrong schema"}',
        '{"actions":[]}',
    ],
)
def test_wrappers_do_not_relax_validation(raw):
    with pytest.raises(ValidationError):
        parse_response(raw)


@pytest.mark.parametrize(
    ("raw", "category", "location"),
    [
        ("invalid JSON", "syntax", []),
        ('{"actions":[{"tool":"shell"}]}', "schema", ["actions", 0, "tool"]),
    ],
)
def test_diagnostics_classify_validation_without_embedding_input(raw, category, location):
    with pytest.raises(ValidationError) as error:
        parse_response(raw)
    diagnostic = response_diagnostics(raw, error.value, Redactor(), attempt=2)
    assert diagnostic["category"] == category
    assert diagnostic["attempt"] == 2
    assert diagnostic["errors"][0]["loc"] == location
    assert set(diagnostic["errors"][0]) == {"type", "loc", "msg"}
    assert diagnostic["response_excerpt"] == raw
    assert not diagnostic["response_truncated"]


def test_redaction_precedes_bounded_excerpts_and_correction_context():
    secret = "secret-credential-canary"
    raw = "prefix " + "x" * 980 + secret + "y" * 7500 + secret + " suffix"
    redactor = Redactor([secret])
    with pytest.raises(ValidationError) as error:
        parse_response(raw)
    diagnostic = response_diagnostics(raw, error.value, redactor, attempt=1)
    correction = correction_messages(raw, diagnostic, redactor)
    assert diagnostic["response_length"] == len(raw)
    assert diagnostic["response_truncated"]
    assert len(diagnostic["response_excerpt"]) == 2000
    assert len(correction[0]["content"]) == 8000
    assert diagnostic["response_excerpt"].startswith("prefix ")
    assert "suffix omitted" in diagnostic["response_excerpt"]
    assert diagnostic["excerpt_error_offset"] == 0
    assert correction[0]["content"].startswith("prefix ")
    assert correction[0]["content"].endswith(" suffix")
    assert "middle omitted" in correction[0]["content"]
    assert secret not in json.dumps([diagnostic, correction])
    assert [message["role"] for message in correction] == ["assistant", "user"]


def test_schema_errors_with_large_secret_field_names_are_bounded():
    secret = "secret-field-name"
    raw = json.dumps({secret + str(i) + "x" * 5000: "value" for i in range(20)})
    with pytest.raises(ValidationError) as error:
        parse_response(raw)
    diagnostic = response_diagnostics(raw, error.value, Redactor([secret]), attempt=1)
    assert len(diagnostic["errors"]) == 10
    assert diagnostic["errors_truncated"]
    assert secret not in json.dumps(diagnostic)
    assert all(len(item["loc"][0]) <= 120 for item in diagnostic["errors"])


def test_tool_correction_uses_bounded_redacted_assistant_context():
    secret = "tool-correction-secret"
    raw = secret + "x" * 12000 + secret
    correction = tool_correction_messages(
        raw, {"tool": "write_file", "path": "evidence/" + secret}, Redactor([secret])
    )
    assert correction[0]["role"] == "assistant"
    assert len(correction[0]["content"]) == 8000
    assert "middle omitted" in correction[0]["content"]
    assert correction[1]["role"] == "user"
    assert "entire previous action batch" in correction[1]["content"]
    assert "complete replacement" in correction[1]["content"]
    assert secret not in json.dumps(correction)


@pytest.mark.parametrize("mutation", ["missing_comma", "extra_brace"])
def test_reconstructed_nested_failures_show_the_error_not_just_the_ends(mutation):
    # Synthetic reconstruction of the observed failure shapes; the full live output was not saved.
    secret = "credential-" + "s" * 2500
    document = {
        "goal": 'Print "Hello Nathan!" — ' + secret,
        "scope": "A console program. " + "x" * 3800,
        "requirements": [
            {
                "id": "REQ-001",
                "description": "Greet Nathan.",
                "acceptance_criteria": ["stdout is correct"],
            }
        ],
        "constraints": [],
        "assumptions": ["y" * 4000],
    }
    raw = json.dumps(
        {
            "actions": [
                {"tool": "write_file", "args": {"path": "requirements.json", "content": document}}
            ],
            "finish": None,
        },
        ensure_ascii=False,
    )
    replacement = '] "constraints"' if mutation == "missing_comma" else '], }"constraints"'
    raw = raw.replace('], "constraints"', replacement, 1)
    with pytest.raises(json.JSONDecodeError) as decode:
        json.loads(raw)
    with pytest.raises(ValidationError) as error:
        parse_response(f"  ```json\n{raw}\n```\n")
    diagnostic = response_diagnostics(
        f"  ```json\n{raw}\n```\n", error.value, Redactor([secret]), attempt=1
    )
    assert diagnostic["position"] == {
        "source": "normalized_json",
        "offset": decode.value.pos,
        "line": decode.value.lineno,
        "column": decode.value.colno,
    }
    assert len(diagnostic["response_excerpt"]) == 2000
    assert '"constraints"' in diagnostic["response_excerpt"]
    assert "prefix omitted" in diagnostic["response_excerpt"]
    assert "suffix omitted" in diagnostic["response_excerpt"]
    offset = diagnostic["excerpt_error_offset"]
    assert (
        diagnostic["response_excerpt"][offset : offset + 10]
        == raw[decode.value.pos : decode.value.pos + 10]
    )
    assert secret not in json.dumps(diagnostic)
    assert diagnostic["category"] == "syntax"


@pytest.mark.parametrize("secret", ["tiny", "large-" + "x" * 4000])
def test_redaction_maps_error_offset_before_cropping(secret):
    raw = (
        '{"prefix": "'
        + secret
        + ' Bearer another-private-token", "nested": ['
        + " " * 2500
        + "BROKEN]}"
    )
    with pytest.raises(ValidationError) as error:
        parse_response(raw)
    diagnostic = response_diagnostics(raw, error.value, Redactor([secret]), attempt=1)
    offset = diagnostic["excerpt_error_offset"]
    assert diagnostic["response_excerpt"][offset:].startswith("BROKEN")
    assert secret not in json.dumps(diagnostic)
    assert "another-private-token" not in json.dumps(diagnostic)


def test_schema_context_uses_authoritative_schemas_and_valid_example():
    text = schema_context(requirements=True)
    assert json.dumps(ModelResponse.model_json_schema()) in text
    assert json.dumps(RequirementsDocument.model_json_schema()) in text
    example = text.split("Small valid requirements.json content example: ")[1]
    document = RequirementsDocument.model_validate_json(example)
    assert isinstance(document.scope, str)
    assert "requirements.json JSON Schema" not in schema_context(requirements=False)
