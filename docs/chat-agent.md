# Chat Agent

Root-level `tests/` references and backend acceptance records in this document come from local private Python regression tests. That directory is not distributed with the public source and is not needed to build or run the application. `frontend/tests/` and `desktop/tests/` remain public; historical acceptance records below retain their original scope and dates.

## Questions About a Saved Topic Version (2026-10-13)

In the standalone Agent, select an application-data account, switch the source to **Topic Materials**, and select a folder and a saved version. The picker initially shows the most recent saved version and allows choosing an earlier one. A folder without saved versions must first be saved in Local Folders. You can also open a historical version in **Topic Archive** and click **Ask About This Version** to open Agent with that selection.

Each conversation is bound to that version. Changing the source, topic, or version starts a new conversation; stop an active task first. History restores the recorded selection. A question captures the saved version in the existing Agent workspace; later edits to current materials or deletion of the original folder do not rewrite the conversation's captured text. Application-data accounts can use this mode without importing their original chat databases. Ordinary chat analysis still requires its chat account.

Answers identify the topic and version and cite saved materials. Clicking a citation reads the preserved text in the answer's own task, including pagination for long materials, without querying the original chat. Original messages, manually written notes, saved or edited AI reports, original report answers, and saved citation evidence have distinct provenance. OCR and document extraction retain their saved extraction metadata; partial historical extraction remains explicitly partial. Saved reports retain the topic scope, coverage, and complete cited evidence.

Coverage counts saved items touched and saved-text characters read across the conversation. Opening a directory or seeing a search excerpt is not full reading. Complete saved-text coverage does not prove completeness of the original chats or that conclusions are correct. Raw attachment copies supply metadata unless their extracted text was also saved in the selected version; this mode does not parse files, run OCR, or infer contents from filenames. Material gaps are explicitly paginated. Saved dates and conclusions do not establish the current state of WeChat or the real world.

Implementation reuses the existing DeepAgents engine, model selection, checkpoints, streaming, account lifecycle, and evidence store. Thread creation accepts `library_scope: {folder_id, version_id}` only with `origin: "agent"`, mutually exclusive with `chat_scope`. The server records the exact version identity and name. Topic tools can list, search, and read only that captured version; internal file tools cannot bypass material pagination. Invalid or unread citations fail explicitly, without an automatic answer-repair call. Topic checking and automatic collection of new chat materials are not part of this feature.

### Saved-Topic Verification

On 2026-10-13, 399 related backend checks passed, including 32 topic integration cases and 29 saved-material tool cases. Controlled model responses drove the actual DeepAgents graph and local HTTP routes to verify saved-version isolation, citations, follow-up questions after folder deletion, report evidence retention, account separation, cancellation leases, and blocked chat/file-reading routes. Long text and 1,000 attachment gap records were verified with complete pagination. The run reported two existing warnings about the experimental LangChain v3 streaming protocol.

The full frontend suite passed 85 Node and 1,268 Vitest tests across 102 Vitest files; final production static generation passed. Coverage includes topic selection, late account hydration, history/source changes, saved-material pagination and provenance, and direct entry from a saved version. Documentation language and local links passed validation. Private logs are in `.work/topic-agent-20261013/`.

These checks used isolated synthetic materials and controlled model responses, not real model services, user chats, WeChat operations, or installers. Automatic approval review blocked the Chrome launch for visual screenshots, so browser visual acceptance was not completed. This historical record describes source acceptance on 2026-10-13; see the [v1.5.0 release validation record](release-1.5.0-validation.md) for subsequent package and frozen-backend checks.

## Retrieval Modes and Pagination (2026-10-09)

`search_messages` and the planned-retrieval branch add `retrieval_mode: keyword | hybrid`; new queries default to `keyword`. Use keyword search for exact wording, identifiers, and amounts; hybrid retrieval for vague descriptions; and supplement with `read_context` when references, premises, or state changes matter. Tool records show requested and actual modes. Overview, complete analysis, and exact statistics retain their existing flows, with search hits treated only as `search_only` evidence.

Sender conditions are applied before SQL pagination. Query identity and hybrid-retrieval tickets are saved in the current task workspace, bound to the task version and complete query identity. Hybrid pagination preserves the same candidate set; keyword pagination continues to use OFFSET. Agent pagination requires the first-page identity of the current query. Omitting or changing a hybrid query's mode explicitly requires restarting while retaining existing evidence. An unready index or inference failure produces a tool error, without false zero hits or automatic mode switching. Old branches without a mode field retain their original hybrid interpretation; old pagination whose identity cannot be confirmed becomes invalid. For derived FTS upgrades, cancellation, and performance verification, see [Local Semantic Search](local-semantic-search.md#agent-retrieval-and-derived-indexes-2026-10-09).

## Standalone AI Assistant (2026-10-07)

The sidebar's "AI assistant" opens `/agent` without selecting a contact first. Model, API address, key, reasoning capabilities, and usage reuse "Settings → AI services". Missing model configuration produces an explicit message and settings entry point. The existing assistant on the chat page's right side remains available.

- The initial input is centered and moves to the bottom after conversation begins. Answers, process display, supplemental requirements, stopping, and continuing reuse existing components and the DeepAgents path.
- `@Chats` defaults to all readable private and group chats in the current WeChat account, with search and multiple selection. Selected actual conversation IDs are the server's read boundary; the model cannot expand it.
- Each standalone AI conversation has a fixed scope. Changing scope enters a new conversation while retaining the input draft; the next send creates its thread. Old history, originals, and notes remain but do not enter the new scope. Stop an active run before changing scope.
- History lists only conversations created through the standalone entry point, with search, switching, renaming, and deletion. Opening history restores its scope. Account and chat-sidebar drafts and current conversations are saved separately.
- Clicking a citation previews the original and surrounding context, then navigates to the chat. Navigation failures show errors. Citation navigation does not automatically open the chat assistant; task notifications retain their existing behavior.

The interface reuses `/api/ai/agent`: thread creation adds `origin` (default `chat`) and `chat_scope`. The standalone entry sends `origin: "agent"` and `username: ""`; `chat_scope: null` means the entire account, and a nonempty ID list means selected chats. Empty lists, duplicate IDs, conversations outside the account, and unreadable conversations raise errors. Lists can pass `origin=agent`; omission retains existing behavior, and old records lacking the field are classified as `chat`. Standalone scopes cannot be changed through PATCH. Each run and subtask saves an independent scope snapshot rather than using tool-updated `thread.scope` as read authorization.

Existing keyword and semantic retrieval, coverage explanations, and failure explanations remain in use, without rebuilding indexes or adding dependencies. This change does not introduce Relink runtime integration, uploads, long-term memory across conversations, or an external Agent API.

### Verification for This Change

- Backend: added `tests/test_ai_standalone_agent.py` covering scopes, account isolation, historical compatibility, continue/restart/subtask inheritance, new-thread context isolation, and rejection of out-of-scope material/context/media. Related Agent, history, model execution, scope, and SSE regressions were run. Automated tests use isolated storage and controlled responses.
- Frontend: `npm test` passed (98 Node tests and 850 Vitest tests); `npm run generate` successfully generated 34 routes, including `/agent`. Existing build warnings remain and were not treated as errors in this feature.
- Browser: real components with synthetic APIs checked 1440px, 900px, 420px, light/dark themes, empty/running/completed/failed states, scope keyboard operation, history lists, and source previews. Chrome's composition-input protocol confirmed that Chinese composition confirmation does not send; this does not establish acceptance of Windows native input methods or desktop installers.
- Real model: reused the selected Xiaomi MiMo `mimo-v2.6-pro`, reading 13 messages from 2 fictional chats in an independent directory. All 3 cases passed: cross-chat rescheduling lookup, a scoped activity timeline, and a same-thread settlement follow-up. Final successful rounds made 9 API calls with 45,740 input / 2,340 output Tokens. An earlier failed acceptance adapter missing an interface caused another 2 calls with 6,470 input / 175 output Tokens, excluded from the successful-round totals. Answers and citations were checked individually; these results do not establish large-scale private-chat recall or real WeChat operation acceptance.

Reproduction script: `tools/verify_standalone_agent.py --database <existing-AI-database> --output <new-isolated-directory>`, run through the project's Python environment. It reads only current model configuration; chats come from fictional in-memory samples. Credentials are used only in memory and are not written to the acceptance directory. The script's out-of-scope text check matches complete sample text and must be combined with server-scope tests and manual answer review; it alone cannot prove that every summary fragment is free of leakage.

Synthetic browser entry: `frontend/tests/fixtures/agent-standalone.html` (existing Vite acceptance configuration). Results and screenshots are stored locally in `.work/standalone-agent/`, rather than as user data intended for publication.

## Existing Documentation and Historical Records

The current engine is described in [DeepAgents v3 Architecture and Migration](deepagents-migration.md). The following content remains as pre-migration historical documentation; old checkpoints, fixed execution stages, and old-task continuation behavior no longer apply to new runs. Earlier Mac acceptance does not mean this change has passed on Mac.

AI at the top right of the chat page enters conversation mode by default; original message summaries, automatic tasks, and attention alerts are under "Tools". The sidebar and large view share state.

## Usage

- AI conversations belong to the current WeChat account; switching WeChat chats does not switch AI conversations. All old history is listed together, retaining original answers, sources, and continuation capability.
- Questions may be consecutive. Content sent during processing is recorded as a supplement and applied before the next action. Input-method confirmation does not send; Shift+Enter inserts a newline.
- Search defaults to all readable history. People, conversations, and dates are per-turn query conditions. Follow-ups retain valid conditions, which the user may explicitly cancel. Read-scope selection and fixed/follow controls are no longer used.
- History supports creation, renaming, and deletion; it is stored separately from WeChat history and isolated between accounts.
- The Agent no longer limits tool calls, model requests, media-analysis counts, or total run duration; historical quota settings no longer apply. AI service settings retain only model services, local retrieval, and usage records.
- After manual stop or application restart, "Continue searching" retains existing evidence and usage records. Cached pages do not require another vision-model call.
- Usage is associated with each run ID. Missing upstream usage is marked unknown without cost estimates. Background compaction of long conversations also counts toward model usage.
- Models are grouped by service configuration, and native thinking levels appear only after capabilities are confirmed. New questions use the current selection; supplements during execution and continuation after stop retain the startup configuration. Enter submits a supplement during execution; the main button always stops.

## Data and Interfaces

Business data resides in existing output/ai/ai.sqlite3, using agent_thread and agent_run types. Old manual-quota settings endpoints have been removed, and historical settings do not affect execution. Stage checkpoints reside in agent_checkpoints.sqlite3, with nodes storing only run identifiers. Keys are resolved at execution and do not enter checkpoints.

`/api/ai/agent` provides threads, threads/{id}/messages, runs/{id}, runs/{id}/stop, runs/{id}/continue, and events. Messages carry client request_id values for deduplication; sending again during execution enters the same task and increments the supplemental version.

SSE uses stable event IDs and supports Last-Event-ID reconnection; the frontend also queries periodically. After complete exit, unfinished tasks are marked interrupted without automatically incurring new model costs.

Read-only tools wrap existing message search, scope pagination, message context, and media lookup, without SQL, arbitrary files, Shell, or message-sending capabilities. Backend query parameters are restricted to authorized accounts, conversations, and time scopes.

Specific-information searches reuse hybrid keyword/semantic retrieval; unavailable semantic indexes fall back to keywords with the reason displayed. Indexes may lag behind live chats. Before answering, recent messages are reread for relevant recent conversations; incomplete pagination or unavailable data sources provide coverage gaps to the model. Direct search of text in unparsed images is not guaranteed.

## Verification

- Backend tests: `tests/test_ai_agent.py`, plus existing AI service, endpoint, and provider tests.
- Frontend tests: `frontend/tests/chat-agent.test.js`, plus existing AI panel and custom-selector tests.
- All automated tests use simulated responses. Real-model acceptance should use user-specified questions and conversations and inspect the current usage record.

## Execution Conversation Flow and Protocol Fixes

Tool calls appear chronologically; expanding them shows scope, page position, and coverage. Brief progress explanations accompany key findings. A compact process record remains visible by default, without automatic collapse on completion or scrolling. Users may collapse it manually, and streaming updates preserve that choice. The process can be reloaded for each historical turn.

Decisions choose native tool calls or a JSON action protocol according to models.dev tool capabilities; unknown capabilities retain runtime probing. Explicit upstream rejection of forced tool selection switches to the JSON action protocol. Each decision allows at most three automatic corrections, with actual usage recorded for every request and no total-run quota. Message offset always denotes message position; conversation selection uses a separate conversation_offset to avoid missing later pages.

Successful tools save evidence, pending queues, and query caches; retries continue from the failed step. Responses extract Token usage before format/source validation. Failed audits retain category, field path, finish reason, and diagnostic ID without raw responses or keys. Unknown usage in old records is not backfilled.

Run endpoints add timeline, error_info, and coverage_warnings; conversation endpoints add runs summaries. Process records have id, seq, and revision, and SSE/query snapshots merge by record revision. Unavailable tool sources and incomplete pagination both show coverage explanations.

Real verification on 2026-09-08 used the same user-specified conversation and the question "What important things have been discussed recently?": the old task resumed to completion with 5 new requests and known usage of 104,579 input / 11,711 output Tokens (1 compatibility request returned no usage). A new conversation completed from the start, reading 92 messages in approximately 51 seconds with 6 requests and known usage of 21,806 input / 3,422 output Tokens (1 returned no usage). The resumed task retained its original 200 evidence messages. Reproduction and compatibility-debug requests were also associated with the original task audit. These figures exclude unknown usage that cannot be recovered from the old task, and no costs are estimated.

# Answer Evidence and Retrieval Markers (2026-09-08)

AI assistant search steps display the actual returned retrieval method: hybrid keyword/semantic search or keyword fallback with coverage explanations. Expanding a step shows this page's hit counts from both paths; the same message may count in both. Chat search results also display dual-match markers.

Each turn's "Answer evidence" separately shows found messages, original sources included in the current answer request, and valid citations actually used in the answer. Expand to see original excerpts, retrieval sources, whether they entered the request, and citation status, and to locate original messages. Sources expand in groups of 20 to bound long-list rendering.

When constructing an answer request, the backend saves the original-source list under the unified input budget and marks it complete after the call succeeds. Unfinished calls, context omissions, and old data lacking tracking records are explained explicitly. The former 90,000-character concatenation method has been replaced by the segmentation below. Source lists distinguish originals from segment summaries; they prove request inclusion, not that the model necessarily used the content. Subsequent context reads retain earlier keyword/semantic hit information.

Acceptance: 29 related Agent backend and 21 Vue regressions passed. Fictional material in `frontend/tests/fixtures/agent-evidence.html` checked real component expansion, retrieval markers, citation states, and layout, without cloud-model calls.

## Large-Scope Analysis and Current Conversation Context (2026-09-09)

The model parses subjects, conditions, and time, filling Unix seconds in the local time zone. The backend validates start/end order, cutoff time, and authorized conversations, then displays the actual query scope in the panel. Missing valid dates do not automatically become fixed three-day or seven-day ranges.

Processing modes include specific-information retrieval, overview, timeline, exact statistics, and complete extraction. Specific-information retrieval reuses hybrid keyword/semantic search and can read surrounding original context. The program traverses every page in the authorized scope for the other four modes; the model cannot answer early to skip unanalyzed pages. SQLite computes statistics after deduplication by message identity, providing daily totals, speaker rankings, and daily conversation/speaker details. Semantic extraction is always called "Analysis results".

Segments split messages and attachment text by request budget while retaining original character positions. Adjacent segments include a small overlap used only as background. Each segment extracts findings with sources; all findings are archived separately, then merged in layers, with long merge inputs grouped further. Suspected conflicts first retrieve the corresponding source context; unresolved points remain marked for verification. Complete traversal does not guarantee that semantic extraction has no omissions.

Run JSON stores only page position, current segment, merge level, and coverage state. `agent_material` stores task originals with conversation, date, and source indexes; `agent_piece` stores versioned segments, findings, merge results, long-input pieces, tool results, and old process records. Paginators reuse the same source cursor; deduplication identifiers use a disk-backed temporary SQLite database with a fixed cache. Message bodies and intermediate results are not rewritten on every state update.

Each completed segment and merge step saves a checkpoint. After stopping, restarting, or upstream failure, continuation resumes unfinished parts without model calls for completed segments. Changes to the question or scope create a new version, excluding old versions from new analysis. Narrowing scopes also removes originals that no longer qualify; all material endpoints validate account, conversation-scope revision, and task version. New AI conversations do not inherit other conversations' background, and no long-term cross-conversation memory has been added.

Earlier messages in the current conversation are organized by volume while retaining goals, subjects, dates, corrections, and citations; original user and assistant messages remain local. "The second thing" is understood from recent answers and the current goal; "What about last week?" lets the model update dates. Oversized user inputs first segment and merge requirements. Both frontend and backend enforce 1 MiB UTF-8, and this change adds no upload entry point.

Context windows, output capability, and image capability are matched by provider and model ID through the [models.dev public API](https://models.dev/api.json). Custom endpoints accept only a unique exact-name match, with the UI identifying original-provider reference parameters. Fetching upstream model lists also reads explicitly supplied context, output, modality, and capability fields and caches them locally; missing fields are supplemented by models.dev. Priority: manual settings > upstream response > models.dev. After automatic filling, context, image capability, and expanded maximum-output/tool/structured-output capabilities remain editable. Manual values are saved separately and survive refresh/reopening; "Restore automatic detection" clears them. Changing model or endpoint triggers detection again without retaining the previous model's manual values.

Model configuration shows input/output modalities, tools, reasoning, structured output, temperature, attachments, open weights, pricing, dates, and provider logos, with parameter provenance. Catalogs are cached for one day and retain the last successful data offline; unlisted models may still be configured manually.

The configurable 12,000-input total-run quota is no longer used. Each request reserves output and wrapper space within the model window. Unknown windows start with 32,768-byte segments, reduced if upstream reports insufficient context. For unknown tokenizers, UTF-8 byte upper bounds conservatively meter text; images reserve space separately rather than counting Base64 as text Tokens. Actual Tokens remain based on server usage. Explicit upstream context-limit errors or truncated segment outputs shrink unfinished segments; insufficient capacity retains progress and raises an explicit error.

`GET /runs/{id}/materials` accepts `kind=sources|findings|statistics`, `offset`, `limit`, `query`, and `version`; `GET /runs/{id}/materials/{source}` reads originals by character position. The Agent can also look up material through `search_material`, `read_material`, and `read_results`. The panel currently exposes verifiable information through answer citations and stage-note summaries. Live-source failures, snapshot limits, unfinished scopes, and unknown coverage in old records are explained separately.

Automated acceptance is in `tests/test_ai_agent_context.py` and `tests/test_ai_message_pages.py`, covering multiple conversations/pages, scope changes, original citations, long attachment text, restart resumption, over-limit reduction, 8K windows, endpoint isolation, and one hundred thousand synthetic messages. Frontend reading-summary regressions are in `frontend/tests/agent-run-reading-summary.test.js`. The one-hundred-thousand-message stream includes duplicates; tests assert peak Python allocation below 8 MiB and resident-memory growth below 32 MiB after the first batch. Storage of one hundred thousand originals is also compared with a deterministic count baseline. These figures constrain the working set for test inputs, not every attachment parser's memory usage.

Real acceptance uses `tools/verify_agent_context.py`, which reads existing model configuration and saves tasks/usage in an independent state directory. It supports `--resume-run` and consecutive `--followup`, without copying keys into acceptance records or modifying the user's automatic AI tasks.

### Acceptance Results

- 123 related backend and 54 frontend regressions passed; the final count-display adjustment also passed 14 context regressions, and the frontend production build passed.
- The currently configured model completed an overview of 228 real group-chat messages and a consecutive follow-up retaining the same date scope. The overview saved 19 segments and 50 findings; the follow-up saved 11 segments and 38 findings. List answers show the complete result count programmatically before presenting highlights, so a few summarized items are not mistaken for all results.
- Material and source metadata for the two rounds totaled 68,220 / 68,023 UTF-8 bytes (excluding media fields), both above the default 12,000-input single-request budget. Answers were generated only after all pages finished. The answers' 20 / 25 distinct citations all existed in their task material stores and fell within actual query times.
- The live source was unavailable during testing, so decrypted snapshots were used. This limitation appeared in coverage state and answers; snapshot coverage was not represented as complete live coverage.
- Debugging, failures, compatibility retries, context organization, resumption, and final-answer refreshes were audited under the original tasks: 79 total model calls, known usage of 124,456 input / 225,201 output Tokens, plus 2 calls without returned usage. These data include the repair process and are not a normal single-question cost or duration benchmark.

Real records are saved independently in `tmp/agent-context-live-20260909/verification.json`. Checks cover scope, citation existence, and usage consistency; these program checks do not prove absence of semantic-extraction omissions.

### Conversation UI and Sidebar Width

The UI currently uses AI Elements Vue source components Conversation, ConversationContent, Message, ChainOfThought, and ContextIcon, adapted to existing Python/SSE state. Messages update by ID; there is one scrolling-management entry point, and reading-position restoration uses the library's public actual scroll node. Tailwind has migrated to 4.

Source is pinned to commit `ad4818f6081a2fe6e5b7bb53cebc5a66d9a24148`; see `frontend/components/ai-elements/README.md` for provenance and license. The project retains the AI Elements Vue components actually used, imported on demand.

Layout follows Codex: user messages use compact rounded bubbles, answers use direct text layout, and execution shows tools and progress chronologically. Tool icons align left with the body; names and expanded details are indented 26px. Adjacent calls with the same tool, conversation, query, and scope merge into one row, while expanded results, duration, and reuse remain individually visible. Usage and coverage appear below answers; failure, pause, and unread material remain explicit. The compact process remains by default, and completion/scrolling does not alter the user's expansion choice.

The sidebar's left edge supports mouse/touch dragging, locally remembered width, and double-click reset to 440 pixels. With the separator focused, left/right arrows adjust width, Shift accelerates, Home/End reach boundaries, and Enter restores the default. Width adjusts to the window and other sidebars; collapsing the large view restores the original width. Tests are in `assistant-thread.test.js`, `agent-panel-resize.test.js`, and existing Agent interaction tests. Preview `tests/fixtures/agent-redesign.html` supports streaming demonstrations without model calls.

The large view shows AI conversations for the current account, with search, creation, switching, renaming, deletion, and load-failure retry; deletion requires confirmation. Drafts are saved by AI conversation. Old conversations' chat association is compatibility information only and does not limit read authorization for new questions. Asynchronous reads are isolated by account and request version.

See `docs/ai-chat-ui-validation.md` for this UI and Vue integration acceptance.

## Agent Dynamic Read Window

The new version reads according to actual remaining request budget, using half-open time intervals, fixed cutoff time/time zone, and program-generated stable continuation cursors. Oversized intervals are bisected; within a minimum one-second interval, slicing continues by stable message order and character position. Without a specified conversation, the latest N messages means N total across all filtered results.

Active originals are organized into stage notes at approximately 80%, targeting reduction below 60%; recent originals and notes target 15% / 20%, respectively. Active originals are released only after notes, sources, and progress are successfully saved in the same transaction; original evidence remains local. Save failure, pause, restart, and window shrinkage preserve the pending analysis queue. The ring and actual request share budget metering; UTF-8 bytes are conservative estimates, not actual Tokens. Cumulative model usage is audited independently.

The following 10% segmentation and offset description applies only to compatibility resumption of old-run checkpoints; new tasks use the continuous-reading protocol above.

Both scope traversal and proactive reads fill batches to each page's current capacity, retaining the next message for continuation before exceeding it. A single oversized original is stored completely locally, then split by character offset for the model; overlapping preceding context also consumes capacity. Original evidence in decision and answer requests has the same 10% material-budget limit, replacing the fixed candidate set of the latest 100 messages. The latest-N total constraint, conversation authorization, and fixed cutoff retain their original semantics.

Tool-result `next_offset` is the actual continuation position, with `null` on the final page; the model cannot arbitrarily add 50 or 100. Search positions advance by underlying hit count even when individual hits cannot be normalized, preventing repeated reads of the same page. Full budget is checked before sending. Upstream context shortages or truncated segment outputs shrink unfinished segments while retaining completed results and progress. If material capacity or even the minimum request remains insufficient, explicit errors replace silent truncation of sources or bodies.

Dynamic pagination tests are in `tests/test_ai_agent_reading_budget.py`, alongside existing Agent context, protocol, provider, and streaming-read regressions. Budgets, actual counts, and shrink reasons enter existing diagnostic logs, without new chat-body logs, settings, or database migrations.
