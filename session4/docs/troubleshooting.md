# Troubleshooting

## Starting is disabled

Read the status text. The vision must be nonempty and explicitly saved, the four role prompts must be readable, and configuration must validate. Live mode requires endpoint, model and key settings. Mock mode requires none of those credentials.

Start controls stay disabled during a run and cancellation cleanup. A second process cannot open the same workspace. If your vision draft differs from the file, save it before starting.

## Endpoint or model errors

The service must expose compatible Chat Completions with Bearer authentication. The URL is used literally; `/api/v1` is not assumed to mean `/api/v1/chat/completions`. Verify the actual request URL against your service documentation if you receive 404 or a protocol error.

Authentication errors do not retry. Suitable transient failures retry twice within the total request deadline. Stop interrupts retry waits. Streaming incompatibility can fall back to a full response; malformed JSON receives one correction attempt before failure. Native provider structured-output support is not required.

JSON output is requested by default through `model.json_output="auto"`. Open **Details** and inspect `model.output_mode` to see whether requests use `json_object` or text, and why. An explicit rejection of JSON mode triggers a visible text fallback; unrelated errors do not. Use `"on"` to require JSON support or `"off"` to omit the option. A successful JSON-mode request still needs local schema validation, since a provider can ignore the option or return structurally incorrect content. Offline tests verify handling; support by your configured live model must be confirmed in an actual run.

## Invalid model responses and Failed agents

When a response still fails validation after one correction, the status names the responsible role and the validation problem. Open **Details** and find `invocation.response_rejected` for that run and agent. Each event distinguishes JSON syntax from action-schema errors, includes error locations and a bounded redacted excerpt, and reports whether the excerpt was truncated. A syntax error may indicate invalid escaping; a schema error may identify an unsupported tool or an incorrect `finish` field. Historical failures created before these diagnostics were added have only the older generic error.

Syntax excerpts now show the area around the error instead of losing the middle of long responses. `position` is relative to normalized JSON after removing outer whitespace and a permitted code fence; `excerpt_error_offset` points into the redacted excerpt. Missing commas and extra braces are rejected, never guessed or partially executed. The correction asks for smaller output, preferably one file write per response.

For invalid requirements content, inspect `invocation.artifact_rejected`. For example, `path: "requirements.json"`, `loc: ["scope"]`, and `type: "string_type"` mean the scope must be text, even when an in-scope/out-of-scope object seems natural. The generated schema and correction explain the expected type. Such a write rejects the entire action batch before any tools execute and uses the existing one-artifact-correction allowance.

A single enclosing Markdown code block is accepted, but the enclosed JSON must still pass the full schema. The correction request includes the rejected response and errors; it cannot bypass the one-correction, model-turn, or time limits. If required correction context cannot fit, shorten prompts or increase `context_char_budget` within the provider's capacity.

Only the responsible agent displays **Failed**. Agents with pending work display **Blocked · Remaining work stopped**, including a Tester that prepared tests but could not execute them. Concurrent work stopped by the failure displays **Cancelled**, and completed work stays **Completed**. The overall run remains failed. A new run begins a fresh cycle using accepted outputs; it does not resume the failed invocation.

If the provider rejects context size, lower `context_char_budget`. If responses regularly time out, adjust the explicit timeouts in the model configuration. A longer timeout does not weaken Stop's acceptance gate.

## Framework-owned file errors

Agents must not write or delete `handoff.json`, `handoff.md`, `dependencies.resolved.json`, or `evidence`, including descendants and case variants. Put handoff information in `finish.summary`, `finish.requirement_ids`, and `finish.known_limitations`; the framework generates the handoff files. Execution tools produce evidence that Tester reads and interprets in `test_report.md`. System Engineer writes `requirements.json`; the framework renders `requirements.md`.

If a model requests a protected path, the entire action batch is rejected before any tools execute. Earlier successful turns' staged work and evidence are preserved. The model receives one correction opportunity to resend a complete batch without the forbidden operation. A second violation fails with the responsible role, tool, and filename. Open **Details** and inspect `invocation.tool_rejected` and the correction event with `kind: "tool"`; the action index is zero-based. This recovery does not apply to unsafe paths, credential-bearing artifacts, or unrelated tool failures.

Check customized role prompts for instructions that ask the agent to author handoff or evidence files. The shipped prompts distinguish role-owned outputs from framework-owned files and use tool paths relative to each role's output folder. Saved prompt changes apply at the next cycle; restart the app after installing runtime-code changes. Historical failures may lack the rejected filename because older versions did not record it.

## Docker or dependency errors

Verify `docker info` reports a usable Linux engine, then build `docker build -t four-agent-runner:0.1 docker`. Missing wheels, unavailable package indexes and conflicting dependencies are setup failures, not successful tests.

The dependency phase has network access; product execution does not. Products that require external network services are outside the initial execution profile. Framework-generated environment variables and credentials are not passed into containers.

Docker mounts require a local context. Workspaces containing commas are rejected for real execution. On Windows, confirm Docker Desktop can access the drive containing the workspace.

## Stop or close is waiting on cleanup

The GUI hides processing previews and revokes publication before awaiting cleanup. Requests may still be computing at the remote model provider after the local stream closes. Docker resource termination depends on the local daemon responding.

Restore Docker and select **Retry Cleanup** if necessary. The application will not claim that containers stopped or allow overlapping starts while cleanup remains uncertain. Avoid manually starting another application process in the same workspace.

After a process crash, unfinished records become interrupted. Publication journals are reconciled; incomplete staging is discarded; workspace-labelled containers and dependency volumes are removed before another real run. A new run starts at System Engineer with accepted artifacts rather than resuming model state.

## Prompt conflicts and artifact changes

Clean editors reload external changes. A dirty editor keeps its draft and requires an explicit overwrite/reload choice before replacing a changed prompt. Read/write failures do not discard drafts. Saved prompt changes apply at the next cycle; configuration changes apply at the next run.

Do not edit files in accepted revision directories. They are hashed inputs and changing them invalidates the recorded evidence. Restore a modified revision from a trusted copy or start with a separate workspace. Use vision/role prompts to request normal iterative changes.

## Tests failed versus tests could not run

Assertion failures are valid product evidence. The cycle can finish and Marketing must qualify its claims; later cycles receive the failures. Runner errors, timeouts, collection problems, no tests, and malformed evidence stop execution instead of producing a passing report. Skips remain explicit. Simulated mock results always remain unverified.

Open the Tester's current output for `test_report.md`, `evidence/latest.json`, and the exact input revision references in `handoff.json`. The Details dialog contains sanitized lifecycle and handoff events, not private model reasoning or HTTP credentials.

## Optional live smoke test

Use a disposable workspace with the small Hello World vision and your model configuration, then request one live cycle. Check requirements, the product, actual Docker test evidence and the feature brief. This is the only optional verification that contacts your service and may incur model charges. Ordinary tests use deterministic models and temporary workspaces.
