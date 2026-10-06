# Configuration

`config/workbench.toml` is the active configuration. `examples/workbench.example.toml` is a complete starting example. Configuration is validated before a run; it is not editable by generated agents.

## Model settings

The shared model uses `LLM_ENDPOINT_URL`, `LLM_API_KEY`, and `LLM_MODEL`. Compatible legacy names are `OPENAI_API_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_API_MODEL`. Process environment values override matching `.env` variables. Different canonical and legacy values are rejected, so aliases cannot silently select a different model or service.

The protocol is OpenAI-compatible Chat Completions with Bearer authentication. The configured URL is the entire request URL: nothing is appended. The adapter supports public text content and SSE streams, ignores private reasoning fields, and requests JSON through instructions rather than assuming native structured-output support.

Each invocation allows one correction for an invalid JSON action response and, separately, one correction for invalid staged artifacts. Both count toward the existing model-turn and time limits. Correction requests appear in Details with a `response` or `artifact` kind. For `requirements.json`, `write_file` accepts a JSON object as `content` and serializes it safely; JSON text remains supported. Requirements must still pass the full schema and stable-ID checks before publication.

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
| Provider retries / schema correction | 2 / 1 |
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
