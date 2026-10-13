# Search: Content and Attachments

The sidebar has a single "Search" entry. "All Content" and "Attachments" on the page share the account, keywords, and dates, preserving these criteria when switching views.

- **All Content**: Queries chat records, attachment text, OCR text, and folder contents together. Select sources and click "Search"; each source independently displays results, coverage, errors, and pagination.
- **Attachments**: Allows browsing with an empty keyword and offers name/body, type, conversation, and sender filters. The original attachment center's preview, download, original-message navigation, attachment saving, text extraction, OCR, batch stopping, full-text viewing/copying/export, and recognized-text saving are all available here.

The attachment text/OCR groups in All Content offer "View and Process Attachments", which opens the attachment view with the same query criteria. After extraction finishes, switch back to All Content and search again to find the new text. Switching views cancels old requests and clears old results; it does not duplicate attachment or OCR caches. The historical `/attachments` address explicitly redirects to `/search?view=attachments`.

Keywords in both views are space-separated, with at most 8 terms, and all terms must match. Matching is case-insensitive; identifiers, amounts, and punctuation are still checked literally. When "Name and Extracted Text" is selected for attachments, different terms can match the name and body separately. Search uses existing local content, does not call models, and does not automatically extract text or run OCR. Users must explicitly start recognition in the attachment view. Existing search on the chat page and Agent retrieval remain available.

## All Content Sources and Coverage

| Source | Searched content | Time filter | Open result |
|---|---|---|---|
| Chat records | Bodies, titles, links, quotes, and amounts in the local message index | Message time | Locate the original message |
| Attachment text | Saved document extraction text with valid source verification | Associated message time | Locate the attachment's original message |
| OCR text | Saved local OCR text with valid source verification | Associated message time | Locate the image or file's original message |
| Folder contents and notes | Titles, bodies, notes, and tags of current materials, including saved reports and text copies | Material update time | Open the corresponding material |

- Dates include the entire selected end day, including fractional material-update timestamps in its final second.
- Chats and attachments exclude hidden conversations and public accounts. Revocation archives retained by the chat index show a revocation marker; attachment text does not cover messages present only in revocation archives.
- "In Scope" is the record count within the current account, dates, and source scope. "Searchable Content" is the count of valid records in that source with existing text, not the total keyword match count. The page shows only loaded matches and does not estimate the complete result count.
- Complete extraction, partial extraction, no extracted text, not yet extracted, and changed sources are counted separately for attachments. Document extraction and OCR are a separate set of method counts and must not be added to status counts. A document text layer does not mean its images have undergone OCR.
- Restored accounts without an imported chat database can still search materials. Other sources show unavailability and unknown coverage; this must not be interpreted as zero matches.
- Switching such an account to the attachment view explicitly shows that the chat database is unavailable; another chat account is not substituted. Saved attachment copies remain readable from Local Folders.
- Chats and attachments come from a local message-index snapshot. Pending index updates are explicitly indicated. Search snippets are not rechecked against the original database; clicking a source continues to use the existing message-location flow. Historical ordinary-document caches retain existing signature compatibility rules; OCR must be bound to the original message and media identity.
- Material search covers current versions without duplicating historical topic versions in results. Saved material copies can still be opened when the original chat no longer exists.
- Caches with changed sources are excluded and counted. Corrupted caches and database read errors directly display that source's failure reason and can be retried individually by the user; they are not treated as successful empty results.

## Implementation and Lifecycle

All Content uses `GET /api/search` with `account`, `q`, `source=chat|documents|ocr|notes`, `offset`, and `limit`, plus optional integer-second `start_time` and `end_time`. The start second is inclusive, and the entire end second is inclusive. A page contains at most 100 items. The attachment view reuses `/api/attachments` and its existing file, text, and extraction endpoints; its queries likewise apply exact keyword filtering before pagination. Endpoints allow local-machine access only.

Changes to the account, criteria, or restored version cancel old requests and invalidate old results. The four request groups do not substitute for one another; a pagination failure in one group retains its previous results. Query terms are not added to navigation URLs, persistent storage, or new debug logs; the service retains existing query-parameter redaction.

The implementation reuses message SQLite FTS, AIStore attachment caches, and material records without adding a vector database, index dependency, or cross-request result cache. Chat search uses FTS candidates, sorts by time, checks each literally, and paginates actual matches. Pure-symbol queries explicitly use a literal scan rather than switching algorithms after catching a failure. Attachment search fully checks in-scope sources and extraction metadata to obtain actual coverage counts. Material dates are filtered during SQLite reads.

Message search holds existing account and snapshot work leases; material search uses the application-data account lease. Restoration, account deletion, original-message navigation, and material-draft confirmation continue to reuse existing flows.

## Measurements and Acceptance

### Content and Attachment Integration Acceptance (2026-10-13)

- The frontend passed 128 checks across 10 related files, including 10 real component/Pinia integration tests and 46 attachment operation tests. The backend passed 96 checks across 6 related files covering attachments, full text, text materials, unified search/API, and attachment-index sources. The production build passed.
- A production page connected to real local search, attachment, and material HTTP routes passed checks for a single entry/form, shared criteria, restrictions for accounts containing only restored materials, and old-address redirection.
- Actual TXT download, text extraction, and full-text reads for synthetic attachments passed, as did switching back to All Content to search newly extracted text. Sources and media paths used disposable synthetic fixtures; no real WeChat attachments were read, no OCR/models were run, and no package acceptance was performed.
- Checks at widths of 1440 and 480, with two themes and two views, found no horizontal overflow or script errors. The page had only one material scroll container. Private records: `.work/search-attachment-merge-20261013/`.

### Initial Unified Retrieval Measurements

The reproducible tool is `tools/benchmark_unified_search.py`. It uses fixed synthetic data with 1,000 or 10,000 records per source. The 10,000 tier contains 30,000 indexed messages, 20,000 extraction caches, and 10,000 current materials. Each source has 13 queries covering Chinese text, letter case, identifiers, amounts, multiple terms, rare terms, zero matches, symbols, pagination, and time ranges.

Each scenario has one warm-up and five measurements; p95 is the maximum of these five samples. The tool checks fixed match identities, ordering, fields, pagination, and coverage, and saves code snapshots and digests. Measurements exclude index preparation, HTTP, desktop rendering, models, OCR, and real WeChat operations, and must not be described as user-facing speedups in all scenarios.

On 2026-10-13, after regressions and builds finished, key scenarios were alternately retested in serial using the same SQLite data with 10,000 records per source. Results follow in milliseconds:

| Scenario | Before optimization p50 / p95 | After optimization p50 / p95 |
|---|---:|---:|
| Chat common term, first page | 59.92 / 60.57 | 20.47 / 20.92 |
| Chat deep page | 85.92 / 92.52 | 79.56 / 80.42 |
| Chat zero matches | 1.08 / 1.22 | 1.08 / 1.13 |
| Materials with date range | 65.99 / 72.69 | 27.48 / 27.52 |
| Materials without date limits | 80.00 / 85.03 | 77.90 / 79.75 |
| Attachment text, common term | 460.13 / 466.72 | 455.71 / 462.73 |
| OCR common term | 459.26 / 463.18 | 460.45 / 479.30 |

Chat improvements come from stopping unnecessary body decoding once exact matching has completed the current page; material date-query improvements come from filtering dates before reads. Materials without date limits, attachment text, and OCR have no clear algorithmic speedup; small differences in the table are not counted as optimization gains. Each attachment query still checks 20,000 in-scope caches to retain source validity and complete coverage statistics. This is the main remaining cost and is not reduced by skipping validation or fabricating coverage.

The full set of 104 scenarios checked fixed match identities, ordering, fields, pagination, and coverage. A snippet fix for letter case produced expected ellipsis changes for 81 results across 5 scenarios. Field-by-field comparisons confirmed complete equality apart from snippets. The original strict digest-comparison failures were retained rather than suppressed. The final end-second and malformed-type fixes have additional boundary tests, and final code was used in serial measurements.

Reproduce the benchmark:

```powershell
.venv/Scripts/python tools/benchmark_unified_search.py --output .work/unified-search-before --sizes 1000 10000 --iterations 5
# After modifying and verifying the code, use a new output directory; complete result equality is required by default.
.venv/Scripts/python tools/benchmark_unified_search.py --output .work/unified-search-after --sizes 1000 10000 --iterations 5 --baseline .work/unified-search-before/summary.json
```

Use `--allow-snippet-changes` only after explicitly verifying that snippet changes are expected. This option still requires all nonsnippet results to be identical and reports differences per scenario. Other development checks ran on the same computer during the first full matrix, so the main performance conclusions use later serial retests without those concurrent workloads. The operating-system cache was not cleared.

Acceptance records for this round:

- Backend, 20 related test files: 329 checks passed, plus 4 subtests. After final boundary fixes, the 5 affected unified-search, API, attachment, and text-material files were rerun, with 77 checks passing. These counts overlap and are not added together.
- Frontend, 14 related test files: 137 checks passed; navigation Node tests passed 10 checks. The production build passed.
- Production Nuxt pages connected to real local HTTP routes and synthetic SQLite passed checks for four-source results, material pagination of 20 + 3, opening the corresponding material, accounts containing only materials, zero matches, per-group corrupted-cache errors, and individual retries. Light/dark themes at widths of 1440/480 had no horizontal overflow or page script errors, and no requests to execute models or OCR.
- The final service additionally checked four-source results and fractional timestamps in the end second over real HTTP. Independent review confirmed resolution of coverage wording, original-message source labels, snippets, date boundaries, and malformed caches.
- Private evidence is in `.work/unified-search-20261013/`, including benchmark source snapshots, `snippet-comparison.json`, `serial-final.json`, test/build logs, `browser-report.json`, `final-http.json`, and page screenshots. Temporary acceptance services were shut down afterward.

These checks used disposable synthetic materials and did not read or restore real user materials. They did not perform full acceptance of users' real chat databases, real model/OCR inference, real WeChat operations, or package acceptance. Original-message navigation was checked through component callbacks and real anchor-protocol tests; synthetic browser acceptance verified the actual flow for opening saved materials.
