# Troubleshooting

## Starting is disabled

Read the status text. The vision must be nonempty and explicitly saved, the four role prompts must be readable, and configuration must validate. Live mode requires endpoint, model and key settings. Mock mode requires none of those credentials.

Start controls stay disabled during a run and cancellation cleanup. A second process cannot open the same workspace. If your vision draft differs from the file, save it before starting.

## Endpoint or model errors

The service must expose compatible Chat Completions with Bearer authentication. The URL is used literally; `/api/v1` is not assumed to mean `/api/v1/chat/completions`. Verify the actual request URL against your service documentation if you receive 404 or a protocol error.

Authentication errors do not retry. Suitable transient failures retry twice within the total request deadline. Stop interrupts retry waits. Streaming incompatibility can fall back to a full response; malformed JSON receives one correction attempt before failure. Native provider structured-output support is not required.

If the provider rejects context size, lower `context_char_budget`. If responses regularly time out, adjust the explicit timeouts in the model configuration. A longer timeout does not weaken Stop's acceptance gate.

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
