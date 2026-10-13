# Local semantic retrieval

## Usage

Prepare retrieval in two steps under **Settings → AI services → Local retrieval**: select a downloaded model, then choose conversations and a time range. Click **Enable and start organizing** to save, enable, and build the index in one action. The feature is off by default. Opening Settings, completing a download, or asking Agent a question does not automatically scan the entire account.

- Model files are shared among local accounts; model selection, indexes, and processing records are isolated by WeChat account.
- Download a model before selecting it. **Change model** opens a secondary management dialog; selection remains a draft until the primary button builds an index for the new model. The switch occurs only on success, while the preceding index continues serving requests.
- Opening scope selection from a chat preselects that conversation; general Settings does not select everything by default. Available ranges are the last 30 days, 90 days, custom dates, and all history.
- Ordinary search adds **Keyword / Smart search**, with Keyword as the default. Smart search combines FTS5 and semantic candidates, retaining original-message location and conversation, sender, time, and message-type filters. The interface shows related-candidate counts; similarity is not accuracy.
- New Agent `search_messages` queries default to `retrieval_mode=keyword`. Use keyword for exact terms, identifiers, and amounts, and `hybrid` for vague descriptions. Tool records show requested and actual modes separately. Index scope cannot expand Agent permissions; results explain uncovered scope, freshness, and candidate truncation.
- Keyword search and local vector inference do not call cloud models. **Agent still generates answers through the configured AI service, and the corresponding chat evidence is sent to that service.** Automatic-summary and follow-up-reminder decision flows remain unchanged.

Module navigation is standardized as **Model services / Local retrieval / Agent / Usage records**, with short purpose descriptions and arrow-key switching. The main page retains only the two preparation steps and one primary button, showing unmet prerequisites beside it. When paused without configuration changes, the button shows **Continue organizing**. Running tasks show actual stages, counts, and elapsed time; completion explains where to use Smart search. GPU devices and components are managed centrally in **Settings → Local acceleration**, with a shortcut in Local retrieval. Automatic updates, disabling, and cleanup remain in advanced settings. CPU works without component installation. AMD DirectML, per-model verification, and actual runtime state are described in [Local acceleration](local-acceleration.md).

Model cards provide download, pause, resume, retry, offline import, and deletion. Disabling the feature retains models/indexes and stops background tasks; clearing an index does not delete WeChat messages. Models referenced by account configuration or existing indexes cannot be deleted.

## Agent retrieval and derived indexes (2026-10-09)

The primary tool and retrieval branches inside plans pass resolved sender conditions into SQL, filtering before pagination. Keyword queries neither open the semantic service nor encode queries or calculate vector distances. Overviews, complete analysis, and exact statistics retain the existing read/statistics paths. Search results are always marked `search_only`; Agent uses `read_context` to check original text when it needs to confirm references, premises, and state changes.

The existing semantic-index database now includes derived FTS5. After Python `.lower()`, body text is encoded character by character as ASCII tokens, retaining punctuation, whitespace, and emoji positions. Queries reuse the existing rule of at most eight terms, retrieve through phrase AND, then recheck with the original literal rules. Conversation, time, sender, and type filters plus rechecks finish before the result limit is applied. Hybrid queries that base FTS cannot express, such as quote-only input, explicitly mark that branch unsupported while derived FTS and semantic paths continue executing.

Migration batches use the existing per-account task and thread mechanisms, prioritizing the current generation and then generation/source order, with atomic checkpoints per batch. Message updates retain rowid. Adds, edits, trimming, and cleanup maintain derived FTS together; existing vectors remain and BGE is not called again. Status field `literal_index` shows coverage and migration state. Hybrid requests error explicitly until backfill completes; paused or failed migration is not secretly retried by background organization. Chunks gain a `(generation, username, id)` index while preserving full distance calculation, message deduplication, and equal-distance ordering.

Agent uses `strict_paging=true` and saves hybrid-search tickets in the existing workspace by task version, scope, query, mode, conversation, and filters. Subsequent pages reuse the same candidates without reencoding or accessing base FTS again. Expired tickets, process restarts, missing tickets, or changed conditions require an explicit restart from page one; existing evidence remains. Keyword pagination still uses OFFSET. Old retrieval branches without a mode field retain their original `hybrid` interpretation, but old continuation pages whose identities cannot be confirmed become invalid. Ordinary chat search defaults to `strict_paging=false`, retaining existing pagination behavior.

Index building, index failure, and semantic-inference failure in strict retrieval return explicit tool errors instead of zero hits or automatic retrieval-method changes. The user's current BGE selection, background semantic organization, account permissions, snapshot lifecycle, and cancellation protection remain. No native WeChat FTS, new vector database, or approximate-search dependency was introduced.

Reproducible benchmark: `tools/benchmark_agent_retrieval.py --output <new-isolated-directory> --baseline <pre-optimization-commit>`. By default it uses synthetic messages and fixed vectors. `--manifest <local-private-JSON>` can copy real indexes read-only and use the current local model. Manifest fields are `source_index`, `model_root`, and `config` (existing local-retrieval configuration). Do not commit the manifest or database copies. Reports include counts, timings, devices, and space only, without queries, account identifiers, or chat bodies. Real-model cold start means a new service and new inference process; synthetic groups create only a new service and synthetic-engine instance. Neither clears the operating-system file cache. The benchmark directly calls `LocalSearch.hybrid()` with empty base-keyword candidates, measuring the semantic database's literal branch and vector fusion. It excludes base full-text FTS, routes, ChatTools/Gateway, desktop rendering, and the answer model, so it does not represent complete Agent-tool latency.

### Local measurement scope

On 2026-10-09, the pre-optimization baseline was `b7dc9dda252953eb32eeb79a5b20d0f7f7b64778`. Both versions used the same machine, data copy, and eight fixed query groups. The real-account index copy contained 35,537 messages and 5,704 chunks, retaining the user's `bge-base-zh` and `auto` selection; the actual device was CPU. Each version ran 5 cold starts, 24 warm queries, and 25 continuation pages. Values below are milliseconds, with p95 calculated by nearest rank.

| Stage | Before p50 / p95 | After p50 / p95 |
| --- | ---: | ---: |
| First hybrid query in a new inference process | 1135.88 / 1144.86 | 982.59 / 988.08 |
| Warm hybrid query | 214.91 / 226.03 | 65.70 / 82.02 |
| Hybrid continuation page | 1.11 / 1.19 | 1.13 / 1.28 |
| Literal branch inside hybrid retrieval | 153.57 / 159.67 | 1.80 / 17.98 |

Migration took 72 batches and 2.449 seconds. The database grew from 139,640,832 to 148,889,600 bytes, approximately 8.82 MiB extra. Digests of all existing vectors matched. Literal results and identities/order of the first ten hybrid results matched. Neither version encoded again during pagination: the old version already had an implicit cache. This iteration strengthens ticket identity and invalidation; eliminating pagination inference overhead cannot be claimed as a new result of this change.

A second run used 30,000 synthetic messages and 30,000 fixed two-dimensional vectors to isolate database cost. Migration took 0.935 seconds and added 13,275,136 bytes. Warm-hybrid p50 fell from 284.18 to 241.40 ms, but p95 rose from 288.30 to 315.03 ms; new-service first-query p50 rose from 285.76 to 304.72 ms. Frequent terms matching many FTS candidates still incur sorting/recheck cost, so acceleration is not promised for every query. Both groups checked matching hits before/after; real BGE verification and synthetic fixed-vector verification are recorded separately.

These results establish actual BGE inference at the local-service layer on a read-only real-index copy. They do not establish acceptance of the answer model, running desktop application, installer, or WeChat client operations. Original account indexes, configuration, and source messages were not rewritten for testing. Backend regression covers character semantics, SQL sender pagination, exhaustive vector ranking, adds/edits/cancellation during migration, cleanup rollback, ticket expiry/restart/changes, and account isolation. Frontend regression covers requested/actual modes and explicit error presentation.

Final relevant backend regression passed 434 tests and 4 subtests, with 2 warnings. The full frontend Node suite and Vitest's 76 files/911 tests passed; mode/error presentation separately passed 27 rechecks. These are automated regressions. The real local BGE and read-only account-copy scope is described above and is not presented as full desktop acceptance.

When no model is selected, the recommended model is shown with an explicit requirement to download and click **Use this model**. To delete the last model, first disable the feature and clear indexes to release that account's references. Deletion remains blocked if other accounts still use it.

## Pinned Hugging Face sources

The manifest is `src/wechat_decrypt_tool/resources/local_search_models.json`. It includes full commit SHAs, per-file sizes and SHA-256 hashes, licenses, pooling, prefixes, dimensions, and input limits. Runtime does not track `main`.

| Model | Hugging Face repository and pinned commit | Download bytes | Vector dimensions |
| --- | --- | ---: | ---: |
| BGE Small Chinese, recommended | `Xenova/bge-small-zh-v1.5` · `75c43b069aac4d136ba6bc1122f995fedcfd2781` | 95,401,750 | 512 |
| BGE Base Chinese | `Xenova/bge-base-zh-v1.5` · `71e50dc531959f9e04ebf190ea25b00261a0a186` | 407,503,264 | 768 |
| Multilingual E5 Small | `intfloat/multilingual-e5-small` · `614241f622f53c4eeff9890bdc4f31cfecc418b3` | 492,421,556 | 384 |

BGE's original author is **BAAI**, and its ONNX conversion source is **Xenova**; E5 comes from intfloat. The manifests mark all three repositories MIT. Downloads include only `onnx/model.onnx` and required tokenizer/configuration files, excluding training weights and other quantized variants. Each current manifest has six files, FP32, and a maximum input of 512 tokens.

BGE uses CLS pooling and L2 normalization; queries receive the Chinese retrieval prefix, while documents do not. E5 uses attention-mask mean pooling, L2 normalization, and `query: ` / `passage: ` prefixes.

The official download SDK is pinned to `huggingface-hub==1.27.0`. All requests explicitly pass `token=False`, with implicit credentials and telemetry disabled; other tools' Hugging Face login state is not read. Anonymous mode supports the included public, nongated resources; **it does not mean every Hugging Face model requires no login**. API behavior is documented in [Official file downloads](https://huggingface.co/docs/huggingface_hub/package_reference/file_download) and [Gated models](https://huggingface.co/docs/hub/models-gated).

Downloads run in a separate worker, serializing one model globally and its files sequentially. Hugging Face CDN redirects are allowed; persistent status saves only repository, commit, and file identifiers. There is no third-party mirror switching. Each recovery round shares a maximum budget of five attempts. HTTP 429 uses the server's wait interval; transient errors use bounded backoff; CDN access denial resolves the pinned address again. Missing versions, changed source permissions, insufficient disk space, and checksum failures have distinct messages. SDK internal retries are disabled to prevent stacked budgets.

Pause retains Hugging Face temporary data; resume reuses verified files. **Cancel and clean up** deletes only this application's model-version directory. A completed download becomes selectable only after full hashes, model loading, and small-sample inference pass. Complete-model startup reads local files only; corrupt files cannot be classified as CUDA failures.

### Offline import

On a networked computer, prepare the manifest files for **the same commit version** in the table, retaining paths such as `onnx/model.onnx`. On the target machine, click **Offline import** on the model card and enter that directory. The application verifies fixed hashes and runs local inference. No Hugging Face token is needed, and repository code is not executed.

The NVIDIA offline-component directory must contain the ORT GPU wheel matching the current CPython architecture plus four NVIDIA wheels, with filenames and hashes from the GPU manifest. Arbitrary models, arbitrary ONNX files, repository scripts, and third-party CUDA installers are not accepted.

## CPU / NVIDIA / AMD compatibility matrix

CPU ships with the base application: Windows uses `onnxruntime==1.28.0`; macOS uses `onnxruntime==1.23.2`, with macOS 13+ arm64 / x86_64 wheels. The tokenizer is pinned to `tokenizers==0.23.1`, and the vector extension to `sqlite-vec==0.1.9`. Mac automatic mode uses CPU and hides the Windows NVIDIA component entry.

Windows x64 AMD DirectML and NVIDIA CUDA components are independent of CPU and voice environments. AMD ORT 1.24.4, its component closure, RX 6750 GRE testing, and frozen acceptance are described in [Local acceleration](local-acceleration.md). The following is the existing NVIDIA CUDA manifest and historical acceptance; its manifest is `src/wechat_decrypt_tool/resources/local_search_gpu.json`:

| Component | Pinned version |
| --- | --- |
| ONNX Runtime GPU | 1.26.0, CPython 3.11 / 3.12 / 3.13 Windows x64 |
| CUDA Runtime | 12.8.90 |
| cuBLAS | 12.8.4.1 |
| cuDNN | 9.8.0.87 |
| cuFFT | 11.3.3.83 |
| Application's Windows driver baseline | 572.61 or newer |

The CPython 3.11 component download totals **1,671,874,915 bytes**; extraction requires additional disk space. Components come from official projects' PyPI wheels and are managed separately from retrieval models' Hugging Face sources. Wheel licenses remain with components.

The driver baseline follows the [CUDA 12.8 Update 1 driver](https://docs.nvidia.com/cuda/archive/12.8.1/cuda-toolkit-release-notes/index.html); it does not claim that every CUDA 12 application fails below that version. Devices below the baseline do not become verified GPU candidates. Automatic mode can use CPU; explicit GPU selection raises an error. Starting with ORT 1.27, default GPU packages use CUDA 13, so directly upgrading the GPU package is not valid; see [ORT CUDA requirements](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html).

| Scenario | Historical verification (not a full CUDA recheck after the current AMD integration) |
| --- | --- |
| RTX 4070 SUPER, driver 596.49, Windows x64 / Python 3.11 | Real CUDA inference passed for all three models |
| Forced CPU on the same machine | All three models and fixed retrieval samples passed |
| Packaged Windows EXE | CPU, external CUDA components, and sqlite-vec extension passed smoke tests |
| RTX 30 series / RTX 50 series | Intended support range; corresponding hardware unavailable, marked unverified |
| Clean Windows / physical computer without a discrete GPU | Hardware acceptance pending; CPU paths without components and with simulated GPU faults covered |
| Python 3.12 / 3.13 GPU wheels | Manifests pinned; hardware acceptance on those interpreters pending |
| macOS 26.3.1 / Apple M4 / CPU | Complete anonymous download, inference, hybrid retrieval, frozen-program inference, and source-desktop startup passed for three models; real-account end-to-end acceptance pending |
| Intel Mac / Linux CPU | Hardware acceptance pending; Intel Mac dependency wheels checked; the initial version provides no other GPU backends |

Device policies are Automatic, CPU, NVIDIA CUDA, and AMD DirectML. GPUs are selected by persistent identity, remapped to the current native index when a worker starts. Checks include driver, component loading, real inference with the selected model, and corresponding GPU nodes actually executed in profiles; a Provider name alone does not establish success. CPU/GPU use the same FP32 models. CUDA disables TF32; DirectML uses sequential execution and disables memory pattern. System PATH is unchanged, and a global CUDA Toolkit is not required.

Only explicitly classified GPU initialization, execution, or video-memory faults in automatic mode destroy the failed process and rerun the uncommitted work unit once in a separate CPU process, with the reason shown. Failed GPUs remain quarantined and require verification after redetection; restart does not release quarantine automatically. Explicit GPU failure stops immediately. Cancellation, input/model errors, unknown exits, and timeouts do not retry automatically. CPU indexing failure pauses and retains committed progress. Explicit-GPU, cancellation, and strict hybrid-pagination failures raise errors. Existing nonstrict CPU/automatic queries can still display keyword results with an explicit semantic-retrieval-unavailable message.

## Indexing and recovery

- The primary action area offers **Organize from scratch**, regenerating all vectors using the page's current model, conversation, and time-range selections. A paused task can discard resume state and start again. After a model change, the primary button shows **Use new model and organize from scratch**. The ordinary build endpoint also automatically creates an independent index and never reuses old-model vectors. Failure retains the old index; switching occurs only after completion.
- SQLite business records persist configuration, downloads, index tasks, and local audit. Each account has an isolated sqlite-vec database. Index generations isolate model switches; data and cursors commit in the same transaction.
- Content includes text, quotes, card titles, existing voice transcription, and cached local-document text and image/PDF OCR text from the attachment center. Index building does not trigger new transcription, OCR, visual descriptions, or video analysis. Old attachment caches containing only mixed visual output cannot pose as locally extracted text.
- After explicitly completing OCR in the attachment center, update the local retrieval index before Smart hybrid search uses the new cache. Original message provenance, page/image position, and OCR labels remain with retrieval evidence; Agent citations also identify OCR provenance. Verify OCR against the original image. Ordinary chat keyword search still reads original message fields; OCR literal lookup can use the attachment center's body search directly.
- Adjacent messages form chunks by conversation and ten-minute gaps, targeting 384 tokens with 64-token overlap and per-message source mappings. Overlong bodies are split.
- The first task fixes a cutoff time. Both manual organization and automatic updates every minute default to an incremental window beginning ten minutes before the latest committed cutoff, covering same-second messages and short-term backfills. Covered ranges are reused per conversation; newly added conversations or earlier historical ranges are filled as needed. Saving identical settings does not change configuration versions or checkpoints, and device-preference changes do not invalidate coverage. Narrowing scope synchronizes coverage after cleanup; widening it again fills the removed range. Transcription/local-attachment-cache version changes recheck selected scope, with another scope reconciliation daily. Long-term historical backfills may wait for the next reconciliation. The interface shows this round's reason and reused-message count.
- Unchanged content does not regenerate vectors. Changed text rebuilds affected chunks while retaining unchanged adjacent messages. Narrowing scope immediately constrains retrieval and removes excluded portions.
- Background indexing has one concurrent task, at most 8 chunks per batch, and two CPU inference threads. Interactive queries take priority over subsequent background batches. Voice activity does not overwrite retrieval-device preferences; the next work unit selects its device from current verified configuration. Five idle minutes release the model process.
- Each conversation keeps a continuous message stream in a dedicated thread, without rescanning the prefix on ordinary pagination. SQLite connection handling, native-cursor advancement, and closing occur on that thread. New checkpoints transactionally save the last committed timestamp and identities committed in that second together with data. Resume reads directly from that time and skips saved identities while retaining uncommitted messages from the same second. Old offset-only checkpoints still skip the prefix once on their first resume, then migrate automatically after the next commit.
- Automatic read batching is the default (`read_batch_size=0`). With total memory no greater than 4 GiB or free memory below 1 GiB, the maximum is 100 messages. With total memory no greater than 8 GiB or free memory below 3 GiB, it is 500. Otherwise automatic mode uses 1,000. Advanced settings offer 100/500/1,000/2,000; manual choices still obey memory protection, rechecked every batch. This does not depend on NVIDIA GPU presence.
- A batch is processed early when accumulated text reaches 128,000 characters, limiting chunk accumulation from a few long messages. Each message remains complete, so this threshold is not a process-memory hard limit. Inference still runs at most 8 chunks at a time, without expanding video-memory demand as read batches grow. Low-memory policy has simulated-test coverage; other hardware, systems, and disk speeds still require hardware verification.
- Read counts are reported approximately every 250 ms when new messages exist, and immediately at page end. SSE carries events every second, with 1.5-second polling as a backup. The interface updates read/chunk counts, actual batch size, and saved progress directly; older events or polling responses must not decrease progress. Unchanged device state does not write duplicate events.
- Organization uses fixed rounds. It first shows **Counting total messages**, prepares and deduplicates a complete manifest using the conversation catalog, cutoff, and scope fixed at task start, then shows **Read / Round total**. Temporary counts during preparation are not displayed as a denominator. Count failure retains the checkpoint and waits for retry instead of starting inference early.
- Once published, the round total never grows, disappears, or is recalibrated at completion. Progress is **Saved messages / Round total**. Newly discovered background conversations do not interrupt an incomplete round; messages received or backfilled after counting wait for the next round.
- To prevent differences between counting and actual reading, manifest preparation stages the round's messages by page. Organization then uses those local staged messages without reading WeChat bodies a second time. Memory is bounded by page count and character count. Initial organization waits for all counting and temporarily needs extra disk space. Staged files live under `plans/` in the local-retrieval data directory, isolated by account and task.
- Pause/restart retain counting checkpoints and fixed message manifests. Old-task migration includes already-saved counts in the round total. Completion automatically cleans temporary manifests; configuration changes, index clearing, and account deletion clean them too. Missing fixed manifests require organizing from scratch rather than silently recounting a different total.
- Processing continues while minimized and stops on exit. Restart resumes previously running tasks, while explicitly paused tasks remain paused. Data and checkpoints still commit in one transaction; in-progress read counts are not used for recovery. Failure/pause retains only committed batches, and large writes check cancellation every 100 messages. Resume rereads the unsaved batch.
- The `members(source,chunk)` index prevents a full association-table scan when finding chunks for original messages, applying to new and existing indexes. Batch deduplication and association queries run in a background thread.
- Keyword and vector branches each retrieve at most 200 candidates, deduplicate by source, and fuse through RRF with constant 60. Query tickets bind account, permission version, scope, and candidate order, expire after ten minutes, and cache at most 32 queries. Before returning retrieval text, original-message permissions and filters are applied again; unauthorized adjacent text in a chunk is not returned.
- Clearing indexes does not delete WeChat records. Account deletion removes that account's indexes and business records; SQLite free pages may remain for reuse.

The `/api/ai/local-search` prefix includes `status`, `settings`, `conversations`, model operations, `device/list`, `device/recheck`, component operations, index tasks, and `events`. Mutations allow local access only. SSE works with 1.5-second backup polling and supports Last-Event-ID on reconnection. `/api/chat/search` adds `retrieval_mode` and `search_ticket`; existing location fields remain compatible.

## 2026-09-08 acceptance record

### Real-account complete workflow recheck (after efficiency and completion-state fixes)

- Rebuilt through the running desktop application's local backend using the user's two selected conversations, last 30 days, BGE Small Chinese, and CUDA, without expanding conversation scope. `tools/verify_local_search_live.py` can recheck rebuilding, pause/resume, retrieval, and repeated checks; its report saves statistics only, without chat bodies.
- Rebuilding processed 6,622 messages and produced 554 chunks, with approximately 10.6 seconds on the task timer and actual CUDA execution. It paused after saving 1,000 messages, resumed the same task, and completed. Both reading and generation showed intermediate progress.
- All persisted vectors were finite and normalized, and no nonempty message lacked chunk associations. Both conversations passed original-text hits in the first ten results through the real local-search API, conversation/type filtering, duplicate-free pagination, and original-text location returning the target message.
- A repeat complete check processed 6,622 unchanged messages and generated 0 chunks; the existing index retained 6,622 messages and 554 chunks. The settings description incorrectly equating Generated this round with total index size was corrected. Current index totals are now separate; unchanged runs explain reuse, and empty indexes no longer imply that search is available.
- The actual Windows desktop Settings page was rechecked: automatic updates continue reading new conversations and show this check's read/generated counts plus current index totals. Later user-visible totals may therefore exceed this test snapshot.
- A ranking issue was fixed where multiple messages sharing one chunk vector could push an exact original-text match behind adjacent messages; regression coverage was added.
- Final relevant backend regression: 70 passed; local-retrieval Vue: 30 passed. Additional CPU testing used 48 synthetic messages and 8 labeled queries for each of three models, all with hybrid Recall@20 of 1.00. This small sample does not represent accuracy across real chats or performance on other physical machines.
- Local report: `tmp/local-search-live-e2e.json`. This iteration verified the source desktop application and current selected scope; other physical GPUs, clean systems, and installers require separate acceptance.

### Continuous reading and large datasets

`tools/benchmark_local_search_reading.py --messages 300000` uses synthetic SQLite chats in an independent temporary directory, reusing actual snapshot reading, message parsing, and index-storage code without reading real accounts. Fixed 512-dimensional vectors isolate database throughput; results do not represent complete model inference or live WeChat database speed.

- With 3,399 identical messages, old 100-message OFFSET pagination opened the source 34 times and traversed 59,532 messages. Continuous reading opened it once and traversed 3,399, with matching result checks. Final measured reading time: 0.97 seconds → 0.19 seconds.
- All 300,000 synthetic messages were read and indexed, opening the source once and traversing 300,000. Final duration was approximately 156 seconds, peak process RSS approximately 106 MiB, and increase since this iteration began approximately 42 MiB. First progress arrived at approximately 0.21 seconds, with 594 progress callbacks. This represents the current Windows machine only and is not a speed promise for other computers.
- Regression covers same-second messages, missing server IDs, cross-page duplicates, dynamically shrinking batches, early batching for long text, progress within a page, recovery after inference failure, cancellation during transactions, low memory, and system-information read failure. Frontend coverage includes live increases, protection against old polling responses/events decreasing progress, batch settings, and draft isolation.
- Relevant backend regression: 115 passed; frontend regression: 51 passed. After final changes, 28 local-retrieval frontend tests passed again.

The verification directory `tmp/local-search-validation` is separate from real accounts and is Git-ignored. No chats were sent to cloud services, and no answer-model tokens were consumed.

### Complete anonymous downloads and model comparisons

All three models downloaded completely and anonymously into a new application-model directory, passing per-file SHA-256, CPU loading, and inference. First downloads including verification took approximately **15.1 / 20.1 / 25.1 seconds** respectively; these network speeds do not represent other users' connections.

| Model | Maximum CPU / GPU vector difference | Comparison with original BAAI weights |
| --- | ---: | --- |
| BGE Small | 1.565 × 10⁻⁷ | Matching tokenization, maximum difference 2.012 × 10⁻⁷, matching sample ranking |
| BGE Base | 1.788 × 10⁻⁷ | Matching tokenization, maximum difference 2.682 × 10⁻⁷, matching sample ranking |
| E5 Small | 7.451 × 10⁻⁸ | Uses ONNX provided by the intfloat repository; not a Xenova conversion comparison |

Pinned original-author comparison commits: Small `7999e1d3359715c523056ef9478215996d62a620`; Base `f03589ceff5aac7111bd60cfc7d497ca17ecac65`. PyTorch / Transformers were installed only in an isolated verification environment, without adding application runtime dependencies.

Initial GPU testing found `cufft64_11.dll` missing, with normal CPU fallback. After completing and pinning the cuFFT wheel, all three models actually used CUDA. The packaged smoke-test EXE reported CPU ORT 1.28.0, GPU ORT 1.26.0, and sqlite-vec 0.1.9.

Real Hugging Face pause/restart recovery was also verified: pausing at approximately 10,486,476 bytes retained 10,485,760 bytes of temporary weights. Recreating the service retained paused state; manually resuming completed full verification. Importing the same files into a new local-model directory and loading passed. Script: `tools/verify_local_search_download_recovery.py`. Unit regression additionally covers 429 wait budgets, checksum failures, missing versions, and access denial.

### Synthetic labeled retrieval set

The 48 fixed samples and 8 labeled queries cover delays, amounts, order identifiers, vague recall, and English expressions. Testing reused the project's character FTS5 rules and real ONNX + sqlite-vec + RRF with offline mode enabled.

| Model | Keyword Recall@20 | Hybrid Recall@20 | Median query duration | Inference subprocess RSS |
| --- | ---: | ---: | ---: | ---: |
| BGE Small | 0.25 | 1.00 | 15 ms | 158 MB |
| BGE Base | 0.25 | 1.00 | 31.5 ms | 465 MB |
| E5 Small | 0.25 | 1.00 | 15.5 ms | 766 MB |

This is small synthetic regression, **not overall real-chat accuracy or performance on low-spec computers**. Large real-chat datasets, implicit context, and more human-labeled datasets still need evaluation.

Reproduction scripts: `tools/verify_local_search_models.py`, `tools/verify_local_search_gpu.py`, `tools/verify_local_search_reference.py`, `tools/verify_local_search_retrieval.py`, and `tools/verify_local_search_frozen.py`. Logs are `logs/local-search-*.log`. Backend/Vue regression covers account isolation, filtering, transaction recovery, model switching, GPU fallback, cancellation, retry limits, offline import, opening Settings without automatic scanning, and source location. Production builds and desktop model cards were rechecked.

Relevant backend regression: **102 passed**, including 23 local-retrieval tests; Vue regression: **132 passed**. Nuxt production generation completed 34 routes. Packaged smoke testing again confirmed CPU and external-GPU inference. The project restarted with updated source; the feature remains off by default.

Pending release verification: end-to-end network fault injection for disconnection and expired CDN URLs, actual disk exhaustion, large real-account effectiveness, RTX 30/50 hardware, physical machines without discrete GPUs, clean Windows installation, and long-duration concurrent speech/GPU stress. These items must not be marked passed.

## Settings interaction redesign acceptance (2026-09-08)

- Conversation selection provides select all, select all current search results, and clear selection. Batch actions change drafts only and apply on confirmation; closing the dialog retains the original scope.
- Advanced settings use a separate card with explicit expand/collapse controls and explain runtime devices, GPU acceleration, automatic updates, and data management.
- The old primary button's dependency on the top enable switch, including unexplained disabled state, was corrected. Enabling and first-time index building became one explicit submission.
- Model/conversation selections change drafts only; completing a download does not select a model automatically. Advanced settings do not save the primary-flow draft as a side effect.
- Vue regression covers initial enablement, model selection, cancelling scope changes, submission failure, pause/resume, date validation, account switching, and keyboard navigation.
- A separate acceptance page using actual Vue components and fictional data checked layout and interactions without changing real-account scope or starting additional real-chat indexing. Preview: `frontend/tests/fixtures/local-search.html`, for development acceptance only.
