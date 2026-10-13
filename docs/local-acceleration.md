# Local model acceleration and acceptance

Open **Settings → Local acceleration** to inspect GPUs, install isolated components, verify models, and select runtime devices. This entry manages local semantic retrieval and attachment OCR together; CPU support ships with the base application. Acceleration changes only the local inference device, without changing account permissions, index scope, or the AI service that generates Agent answers.

## Feature scope

| Feature | Available devices | Settings and runtime state |
| --- | --- | --- |
| Local semantic retrieval | CPU, NVIDIA CUDA, AMD DirectML | Model and device preferences are saved per WeChat account; the three existing FP32 models are verified individually |
| Attachment OCR | CPU, AMD DirectML | Global preference; RapidOCR 3.10.0, PP-OCRv6 Det/Rec, and retained PP-OCRv2 Cls |
| Speech to text | Existing backend for each voice model | This entry displays actual state; device preferences remain in Speech to text settings, with no AMD integration currently |
| Laya local analysis | CPU | Actual CPU is shown only after successful inference; no AMD integration |

GPU capability is verified separately for the device, driver, component, and current model. Detecting an AMD GPU, completing installation, or finding a Provider name does not establish model usability. Current AMD hardware evidence is recorded below; the first OCR release does not integrate NVIDIA GPU support.

## Usage

1. Open **Settings → Local acceleration** and wait for asynchronous detection. Startup detection does not download components or preload every model. After changing the GPU, driver, or resolving a fault, click **Detect GPUs again**.
2. For AMD, download the **AMD DirectML** component, or click **Offline import** and enter the local directory containing the pinned-version wheels. Downloads can pause and resume; errors show the specific cause.
3. For retrieval, first download and select a model in **Settings → AI services → Local retrieval**, then verify it in Local acceleration. OCR uses the three weights prepared with the base application. Component installation triggers verification of currently eligible models; other models still require separate verification after switching.
4. Select **Automatic**, **CPU**, or a verified GPU. Retrieval automatic mode prioritizes verified CUDA, then verified AMD DirectML, then CPU. OCR automatic mode chooses between verified AMD DirectML and CPU. An explicit GPU cannot be selected before verification passes. Saved changes take effect at the next work unit; all pages of one attachment keep the configuration selected at its start.
5. Check the actual device under **Feature execution**. **Not run yet**, **Verification passed**, and actual execution are distinct states; verification does not pretend that business data has been processed. After the model process is released, the last execution is shown. If some GPU operators run on CPU, the status shows **GPU + partial CPU**.

The attachment center provides an entry to Local acceleration. Device information in an already-completed body comes from that recognition's cache and is historical; it does not mean the GPU is still occupied. OCR remains explicitly user-triggered and does not download models or call cloud services during recognition.

### Failure and recovery

Only after an explicitly classified GPU initialization, execution, or video-memory fault does **Automatic mode** destroy the failed worker and rerun the uncommitted work unit once in a separate CPU worker, displaying the reason for switching. Subsequent OCR pages stay on CPU, and the cache publishes only when the entire attachment completes. A failed device remains quarantined; restarting does not silently try it again. Redetection requires new verification.

An **explicit GPU** fault stops the task without switching to CPU. Cancellation, invalid input, missing/corrupt models, configuration errors, unknown process exits, and timeouts are not treated as automatically recoverable GPU faults. A second failure on CPU also stops execution. Retrieval retains committed progress, while attachments do not publish partial success. Corrupt configuration and read/write failures surface directly instead of resetting to normal defaults.

## Isolated components and models

The AMD manifest is [`local_acceleration_dml.json`](../src/wechat_decrypt_tool/resources/local_acceleration_dml.json), using fixed official PyPI files, sizes, and SHA-256 hashes. The Windows x64 / CPython 3.11 download closure is:

| File | Download bytes |
| --- | ---: |
| `onnxruntime_directml-1.24.4-cp311-cp311-win_amd64.whl` | 25,106,000 |
| `sympy-1.14.0-py3-none-any.whl` | 6,299,353 |
| `mpmath-1.3.0-py3-none-any.whl` | 536,198 |
| Total | **31,941,551** |

The base application supplies `flatbuffers`, `numpy>=1.21.6`, `packaging`, and `protobuf`; frozen builds collect their packages and metadata too. CPython 3.12/3.13 use ABI-matching ORT wheels from the manifest, with slightly different download totals; hardware acceptance on those interpreters is pending. Offline import must provide the matching ORT wheel, SymPy, and mpmath together, rather than only copying a DLL. Extraction also needs additional disk space.

Components are unpacked separately under the application data directory without replacing base CPU ORT, modifying system PATH, or installing a global CUDA Toolkit. CPU, CUDA, and DirectML use isolated inference processes. Each process selects its runtime before importing model libraries; two ORT installations must not be mixed in one process. NVIDIA retains the existing [CUDA component manifest](local-semantic-search.md#cpu--nvidia--amd-compatibility-matrix).

Retrieval retains the pinned BGE Small Chinese, BGE Base Chinese, and Multilingual E5 Small models in [`local_search_models.json`](../src/wechat_decrypt_tool/resources/local_search_models.json), without reexporting or quantizing them. All three use ONNX IR 6 / opset 11. OCR retains the three original files and SHA-256 hashes from [`local_ocr.py`](../src/wechat_decrypt_tool/local_ocr.py): Det/Rec use IR 10 / opset 11, and Cls uses IR 7 / opset 11. Every load verifies the complete model hash; acceleration does not alter weights, vocabulary, pooling, prefixes, or image preprocessing.

## 2026-10-11 AMD hardware record

Environment: Windows x64, CPython 3.11.16, AMD Radeon RX 6750 GRE 10GB, driver `32.0.21045.9003`, CPU ORT 1.28.0, and DirectML ORT 1.24.4. The physical GPU's current raw DXGI index is 0, with 10,689,413,120 bytes of dedicated video memory. The device is saved by persistent identity; each worker startup remaps its current index and LUID. Detection excluded a same-named indirect-display virtual adapter with no rendering capability. A filtered list position cannot be passed directly to DirectML.

All inputs were pregenerated nonprivate text, images, and PDFs. No user chats, account directories, or user attachments were read. The node counts below are execution-event counts in fixed-sample profiles, rather than unique operator counts; they vary with sample count and execution count. The GPU column contains actual executed `DmlExecutionProvider` events.

### Retrieval correctness and execution evidence

The three fixed topics were project delivery, a weekend meal, and invoice amounts. CPU and DML queries for all three models hit `[0, 1, 2]`. Verification covered variable-length inputs, batch-shape changes, vector dimensions, finite values, and normalization. Predetermined thresholds were maximum absolute error ≤0.001 and minimum cosine similarity ≥0.99995.

| Model | Isolated-probe maximum absolute error | Minimum cosine similarity | Production warmup DML / CPU execution events |
| --- | ---: | ---: | ---: |
| BGE Small Chinese | 2.8313e-7 | 0.9999999726 | 250 / 4 |
| BGE Base Chinese | 2.3842e-7 | 0.9999999765 | 698 / 4 |
| Multilingual E5 Small | 1.1921e-7 | 0.9999999622 | 698 / 4 |

The isolated probe covered more calls, with DML/CPU event counts of 1125/18, 3141/18, and 3141/18 respectively. Each model was verified again after integration with the production `LocalInference` worker; hits still matched, and maximum absolute errors were all below 2.1e-7. These fixed retrieval samples do not represent the quality of every real-account query.

| Model | Single query CPU / DML, milliseconds | Batch of 8 CPU / DML, milliseconds | First encoding CPU / DML, milliseconds |
| --- | ---: | ---: | ---: |
| BGE Small Chinese | 5.84 / 17.15 | 16.89 / 19.88 | 9.11 / 73.18 |
| BGE Base Chinese | 29.46 / 63.93 | 96.25 / 71.74 | 44.22 / 74.43 |
| Multilingual E5 Small | 8.05 / 50.51 | 38.20 / 51.64 | 19.30 / 62.06 |

Single-query and batch durations are means of three runs with profiling enabled. Only BGE Base's batch of 8 was faster on DML in this sample. Automatic device priority does not promise that the current GPU is faster than CPU. First encoding excludes model loading; loading times are recorded separately in the evidence.

A separate index recheck used production chunking, workers, and SQLite submission for 24 fixed messages: 24 chunks per model, 3 batches × 8 messages, and one commit. All three topics had correct top1 hits. CPU indexes with DML queries and the reverse combination passed. Message/chunk statistics, commit cursors, and SHA-256 hashes of stored vectors were unchanged across the switch.

| Model | New worker cold start CPU / DML, seconds | 24-message indexing including cold start CPU / DML, seconds | Single query including SQLite CPU / DML, milliseconds |
| --- | ---: | ---: | ---: |
| BGE Small Chinese | 0.368 / 0.677 | 0.448 / 0.819 | 5.42–5.89 / 12.79–17.31 |
| BGE Base Chinese | 0.979 / 1.158 | 1.363 / 1.369 | 26.21–27.75 / 52.04–58.62 |
| Multilingual E5 Small | 1.394 / 1.933 | 1.871 / 2.549 | 6.05–6.23 / 35.48–37.43 |

This is one small-batch record. Cold start means a new process; the system file cache was not cleared. WeChat reading, database table creation, and UI were excluded. Full data and the reproduction script are `index-production-result.json` and `index-production-check.py` in the local acceptance directory.

### OCR correctness and execution evidence

The fixed sample included a Chinese local-OCR test phrase represented below, English `Receipt ZX739201`, and digits `1234567890`. It covered a normal image, a full-image 180° rotation, a blank image, a two-page scanned PDF, and a PDF with a native title and embedded image.

```text
\u672c\u5730\u6587\u5b57\u8bc6\u522b\u6d4b\u8bd5
```

Each OCR image was repeated three times. Text, page labels, and order matched CPU exactly across 9 image/PDF fragments. Actual maximum differences in box coordinates and confidence were both 0, against predetermined tolerances of 2 px and 0.02 respectively. Production-worker replay of six OCR units also matched the fixed expectations completely.

| Stage | Production-worker warmup DML events | CPU events | All isolated-sample DML / CPU events |
| --- | ---: | ---: | ---: |
| Detection Det | 314 | 0 | 2826 / 0 |
| Direction classification Cls | 310 | 0 | 2325 / 0 |
| Recognition Rec | 340 | 4 | 2550 / 30 |
| Production warmup total | **964** | **4** | — |

All three stages have GPU execution evidence. Rec still executes partly on CPU, and actual state shows **GPU + partial CPU**.

| OCR record | CPU seconds | DML seconds |
| --- | ---: | ---: |
| Import and three-stage initialization | 0.614 | 0.856 |
| First normal single-image inference | 0.841 | 1.201 |
| Normal single-image median of three | 0.807 | 0.135 |
| Rotated-image median of three | 0.775 | 0.157 |
| Scanned PDF first-page median of three | 6.890 | 0.330 |
| Scanned PDF second-page median of three | 6.850 | 0.310 |
| Two-page PDF reading and three OCR runs per page | 41.417 | 2.151 |

The single image was 1100×550; scanned PDF pages rendered at 2200×1100. The first GPU image includes first graph-compilation cost. Retrieval and OCR timing did not control parallel tasks, system load, or temperature. These are execution records, rather than rigorous benchmarks, and cannot promise acceleration for every input. Independent production-OCR verification plus replay of six production units totaled approximately 6.267 seconds; this is not directly comparable with the individual timings above.

## Acceptance levels and evidence files

| Level | Completed | Boundary |
| --- | --- | --- |
| Automated tests | Configuration/component/device selection, fault classification, one CPU recovery, cancellation, closing, account isolation, whole-attachment publication, and related checks | Simulated faults establish logical behavior, rather than real driver-fault recovery |
| Source-application lifecycle | Full application startup → status API → shutdown in an isolated empty data directory | No business models loaded and no real-account data used |
| Real source inference | CPU/DML comparisons for three retrieval models, three OCR stages and fixed images/PDFs, production-worker replay | Fixed nonprivate samples, rather than an actual account's complete workflow |
| Production HTTP API | Real execution passed for three retrieval models and OCR verification through ASGI routes; business state remained Not run yet afterward | Production API invoked in-process, rather than the complete browser-to-desktop path |
| PyInstaller frozen | 6.22.3 onedir executable called the production worker; BGE Small CPU/DML and OCR CPU/DML passed | Actual `aiPackagingArgs` and dependency metadata used; not every frozen combination of all three retrieval models covered |
| v1.3.0 production onefile backend | HTTP API started from the final release package; real DML verification passed for all three retrieval models and OCR stages; synthetic-attachment CPU/DML OCR full text matched, actual devices matched, and no fallback occurred | Fixed nonprivate samples; no complete real-account workflow or performance guarantee for all inputs; this release acceptance sent no WeChat messages and did not verify automatic-reply delivery |
| v1.3.0 installer/ZIP | PE version, ASAR, static UI, ZIP CRC, and per-file hashes between NSIS and expanded directory passed; OCR weights, resources, and dependency metadata complete; packages contained no local runtime data or credentials | Actual installer installation, upgrades, clean Windows, other physical GPUs, and full Electron business acceptance still pending |
| NVIDIA | Existing CUDA components and APIs retained; historical retrieval evidence in the retrieval guide | No local NVIDIA GPU; the current code's CUDA hardware matrix was not rerun; OCR has no NVIDIA GPU integration |
| Other devices/platforms | CPU path retained; AMD manifest limited to Windows x64 | No current hardware evidence for other AMD GPUs, Intel GPUs, Python 3.12/3.13, or related combinations |

This version adds no AMD acceleration to voice backends; Laya remains on CPU. Ordinary keyword search, database queries, and PDF text reading retain their existing implementations. Verification records and limits stay in project documentation; GitHub Releases contain only version changes, downloads, and necessary usage instructions.

In frozen testing, CPU 1.28.0 modules and native libraries came from `_internal`, while DirectML 1.24.4 came from the complete external component directory. BGE Small warmup recorded 250 GPU / 4 CPU events, and OCR recorded 964 GPU / 4 CPU events. Actual module origins were checked; successful frozen imports alone cannot substitute for inference verification.

Local evidence is under `tmp/local-acceleration-validation/`. These workspace-generated artifacts normally do not enter Git or release packages:

- `hardware.json`: native device identity, index, driver, and exclusion reasons.
- `search-cpu.json`, `search-dml.json`, `search-comparison.json`, `search-opsets.json`: results, timings, errors, and metadata for the three models; `cpu-*.json` / `dml-*.json` in the same directory are raw profiles.
- `search-production-result.json`: per-model results from the production retrieval worker, actual ORT module/native-library origins, and warmup evidence.
- `ocr-probe/samples/manifest.json`: fixed inputs and font hashes; `ocr-probe/cpu.json`, `dml.json`, `comparison.json`, and stage profiles: OCR results, differences, and timings; `ocr-opsets.json`: model metadata.
- `ocr-production-result.json`, `ocr-probe-report.md`: production OCR replay and stage records.
- `api-production-result.json`: production API verification tasks and actual business state afterward.
- `source-app-result.json`, `source-app-check.log`: full source-application startup, status, and shutdown in an isolated empty data directory.
- `frozen-result.json`, `frozen-build.log`, `acceleration-probe.spec`: frozen inference results, build logs, and build inputs.

## Development and repeat verification

The shared management entry is [`local_acceleration.py`](../src/wechat_decrypt_tool/local_acceleration.py). Components, devices, and business workers are connected through persistent identities and verification keys. Keys include feature, model version/hash, backend, device, driver, component, and probe version; changing any of these requires corresponding verification. Retrieval fixes configuration per encoding operation, while OCR fixes it per entire attachment. Workers do not write user databases; the parent process retains existing account and lifecycle checks when committing results.

[`local_acceleration_runtime.py`](../src/wechat_decrypt_tool/local_acceleration_runtime.py) centralizes ORT loading, Session creation, and profile reading. DirectML uses sequential execution with memory pattern disabled, and ORT cannot silently move an entire Session to CPU. CPU operator partitions are recorded honestly. Profiling stops after fixed-sample warmup; real private inputs do not generate profiles. OCR uses RapidOCR's native `cfg.session` to inject three real Sessions without changing site-packages, global monkeypatching, or first creating unused CPU models.

Before running these commands, explicitly prepare components and models matching the manifests. `device-identity` uses the persistent identity from current detection; the standalone OCR probe's `device-id` must be the raw native DXGI index. These examples are for this machine; do not copy its index to another computer:

```powershell
.venv\Scripts\python.exe tools/verify_local_acceleration.py --backend cpu --models-root tmp/local-acceleration-validation/models --output tmp/local-acceleration-validation/repeat-search-cpu.json
.venv\Scripts\python.exe tools/verify_local_acceleration.py --backend dml --runtime-root tmp/local-acceleration-validation/components/dml-ort1.24.4/cp311 --device-identity '<copy device identity from current detection>' --models-root tmp/local-acceleration-validation/models --output tmp/local-acceleration-validation/repeat-search-dml.json --compare-with tmp/local-acceleration-validation/repeat-search-cpu.json
.venv\Scripts\python.exe tools/probe_ocr_dml.py --mode compare --runtime-root tmp/local-acceleration-validation/components/dml-ort1.24.4/cp311 --device-id 0 --output-root tmp/local-acceleration-validation/repeat-ocr --repetitions 3
.venv\Scripts\python.exe -m pytest tests/test_local_ocr_acceleration.py tests/test_local_ocr.py tests/test_attachments.py tests/test_local_search_ocr.py tests/test_library_extracted_text.py tests/test_ocr_dml_probe.py -q
```

Frozen rechecks use [`tools/verify_local_acceleration_frozen.py`](../tools/verify_local_acceleration_frozen.py). Run from the actual built executable and confirm `frozen=true`, real model outputs, and GPU execution evidence in the results. When adding hardware support or upgrading components, rerun the affected models' CPU comparisons, production workers, and frozen verification before updating this record.

Final targeted regression: **399 passed** across 26 relevant Python test files, including controlled CPU inference and 1 existing jieba deprecation warning; desktop Node tests: **143 passed**. Full frontend Node/Vitest tests and generation build passed. Later small changes such as device-error presentation passed **86 relevant tests**. Raw logs are in the acceptance directory.

Subsequent fault-classification, stop-gate, and platform-limit checks passed 114 and 23 targeted tests respectively. They overlap the regression above and are not added into an independent test total. Final frontend generation succeeded for 38 routes. Items without corresponding hardware remain marked unverified in the acceptance matrix.

### Post-implementation recheck

Additional recheck on 2026-10-11: unchanged runtime state no longer writes duplicate events. State-write failure still raises an error instead of caching success. Verification completion releases tasks and cancellation tokens; only the latest result per verification key remains, and redetection isolates tasks from old generations. Manual installation and restart-resume use the same automatic post-install verification flow and retain the first installation's account context; absent old context does not trigger guessed account selection.

The Local acceleration page merges duplicate refreshes. It subscribes to events and reads speech/Laya status every 5 seconds only while the section is active, stopping when closed. Verification tasks are displayed for the current model; CPU fallback history explicitly indicates the last run. Post-recheck backend tests: **171 passed**; frontend tests: **91 passed**; 38 routes generated successfully. The production verification APIs for all three retrieval models and OCR reran real AMD inference, and source-application startup/status/shutdown passed again.

v1.3.0 release-acceptance addition: when scrolling changes the highlighted settings section, loaded same-account content, import paths, and in-progress operations are preserved. Explicit actions can still refresh results; account switching and closing isolate old responses. Attachment image/video previews use centered native dialogs, preserve list position on opening/closing, and support the close button, Esc, and backdrop dismissal. Desktop and narrow-browser verification used synthetic data. Final relevant regressions passed: backend 188, desktop 143, frontend Node 85, and Vitest 1122; 38 routes generated successfully.
