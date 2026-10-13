# AI Logging and Production Troubleshooting

Runtime diagnostics for AI summaries, alerts, Agent, local retrieval, frontend requests, and desktop notifications are written to the existing application log. The default INFO level records only key milestones; normal details for individual pages, segments, and batches use DEBUG to reduce log volume during long-term use. This policy applies only to the AI diagnostic entry point; it does not change the log levels of other modules, the root logger, or file handlers.

## Log Location and Format

The location remains `logs/YYYY/MM/DD/DD_wechat_tool.log` under the current output directory, accessible through the application's "Open current log" command. When continuous execution crosses midnight, the first record switches to the current day's file; opening the log also checks the date. Switching the output directory or recreating a deleted directory requires no restart. No size limit, retention period, or automatic cleanup has been added.

```text
2026-09-09 10:00:00 | INFO | wechat_decrypt_tool.ai.diagnostics | [ai.model.call.finished] Model call: step finished; runtime diagnostics {"trace_id":"…","task_id":"…","call_id":"…","status":"success","duration_ms":1200,"usage_known":true}
```

Key business steps produce `.started` and `.finished`; exceptional exits record `.failed` or `.interrupted`. `finished` means the function has returned. Assess the business outcome using `status` and the final `terminal` / `task.state`; a function return alone does not establish task success. The event list below includes both key INFO events and detailed DEBUG events, so it does not mean every event is written to the file by default.

Default logging scope:

- Retains task creation, state changes, execution completion, cancellation and resumption, configuration changes, service startup/shutdown, the start and end of every model request (including usage and duration), Agent tool actions, notification display/click/ACK, and SSE disconnection and recovery.
- Retains every explicit WARNING/ERROR and every event with exception or failure metadata; failures in detailed steps are not downgraded. Repeated background failures retain the initial record and per-minute summaries.
- Normal message reads, attachment pages, analysis segments, inference batches, transaction wrappers, caches, skipped rules, incremental budget consumption, the first streamed character, and normal page navigation use DEBUG only.
- Committed index checkpoints, model download progress, and GPU download progress are rate-limited separately by task and execution. The first record is immediate, followed by at most one INFO record per minute; intermediate progress remains available at DEBUG. Completion and exceptions are exempt from this limit.

Use the project's existing DEBUG configuration when temporarily investigating details. DEBUG follows the same redaction rules but produces more detailed logs; restore INFO after troubleshooting. There is no automatic cleanup. Key logs accumulate with task count, model call count, and days of use, so a fixed total size is not guaranteed.

Correlation fields:

| Field | Purpose |
| --- | --- |
| `trace_id` | Connects requests, tasks, and background processing; saved to SQLite for new tasks and reused on resumption |
| `operation_id` / `parent_operation_id` | Identifies a step and its calling relationship |
| `execution_id` | A new value for each actual execution; continuing retains the task ID while distinguishing the current execution |
| `task_id` / `run_id` / `thread_id` | Summary or index task, Agent run, and conversation |
| `rule_id` / `profile_id` / `version` / `scope_revision` | Rule, configuration, supplemental requirements, and authorized-scope versions |
| `call_id` | Matches the SQLite usage record ID; each retry request is recorded separately |
| `diagnostic_id` | Connects failure records; HTTP errors also expose it through the `X-WCDA-AI-Diagnostic` response header |

Asynchronous code propagates correlation information through ContextVar, and the dedicated read thread pool explicitly copies the context. Download and inference subprocesses return only metadata through a Pipe; the parent process writes the file. Missing correlation fields in older records remain unknown, and historical usage is not invented.

## Feature and Event Index

All events below have the `ai.` prefix; started/finished/failed suffixes for complete steps are omitted.

| Feature | Main events and information |
| --- | --- |
| Configuration, default models, connection tests | `profile.saved/deleted/defaults.saved/connection.*`: changed field names, model, protocol, version, and image capability |
| Model lists | `model.catalog`, `model.catalog.page.*`: paginated result counts, HTTP status, and duration |
| Model requests | `model.call.started/acquired/first_token/finished/attempt_failed/validation/retry/compatibility`: queue, request, and first-output duration; usage, validation, and failure category for each call |
| Summary creation and fixed read scope | `summary.task.created`, `summary.conversation.read.*`, `summary.graph_read`: task ID, start/cutoff times, scope mode, conversation count, and read results |
| Segmentation, merging, citations, overviews | `summary.segment.*`, `summary.merge.*`, `summary.graph_analyze/graph_overview`: segment count, levels, cache reuse, citation validation, and failed conversations |
| Rules, alerts, cancellation, resumption | `summary.run_rule`, `summary.rule.skipped`, `summary.retry.scheduled/requested`, `summary.pause_rule`, `summary.alert.matches`, `summary.cancel_task`, `summary.execution.resume`, `summary.task.state` |
| Checkpoints | `summary.rule.checkpoint.committed`, `agent.analysis.checkpoint.committed`, `index.checkpoint.committed`: emitted only after the corresponding transaction returns successfully |
| Images and attachments | `media.resolve_media/enrich/located/cache/page.*/failed`: format, size, per-page processing and cache; fixed categories for missing, encrypted, corrupted, oversized content or an unavailable vision model |
| Agent turns, authorization, tools | `agent.submit/input.accepted/request.reused/scope.applied/input.superseded`, `agent.execute_tool`, `agent.tool.action/cache/data_source.failed`: request deduplication, version, scope, action, returned count, and cache |
| Large-scope analysis | `agent.context.*`, `agent.analysis.segment/coverage/checkpoint.*`, `agent.merge.segment.*`: background compaction, per-page reads, segment findings, merging, checks of uncertain points, and material pagination |
| Agent budgets and terminal states | `agent.budget.consumed/paused`, `agent.context.reduced`, `agent.run.terminal`, `agent.execution.failed`: consumption counts, budget reduction, stop reasons, and retained material counts |
| Model downloads and offline imports | `download.start/run/state/progress/file.*/retry/process.*/import.*`, `model.file.verify.*`: files, pinned revision, existing bytes, retry waits, verification, and load tests |
| GPU components | `gpu.component.detected/file.*/package.install.*/install/failed`, `background.failure`: support status, component file verification, installation, and detection failures |
| Inference and CPU fallback | `inference.encode/process.*/batch.finished/cpu.fallback`: actual device, runtime version, batch size, subprocess PID/exit code, and fallback reason |
| Index building/resumption | `search.build/resume/run`, `index.page.*/batch.*/commit/checkpoint.committed`: read scope, dynamic batches, reuse count, inference, and commit cursor |
| Smart search and pagination | `search.hybrid/recall.finished/ticket.hit/ticket.expired/keyword.fallback`: keyword/semantic recall counts, ticket hits, device, and degradation reason |
| Storage and background loops | `storage.transaction.failed`, `index.transaction.rolled_back`, `background.failure/recovered`: error type, system/SQLite error codes, coalesced count, and duration |
| Service startup/shutdown and recovery | `lifecycle.*`, `runtime.component`, `storage.usage.recovered`, `agent.runs.recovered`, and each service's `start/stop/purge_account` |
| Frontend requests and SSE | `client.request.failed`, `client.sse.open/disconnected/recovered/invalid`, `client.response.stale` |
| Source and notification navigation | `client.source.ready/failed`, `client.navigation.started/finished/failed` |
| Desktop notifications | `client.notification.requested/shown/failed/clicked/ack/ack_failed/duplicate/unsupported` |
| Reporting transport | `client.transport.dropped`; `[ai.diagnostic.undelivered]` in the desktop `desktop-main.log` when the backend is offline |

Key steps use INFO; compatibility switches, recoverable failures, and degradation use WARNING; terminal failures, storage exceptions, and background exceptions use ERROR. Repeated background failures are recorded immediately the first time and summarized per minute afterward, with the coalesced count included on recovery. Downloads still collect diagnostic progress internally every 5 seconds, while the INFO file records it at most once per minute; stage changes and file completion are recorded immediately. Streaming answers record request start and end by default, with first output at DEBUG only and no character-by-character logging. SSE heartbeats, unchanged polling, and component rendering are not logged.

## Troubleshooting

First obtain the task/run ID or the trace/diagnostic ID from the API response headers, then search the current and previous day's logs. For example, in PowerShell with rg installed:

```powershell
rg 'task-or-run-ID' 'D:\actual-output-directory\logs'
```

Investigate by symptom:

1. **No execution**: inspect `task.created` / `input.accepted` / `search.build`, rule configuration, and `background.failure`. Temporarily enable DEBUG to trace skipped triggers through `rule.skipped` values `active_task`, `revision_changed`, `no_new_messages`, and `below_threshold`.
2. **Still waiting**: `call.started` without finished means the model request has not completed. queue_ms/request_ms in the completion record distinguish queue and request duration; enable DEBUG and inspect acquired to distinguish them in real time. For downloads, inspect retry wait seconds; for indexing, inspect checkpoint.committed within the last minute and the task's terminal state. Detailed page/batch/commit events require DEBUG.
3. **Answer failure**: follow the trace from `agent.run.terminal`, `summary.task.state`, or `http.rejected/failed` to `call_id`. Check authentication, rate limits, timeouts, invalid JSON, citation failures, insufficient context, and compatibility switches. `usage_known=false` means unknown usage, not zero cost. Continue after fixing the issue and compare old/new `execution_id` values and cache events.
4. **Semantic degradation**: inspect `search.keyword.fallback`. `scope_not_indexed` means the index does not cover the scope; for `semantic_failed`, trace inference/process errors. `inference.cpu.fallback` means the current batch switched to CPU. Confirm recovery using subsequent checkpoint.committed events and the status of search.run.finished; batch details require DEBUG.
5. **Missing notification**: inspect backend `summary.notify` and notification events first, then follow requested → shown or failed/unsupported by task_id. ACK means only that the client processed delivery; **it does not prove successful system display or that the user saw it**. User clicks have a separate clicked event. When the backend is offline, inspect desktop-main.log; for navigation failures, inspect navigation/source events.
6. **Apparently repeated processing after resumption**: task IDs should match and execution IDs should differ. Cache events are not new model calls, and only committed checkpoints advance progress. Rollbacks, partial attachment failures, and incomplete pagination retain the cursor, so rereading on resumption is expected.

## Content Boundaries and Client Entry Point

Logs exclude keys, message bodies, questions, search terms, prompts, model answers, notification bodies, Base64, signed download URLs, request bodies, and complete tasks/timelines. Exceptions retain only type, fixed category, HTTP/system error codes, and file/function/line locations; exception messages, source lines, and local variables are excluded. DEBUG has the same restrictions. Raw SDK request logs are filtered, and free text from older modules within AI contexts is converted into safe diagnostics.

`POST /api/ai/diagnostics/events` accepts only local-machine requests, fixed events, and allowlisted fields, with at most 50 events and 64 KiB per batch; arbitrary log text is rejected. The client batches events each second, holds at most 500 events, and attempts delivery at most 4 times. The first delivery failure writes to the desktop log, and overflow is explicitly counted. Reporting uses a separate transport and stays outside the request-error reporting chain to prevent recursion.

Ordinary AI requests use the `X-WCDA-AI-Trace` request/response header while retaining the Agent's existing request_id for business deduplication. Native browser EventSource cannot set custom request headers, so SSE uses a validated random `ai_trace` identifier for correlation; the server still returns the trace response header.

Agent progress uses one persistent connection: after an event is written to SQLite, an in-process condition variable immediately wakes subscribers. SQLite also retains disconnection replay through `Last-Event-ID` / `after`. Idle connections send a comment heartbeat every 15 seconds instead of querying the event table every 100ms. On initial entry, the frontend reads one run snapshot; while connected, it merges only SSE deltas and terminal snapshots. It reads an authoritative snapshot only on automatic reconnection, an event-version gap, or SSE disconnection, using bounded fallback polling while disconnected.

## Verification and Limits

Added regressions cover concurrent context and thread pools, resumed execution IDs, HTTP local-machine/size/allowlist restrictions, sensitive sentinels in INFO/DEBUG/SDK/exception stacks, concurrent writes across midnight, repeated initialization, directory recreation, output switching, model errors and retries, streaming log counts, index rollback, frontend queues/SSE, notification stages, and desktop logging when offline.

`tools/benchmark_ai_logging.py --messages 100000 --output tmp/ai-key-logging-benchmark.json` reuses the existing synthetic SQLite message scenario to compare logging disabled and enabled. Before narrowing the policy, it recorded 576 entries and 168370 bytes; the current INFO policy records 1 committed checkpoint and 371 bytes, reducing log volume by approximately 99.8%. This indexing run took less than a minute, so no second progress record was triggered; slower tasks record progress each minute. The benchmark directly calls read and commit methods and excludes full task startup/shutdown, network model requests, real GPU inference, and desktop notifications. The 371-byte result is not an upper bound for all AI use.

Read and transaction-commit duration with logging disabled/enabled was 44.323 → 33.482 seconds. Machine load and caching have a substantial effect, so the negative increase cannot be interpreted as a logging speedup or a promise of fixed overhead. The result file is written separately to the specified path rather than the production business log. Added regressions also verify that normal details from one thousand consecutive rounds stay out of INFO, exceptions remain visible, DEBUG remains redacted, progress is rate-limited by task/execution, and other modules sharing the file handler retain their INFO/DEBUG behavior.

The contact-export redaction test in the current environment depends on an uninstalled native broker, an existing environment blocker; AI logging tests do not depend on that component. Actual network loss, GPU driver crashes, and notification-center display on each operating system still require final integration checks on the corresponding release machines.
