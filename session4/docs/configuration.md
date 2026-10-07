# Configuration

`config/workbench.toml` is the active configuration. `examples/workbench.example.toml` is a complete starting example. Configuration is validated before a run; it is not editable by generated agents.

## Model settings

The shared model uses `LLM_ENDPOINT_URL`, `LLM_API_KEY`, and `LLM_MODEL`. Compatible legacy names are `OPENAI_API_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_API_MODEL`. Process environment values override matching `.env` variables. Different canonical and legacy values are rejected, so aliases cannot silently select a different model or service.

The protocol is OpenAI-compatible Chat Completions with Bearer authentication. The configured URL is the entire request URL: nothing is appended. The adapter supports public text content and SSE streams and ignores private reasoning fields.

`model.json_output` defaults to `"auto"`: requests include `response_format: {"type":"json_object"}`. An explicit HTTP 400/422 rejection of that option permits one fallback to text output, with a preview notice and a durable `model.output_mode` event in Details. `"on"` requires support and fails on rejection; `"off"` omits the option. Events record the requested setting, effective request format (`json_object` or `text`), and reason; they do not certify that the provider enforced JSON. Authentication errors, unrelated request errors, and malformed successful responses never disable JSON mode. JSON and streaming negotiation are independent, each remembered for that role's client during the run, within the same overall request deadline and transient retry budget. Local validation always applies.

Each invocation allows one correction for an invalid JSON action response, one for invalid staged artifacts, and one for a protected-file tool request. These allowances are independent and all count toward the existing model-turn and time limits. Correction requests appear in Details with a `response`, `artifact`, or `tool` kind. For `requirements.json`, `write_file` accepts a JSON object as `content` and serializes it safely; JSON text remains supported. Requirements must still pass the full schema and stable-ID checks before publication.

The model context includes the action-envelope JSON Schema generated from the actual validator. Requirements-producing invocations also receive the generated requirements schema and a small valid example: `goal` and `scope` are strings; `constraints`, `assumptions`, and `unresolved_questions` are lists of strings. An object containing `in_scope` and `out_of_scope` is invalid for `scope`. Every proposed `requirements.json` write in a requirements-producing invocation is schema-checked before any actions in its batch execute. An invalid write rejects the whole batch, preserves earlier staged files, and records `invocation.artifact_rejected` with the filename, zero-based action index, field paths, and validation errors. Its replacement request includes the rejected response and expected types, using the same artifact-correction allowance as final validation. Final required-file and stable-ID checks still run before publication.

Responses may be plain JSON or a single enclosing Markdown code block with a `json` label or no label. After removing that wrapper, the complete response must pass the original action schema before any tools execute. Surrounding prose, multiple JSON objects, invalid escaping, and unsupported tools are rejected.

Response corrections include the rejected assistant response (up to 8,000 characters, with an omitted-middle marker when needed) and specific validation errors. They recommend smaller responses, preferably one file write with `finish:null`; normal batching remains available. The response and correction request stay together within the context budget; a required correction that cannot fit produces an explicit configuration error. Older history is trimmed in complete conversation groups.

Each rejection records an `invocation.response_rejected` event in Details with the attempt, syntax/schema category, bounded validation errors, original response length, and a redacted response excerpt of at most 2,000 characters. Syntax excerpts center on the decoder error when its position is available; schema excerpts retain the beginning and end. Syntax `position` refers to normalized JSON (outer whitespace and one permitted fence removed): `offset` is a zero-based character index and `line`/`column` are one-based. `excerpt_error_offset` locates that error within the redacted excerpt, accounting for redaction length changes. Truncation is marked. Configured secrets are redacted before cropping; provider reasoning fields and full raw responses are not persisted by these diagnostics. Events remain in the existing local runtime database.

Before executing any action in a response, the runner validates every proposed write/delete path. The top-level `handoff.json`, `handoff.md`, `dependencies.resolved.json`, and `evidence` paths are framework-owned, including case variants and descendants. A protected-file request rejects the whole batch before any tools execute or its `finish` is accepted. Earlier successful turns' staged files and execution evidence remain intact. Rejected actions do not count as executed tool calls.

The model gets one tool correction asking for a complete replacement batch, with handoff information supplied through `finish` instead of direct file writes. The rejected response uses the same redaction, 8,000-character excerpt, and required context-group rules as response corrections. A repeated violation fails the invocation with the role, tool, and rejected filename. Details records `invocation.tool_rejected` with a zero-based action index, tool, redacted path, reason, and attempt. Traversal, invalid paths, credential-bearing artifact content, and unrelated tool failures remain terminal errors rather than receiving tool corrections.

An explicit unsupported-streaming response triggers one non-streaming fallback in `streaming="auto"` mode. `streaming="on"` requires streaming; `"off"` disables it. Non-streaming processing displays an honest waiting status. Context-limit errors and authentication errors are actionable permanent failures; connection failures, suitable 5xx responses and rate limits have bounded cancellable retries.

Per-role overrides inherit shared values unless configured:

```toml
[agents.software_engineer.model]
model_env = "SWE_LLM_MODEL"
endpoint_env = "SWE_LLM_ENDPOINT_URL"
api_key_env = "SWE_LLM_API_KEY"
```

To override only the model, omit the endpoint/key fields. An endpoint override must explicitly name its credential variable, preventing accidental transmission of the shared key to a different service. Override credentials are included in redaction even when their variable names do not contain `KEY` or `SECRET`.

All settings and model clients are fixed for a run. Saved prompt contents are snapshotted per cycle. Prompt-path/display-name changes should be followed by restarting the UI so already-open editors are not bound to old paths.

## Workflow

`workflow.steps` defines unique activity IDs, roles, activities, contracts and `after` lists. Multiple prerequisites compile to an AND barrier. `workflow.inputs.<step>` binds aliases to `current.<step>` or `previous.<baseline>`. Previous baselines are `requirements`, `product`, `tests`, `test_results`, and `ads`; they may be absent on the first cycle.

`workflow.deliveries` specifies designated output events, independently of activity dependencies. Every delivery must map to one of the six visible relationships. The required initial activity IDs/contracts and current-cycle bindings are validated. Additional static activities must retain the ordering and role invariants; conditional expansion and arbitrary event-trigger scheduling are not supported.

## Limits

| Setting | Default |
|---|---:|
| Concurrent model requests | 2 |
| Model turns per invocation | 8 |
| Tool calls per invocation | 24 |
| Whole invocation | 900 seconds |
| Connection / first stream response / stream idle | 10 / 60 / 30 seconds |
| Non-streaming response / whole request including retries | 180 / 240 seconds |
| Provider retries | 2 |
| Response / artifact / protected-file corrections per invocation | 1 / 1 / 1 |
| Context budget | 48,000 characters |
| Python check or pytest execution | 120 seconds |
| Dependency installation | 300 seconds |
| Pytest executions per Tester invocation | 2 |
| Container CPU / memory / process count | 2 CPUs / 1 GiB / 128 |
| Artifact file / revision | 1 MiB / 20 MiB |
| Files per revision | 200 |
| Retained cycles | 10, plus pinned outputs/evidence |

Context characters are a bounded selection policy, not a provider-independent token guarantee. Reduce the budget if your service has a small context window. Required prompts are never silently truncated.

## Python dependencies and execution

Generated products and tests may declare packages in `requirements.txt` or request the dependency tool. Only package specifications are accepted. Direct URLs, VCS/local paths, pip options and replacement of the runner's pip/pytest tooling are rejected.

Wheels are installed into an isolated dependency volume during a network-enabled phase. Generated code runs afterward with networking disabled, a non-root identity, read-only input mounts and a dedicated disposable output location. Inputs are private copies with container-readable permissions; accepted artifacts retain their original permissions, independent of the host user's numeric ID. There is no host-workspace mount and no Docker socket inside containers. Resolved packages and the inspected immutable runner image ID are recorded in evidence.

The framework does not install system compilers or build source distributions. A package without an appropriate wheel produces a setup error. The default package index is PyPI; a different credential-free HTTPS index can be configured.

Execution verifies Linux behavior regardless of whether the desktop host is Windows or Linux. A passing Linux test is not evidence of native Windows behavior.
