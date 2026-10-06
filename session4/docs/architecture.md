# Architecture and execution contracts

## Runtime ownership

Qt owns the main thread. `BackgroundLoop` owns an asyncio loop in a worker thread. `RunController` permits one active run per workspace; the CLI additionally takes an OS file lock to prevent two application processes from sharing a store.

`AgentRunner` implements every live agent invocation. Markdown supplies behavioral instructions; configured activities, code-defined tool permissions, schemas and publication rules supply the execution contract. `MockModel` is intentionally a scripted fixture and does not interpret arbitrary user visions.

`StateGraph` is compiled once per run from validated TOML. Each cycle supplies a fresh state dictionary. Results merge by activity ID; identical duplicate writes are idempotent and conflicting duplicates fail.

The fixed first-version invariants are:

- Four visible roles and all six required handoff relationships.
- System Engineer is the sole entry activity.
- Development and preparation fan out from requirements.
- `add_edge(["develop", "prepare_tests"], "execute_tests")` is an explicit AND barrier.
- Marketing waits for requirements, development and final execution results.
- Additional activities must form a static DAG, with explicit ordering when reusing a role.

The list-form barrier is different from independent incoming edges. See the official [branching documentation](https://docs.langchain.com/oss/python/langgraph/use-graph-api#create-branches) and [`add_edge` API](https://reference.langchain.com/python/langgraph/graph/state/StateGraph/add_edge).

## Cycle trace

For cycle `run-…-c0001`, invocation IDs append the activity ID:

1. `…-requirements` publishes requirements and three deliveries.
2. `…-develop` and `…-prepare_tests` overlap. Development publishes two deliveries; preparation publishes a test-plan delivery to Marketing.
3. `…-execute_tests` waits for both branches, freezes the product/tests, executes pytest, and delivers results to Marketing.
4. `…-marketing` writes its final feature brief and evidence mapping.

There are seven designated deliveries over six visible edges because Tester → Marketing carries two different handoffs. Only final results make Marketing ready. Handoff IDs include the originating invocation, port and destination, so retries cannot duplicate downstream work.

The controller declares completion only after normal graph exhaustion, the full configured result set, the exact expected delivery count, and no active graph/tool branches. It increments the completed count only then. Failing product assertions are a valid completed test result; cancelled, blocked and infrastructure-failed traversals are not completed cycles.

Previous artifacts supply explicit feedback at the next boundary. Current dependencies must bind to the current cycle's outputs, including no-change results. Cycles never overlap.

## Shared data

Pydantic models in `domain.py` validate artifact references, manifests, requirements, model action envelopes, execution evidence and events. SQLite stores run, cycle, invocation, handoff and revision records. Credentials are held only by transport objects and never placed in graph state.

An invocation receives its identity, activity, master/role prompt snapshots, pinned input aliases, owned baseline, output contract and tool limits. Context includes bounded manifests and excerpts, with scoped range reads for additional content. Full tool history is not accumulated forever.

Live models return `{"actions": [...], "finish": ...}` as JSON text. Native provider tools and structured-output support are unnecessary. Partial streamed JSON is never executed. One schema correction is allowed within the invocation's total turn budget.

Tools are `list_artifacts`, `read_file`, `write_file`, `delete_file`, `install_dependencies`, `run_check`, and `run_pytest`. Each is explicitly permissioned by activity. There is no host shell or self-scheduling tool. The framework writes authoritative handoff and execution-evidence files.

## Publication and recovery

Each role folder has `.staging/`, `revisions/`, and `current.json`. The pointer contains the current complete revision and the last accepted reference for each output port. This preserves the last completed test result even if a later test-preparation invocation is the Tester's latest revision.

Inputs are verified and copied into a role-owned staging tree. Validation rejects path traversal, case collisions, symlinks/junctions, nonportable Windows names, oversized artifacts and configured secrets. Staged files must be UTF-8 text. Previous detailed evidence stays in its historical revision instead of being copied indefinitely into every new one.

Publication seals a complete directory and manifest, journals intent, and atomically replaces `current.json` under the cancellation gate. The pointer is authoritative. Startup reconciles prepared journal records against pointers and discards incomplete staging. A database failure after pointer replacement can leave a valid accepted output while the invocation/run fails; it cannot expose an incomplete file set as accepted.

Accepted artifacts are immutable to agents. Hash mismatches caused by external edits stop processing. No cross-file atomicity is claimed for ordinary loose files; revision directories and a single pointer provide that publication boundary.

Pruning keeps ten recent cycles, all current port references, and necessary direct evidence dependencies. Older historical provenance IDs may remain after their contents have been pruned.

## Cancellation

The UI calls `request_stop()` directly to revoke the shared gate, then cancels asynchronous execution. Every tool launch, publication and delivery checks that gate. Pending network streams close on cancellation. Docker resources are registered before creation and are force-removed by name; killing the Docker CLI alone would not stop a container.

Final cleanup is shielded from repeated task cancellation. New starts remain blocked through cleanup. If Docker cannot confirm cleanup, the UI exposes Retry Cleanup and leaves starts disabled.

The UI hides previews immediately. It rejects events from obsolete runs/cycles and suppresses late activity after Stop. Complete revisions accepted before the cancellation ordering point remain available. A new Start creates new IDs and begins a new cycle, rather than resuming a checkpoint.

The responsiveness targets are 100 ms for visible acknowledgement, 250 ms for cancellation dispatch, and normally two seconds for Docker termination on a healthy local machine. They are not hard real-time guarantees. Closing a request does not prove the remote service stopped computing. Containers have independent deadlines; crash recovery removes this workspace's labelled orphan resources before another real run.

## Events and presentation

Events include a schema version, monotonically increasing persisted sequence, UTC timestamp, run/cycle/agent/invocation IDs, type, status and sanitized payload. Lifecycle events are durable. Preview chunks are transient, coalesced by invocation and capped at 8 KiB. The UI drains at 10 Hz and only manipulates widgets on the Qt thread.

LangGraph runs through `astream(..., stream_mode=["updates", "custom"], version="v2")`. Custom streams carry observable sanitized activity; the application event contract remains independent of vendor model callbacks. See [LangGraph streaming](https://docs.langchain.com/oss/python/langgraph/streaming).

There is no external tracing, cloud service, browser connection, checkpoint resume, or graph-editing dashboard.
