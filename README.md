**English** | [简体中文](README.zh-CN.md)

<p align="center">
  <img src="frontend/public/logo.png" alt="xwechat Logo" width="160" />
</p>

<div align="center">

# xwechat

**A local WeChat data analysis tool developed purely as a personal hobby, for recreational exploration and technical learning**

[![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011%20(x64)-0078D6?logo=windows&logoColor=white)](https://github.com/gongyuanshen/xwechat)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Node.js](https://img.shields.io/badge/Node.js-22.12+-339933?logo=nodedotjs&logoColor=white)](https://nodejs.org/)
[![Vue.js](https://img.shields.io/badge/Frontend-Nuxt%204%20%2F%20Vue%203-4FC08D?logo=vuedotjs&logoColor=white)](https://vuejs.org/)
[![License](https://img.shields.io/badge/License-Personal%20Hobby%20Only-lightgrey)](#disclaimer-and-strict-prohibition-on-commercial-use)

<br />

> ⚠️ **Important notice**: This is a **personal hobby project for entertainment and learning experiments**, created solely for personal enjoyment and technical research. It **involves no financial gain, commercial operations, or paid services**. Any use of this project for commercial profit is strictly prohibited.

</div>

---

## About the project

**xwechat** is a Windows tool for organizing, backing up, and analyzing your personal WeChat records. You can decrypt or import your own local data, revisit chats and Moments, search and export records, generate annual recaps, and use an **Agent** to find information and trace events and relationship clues, with local models or configured AI services as needed.

Database decryption, snapshot reading, and storage take place on your computer. The former client/broker data runtime modes and their authorization chain have been removed. Image decoding, media conversion, and speech recognition still have their own local component and model dependencies. **“Local data processing” does not mean that every application feature works without network access**: remote AI services receive the materials needed for the requested analysis; model downloads, media downloads, and external links also require a network connection.

This project is unsuitable for use as a full-fledged client. It is intended for viewing records rather than everyday client interaction, and some features are cumbersome to use. See the limitations below for details.

This is an unofficial experimental tool for personal learning and recreational exploration. Some features depend on the WeChat version, window state, local resources, and model capabilities. It cannot replace the WeChat client.

The current application interface is in Chinese; control names in this README are English translations of the labels shown in the application.

[Features](#current-features) · [Personal library and saved searches (Chinese)](docs/personal-library.md) · [Quick start](#quick-start) · [Agent](#agent) · [AI and speech](#ai-and-speech) · [Run from source](#run-from-source) · [Windows packaging](#windows-packaging) · [Known limitations](#known-limitations) · [Report an issue](#report-an-issue)

## Current features

The table below describes features in the current source code. Previously downloaded installers may not contain these updates; packaged capabilities are determined by the corresponding Release notes and actual acceptance checks.

| Feature | Current capabilities |
| --- | --- |
| Data decryption and import | Detect local WeChat accounts; scan for and validate database keys; decrypt WeChat 4.x local databases; preview and import account archive ZIP files or decrypted account directories |
| Multiple accounts and synchronization | Switch between local accounts; periodically synchronize verified snapshots and refresh manually for accounts bound to a source directory and key; browse imported historical archives independently |
| Chat browsing and search | Browse private chats, group chats, images, videos, voice messages, stickers, replies, merged chat records, and more; search by keyword, conversation, sender, date, and message type; view context, calendar heatmaps, and floating history windows |
| Personal library and saved searches | Save search filters, original chat messages, attachment copies, and completed Agent reports per account; filter by message type and rolling date range; manage folders, notes, tags, and manual verification; edit report copies and export Markdown/HTML; [guide (Chinese)](docs/personal-library.md) |
| Search and attachments | One Search entry for chats, extracted attachment text, OCR, and library notes, with source and coverage details; switch to Attachments with the same account, keywords, and dates to browse, filter, preview, download, extract text, run local OCR, or save copies; [guide (Chinese)](docs/unified-search.md) |
| Application data backup and restore | Back up matters, folders, notes, reports, saved searches, topic versions, and referenced attachment copies together; restore missing records to the active application while retaining local versions on conflicts; saved library data remains usable without the original chat database; [guide (Chinese)](docs/application-data-backup.md) |
| Matters and topic history | Save evidence-backed to-dos and explicitly confirmed deadlines or rescheduling, with optional reminders while the app runs; save topic versions and compare added, removed, or changed materials; [matters guide (Chinese)](docs/matters.md) |
| Chat time machine | Compare messages from the same day across recorded years, or browse a chosen date range in year columns, with source navigation and index freshness details; [guide (Chinese)](docs/time-machine.md) |
| Disk usage management | View per-account usage for databases, media, indexes, snapshots, and library copies in Settings; explicitly remove rebuildable full-text indexes while protecting original messages, attachments, snapshots, and running tasks; [guide (Chinese)](docs/performance-storage.md) |
| Contacts and service accounts | View contact categories, profiles, group members, friend verification records, and service account messages; export contact profiles |
| Moments | Browse the local timeline, historical cover images, images, videos, Live Photos, likes, and comments; perform full and incremental exports |
| Favorites and other records | Search and export local favorites, mini program information, Channels live-stream caches, and payment records; view records related to message revocation |
| Record export and account backup | Export HTML, JSON, TXT, and Excel; create full ZIP archives and incremental directory exports for chats and Moments; export complete account archives containing databases and resources |
| Agent | Use a standalone entry point or an in-chat conversation; plan steps and call search and reading tools to trace events, to-dos, and relationship clues across chats; coordinate subtasks for complex analysis as needed, with original-message citations, execution progress, additional instructions, stop, and resume |
| Chat AI tools | Summarize messages, run automatic summary tasks, and manage follow-up alerts and history; scheduled tasks run while the application is running and use the configured model service |
| Experimental automatic replies | Opt in to text replies for selected private and group chats with a fixed model and custom instructions; groups default to mentions of you; inspect reply history and manually review uncertain sends |
| Chat profiling and intent recognition | Analyze with local Laya or an explicitly selected API; generate person/group profiles, emotion and intent labels, chat characteristics, and tentative MBTI inferences; automatic recognition requires explicit opt-in |
| Local semantic search | After downloading and enabling a retrieval model, gradually index all chats in the current account; combine keyword and semantic search, pause and resume indexing, and restrict individual queries by chat and date |
| Local speech-to-text | Download and select CPU or NVIDIA GPU models; transcribe individual or batches of voice messages; preserve existing WeChat transcriptions and distinguish result sources |
| “Echoes of Another Realm” annual recap | Enter a ten-chapter, three-dimensional annual experience by account and year; view message volume, active hours, text, interacting contacts, reply patterns, and sticker statistics; expand details and return to original messages |
| Experimental WeChat sending | Use a logged-in Windows WeChat window to send text, PNG/JPEG images, and files; select or paste multiple attachments and send them one by one through a queue |
| MCP integration | Provide read-only local account, chat, Moments, media, annual statistics, and other query tools to clients that support HTTP MCP |

## Quick start

### Get the desktop application

Check [GitHub Releases](https://github.com/gongyuanshen/xwechat/releases) for published versions and their notes. Windows x64 packages are available in the following forms:

The current project version is `1.4.0`. Version numbering for this independent project starts at `1.0.0`. Refer to the Releases page for the packages actually published and their versions.

- **Installer**: Run `xwechat-<version>-Setup.exe` and select a directory in the installation wizard.
- **Portable package**: Fully extract `xwechat-<version>-Setup.zip` and run `xwechat.exe` from the extracted directory. Keep all resources in that directory.

Desktop packages include Electron, the Python backend, and basic runtime components; you do not need to install Python or Node.js separately. Current builds also collect local CPU OCR components and their weights. Optional AMD DirectML components for retrieval/OCR and NVIDIA CUDA components for retrieval are managed under **“Settings → Local acceleration”**; see the [scope and acceptance record (Chinese)](docs/local-acceleration.md). Laya, speech, and semantic retrieval **model weights must be prepared separately**. The Qwen GPU speech model also requires a package containing its runtime components. See [Windows packaging](#windows-packaging) for build instructions.

### Connect to local data for the first time

1. Keep desktop WeChat running, log in to the account you want to read, and click **“Detect local WeChat”** on the home page.
2. Verify the account and data directory, then open the decryption page. Before obtaining the database key, enter the absolute path to that account’s complete `db_storage` directory. You can also enter a known key manually.
3. After obtaining the key, exit WeChat and copy the account’s entire `db_storage`, keeping the `.db` files and their corresponding `-wal` files. Then perform standard decryption on this stable copy. Standard decryption does not guarantee cross-database transaction consistency while WeChat is running.
4. Images use a separate image key. Batch image decryption, sticker downloads, and speech-to-text are optional; you can skip them initially and enter the chat page.
5. Select a decrypted account from the account list to browse, search, or export records. Prepare the relevant services and models when you need AI or speech features.

Continuous synchronization uses the source directory and key bound to the account. Verified snapshot synchronization starts automatically on the chat page and waits approximately 30 seconds after each round of checks. **If the bound source is a backup copy, synchronization checks that copy**; accounts imported from archives without a bound source directory do not receive new messages from local WeChat. The top refresh button is for manual refresh. Automatic synchronization stays quiet; failures show their cause and a retry entry point.

### Import an existing backup

Choose **“Import backup”** on the home page and select an original account ZIP exported by this application, without extracting it, or a decrypted account directory. You can also select an `output` root directory containing exactly one account.

Review the detected account, databases, and resources before confirming the import. Data is copied into the application’s local data area; an existing account with the same name is backed up first, and the original backup directory is not modified directly. Ordinary chat reading exports and complete account archives serve different purposes. To import an account, use an account archive or a decrypted directory that meets the requirements.

The chat search sidebar can save frequently used filters, including message type and rolling date ranges. The original-message context menu offers **“Add to library”**, and completed Agent answers offer **“Save report”**. Open **“Search”** in the sidebar to search all content or switch to **“Attachments”** for browsing, extraction, and explicit local OCR; both views share account, keyword, and date controls. **“Personal library”** manages copies and topic versions. See the [guide (Chinese)](docs/personal-library.md) for supported formats and coverage.

Use **“Settings → Data backup”** for application-created matters, notes, reports, saved searches, topic versions, and attachment copies. Restore fills missing data in the active application and keeps local records when they conflict. This backup is separate from the chat database/resource archive; see the [backup guide (Chinese)](docs/application-data-backup.md).

## Export and annual sharing

For chat exports, select conversations, dates, and message types, then choose HTML, JSON, TXT, or Excel and include local media as needed. Moments supports full and incremental exports; contacts, favorites, and other pages provide their own export entry points. To back up account databases and resources, use **“Export archive”** on the home page; save application-created materials separately through **“Settings → Data backup”**.

- Depending on the selected options, export can obtain an image key, download available media, or generate local voice transcriptions. These steps require the corresponding client, resources, network connection, or a ready transcription model.
- Attachments absent from local storage are recorded as missing; this does not guarantee that all historical attachments are complete. Remote images not packaged as local files in HTML, and external links, may still access the network when opened.
- The SHA-256 manifest in an archive checks content consistency. It is not an author signature or proof of origin. Complete account archive imports check the file set, hashes, and database integrity.
- The existing export API supports WEC1 encryption of an entire package, with an explicitly supplied, separate 32-byte content key. Incremental directories do not support whole-package encryption. The graphical importer accepts ZIP files/directories; encrypted files must be decrypted first.

The “Echoes of Another Realm” sharing panel provides separately laid-out **PNG posters** and **offline HTML reading archives**. The desktop version can also export a screenshot of the current three-dimensional scene or a ZIP of screenshots from all ten chapters. Browser poster/HTML sharing and desktop scene screenshots are different outputs.

The poster/HTML sharing panel uses an anonymous summary by default. Chat text, personal information, and private images require explicit selection through the interface options. Desktop scene screenshots use the annual page’s anonymity toggle and do not automatically apply the sharing panel’s settings. Check the actual output before sharing.

## AI and speech

### AI services

Add a service under **“Settings → AI services → Model services”**, enter its endpoint and key, fetch and select a model, then save and test the connection. OpenAI-compatible and Claude Messages interfaces are supported, as are locally hosted Ollama or LM Studio services that you start yourself.

Connection tests and analysis requests may incur usage. See **“Settings → AI services → Usage records”** for call details. Costs are determined by the provider’s bill; requests that do not return usage information are marked separately. When using a remote service, relevant chat content, attachment text, or images are sent to the endpoint you configured.

### Agent

The Agent is built on **DeepAgents** and uses your selected model service. It plans steps according to the question, calls tools to find and read chat materials, and combines the evidence into an answer. Access to chat materials is restricted to the current account and authorized scope; tools are for read-only analysis.

- **Entry points and scope**: First select a connected WeChat account, then open **“AI assistant”** in the sidebar and use **“@Chats”** to select all readable chats or specific private/group chats. Each standalone AI conversation has a fixed chat scope; changing the scope starts a new conversation, and you must stop any ongoing processing first. **“Conversation”** mode in the chat page’s right-side AI panel uses the same Agent. You can start with the current chat and find other chats as needed.
- **Retrieval and analysis**: Find messages by person, conversation, date, and keyword; read long chats in pages and check surrounding context. Supported tasks include cross-chat search, event timelines, progress tracking, to-do lists, and relationship-clue analysis. When local semantic search is enabled, keyword and semantic retrieval can be combined.
- **Task planning and subtasks**: Complex questions can use independent scope analysis, topic retrieval, or fact-checking subtasks as needed, while the main Agent continues its analysis and combines the results. The interface displays actual tool calls, subtask states, and progress, and explains coverage gaps when materials have not yet been fully read or are unavailable.
- **Image and attachment understanding**: The Agent can process local images in chats and attachments such as TXT, Markdown, CSV, PDF, DOCX, XLSX, and PPTX. Attachment center OCR runs locally on the CPU or a verified AMD DirectML GPU selected in **“Settings → Local acceleration”**, without runtime model downloads or cloud calls; completed results record the actual device, and indexed OCR text can provide retrieval evidence with its original source and OCR label. Visual understanding of images, scanned PDFs, and illustrations still requires support from the selected model. A remote Agent service receives the materials needed for its answer or visual analysis. Missing, oversized, or unsupported content is reported as a processing gap.
- **Original evidence and statistics**: Answers can include citations to original chat messages. Click to preview the original text and surrounding context, then navigate to the corresponding chat. Message counts, date distributions, and speaker rankings are calculated by the program; confirmed values can be passed to a calculation tool. Model summaries and relationship inferences still need to be checked against the original messages.
- **Follow-up questions and task control**: Conversation history is retained, with search, switching, renaming, and deletion. You can add instructions or stop processing while a task is running. The user can resume the latest unfinished task, reusing its existing state and materials.

For example: “Organize the plans and to-dos agreed on in last week’s project group chat, with citations to the original messages”; “Find the time, location, and reason for rescheduling this trip in the selected chats”; or “Trace changes in interactions with a contact over the past month, distinguishing facts from inferences.”

### In-chat AI tools

The **“Tools and tasks”** menu in the chat page’s right-side AI panel provides message summaries, automatic summary tasks, follow-up alerts, and history. Scheduled tasks run while the application is running, including when it is running in the system tray.

### Experimental automatic replies

Open **“Settings → AI services → Automatic replies”**, select the data account, a fixed model, and the private/group chats that may receive replies. Add custom instructions as needed. Select the WeChat window, confirm that it is logged in to the selected data account, and choose **“Save and apply”**. Automatic replies are disabled by default; changing the chat panel's model does not change this fixed model. Relevant chat text is sent to the selected service.

- **Scope and timing**: Only new text messages observed after activation can trigger replies. Newly added groups default to mentions of you; you can change each group's trigger. The chat input toolbar can add or remove the current conversation. Clearing the scope and applying stops replies; new conversations are not included automatically. Processing depends on snapshot synchronization and runs while the application is running, including in the system tray.
- **Window and account limits**: Only one data account can run automatic replies at a time. Sending activates the bound Windows WeChat window and locates conversations by their full, exact display names. Duplicate names, changed names, and conflicting drafts can pause a conversation. This does not establish a verified live account or recipient ID binding; do not switch WeChat accounts while replies are enabled.
- **Sending and review**: “Displayed in client” means local UI evidence, not delivery or read confirmation. An uncertain submission pauses that conversation for manual review and is never automatically retried. Old batches are not resent after review or recovery. Restarting the application requires window/account confirmation and activation again.
- **History cleanup**: Delete individual completed, cancelled, failed, or interrupted records, or use **“Delete all”** to clear eligible records across every conversation and page in the current account, regardless of filters. This hides records from the history view while retaining submission and deduplication evidence. Records still processing or awaiting review remain visible; WeChat messages are not deleted or recalled.

See [Windows WeChat sending (Chinese)](docs/wechat-qt-send.md) for the window automation and confirmation boundaries. Automated checks do not establish acceptance with a real model or a complete real WeChat automatic-reply workflow.

### Local Laya profiling and automatic recognition

Open **“Chat profile”** in the chat toolbar, enter the analysis settings, select the time range, subject, and analysis method, then start the analysis. Local Laya is the default. Its approximately 681 MB model can be downloaded or imported offline and runs locally on the CPU once ready. Explicitly select the API method when you need more detailed written analysis.

Private-chat profiles analyze only the other person’s messages. For group chats, you can select the whole group or a specific member; a member profile reads only that member’s messages. Laya is a classification and statistics model. Emotion, intent, closeness tendencies, and MBTI are experimental inferences from chats and should be checked against the original messages.

**“Intent recognition”** on the chat page is a separate toggle, disabled by default. Only after explicit opt-in does it analyze loaded, paginated, and newly appearing text. Your own messages are excluded from automatic recognition and its context. Existing labels can be reused; selecting the API method sends relevant materials and may incur costs.

### Local semantic search

Under **“Settings → AI services → Local retrieval”**, download and select a retrieval model, then click **“Enable and start indexing”**. The feature is disabled by default. Once enabled, it gradually indexes all chats in the current account in the background; pausing or restarting retains progress. Chat and date conditions in a question restrict that query, without changing the enabled account-wide index scope.

Indexing and vector inference run locally. A CPU is sufficient; optional NVIDIA CUDA and AMD DirectML are managed in **“Settings → Local acceleration”**, with verification for each model and device. See the [acceleration guide and measured results (Chinese)](docs/local-acceleration.md). Disabling the feature retains the model and index; clearing the index does not delete chat records. Retrieval is limited by indexed materials and account permissions. **Local retrieval does not change the model service that the AI assistant uses to generate answers**.

### Local speech-to-text

Download and select a model under **“Settings → Speech-to-text”**, then transcribe an individual voice message or open batch transcription from the chat toolbar.

| Model | Approximate download size | Hardware and characteristics |
| --- | ---: | --- |
| Zipformer CTC | 29 MB | Default CPU model; suitable for lower-spec devices and short Chinese/English voice messages; output has no punctuation |
| Qwen3-ASR 0.6B · CPU | 2.03 GB | CPU; prioritizes Chinese transcription quality; the interface recommends 16 GB of memory |
| Qwen3-ASR 0.6B · GPU | 1.58 GB | NVIDIA GPU; requires additional Qwen GPU runtime components |
| Turbo | 1.6 GB | Supports CPU/NVIDIA GPU; prioritizes speed |

Voice messages are processed locally. Batch transcription preserves existing text and distinguishes native WeChat transcriptions from results generated by this project. Deleting this project’s transcription results retains the original voice messages, native WeChat transcriptions, and models. Qwen GPU does not automatically switch to another CPU model; refer to the Settings page’s detection results for model and device availability.

## MCP integration

Open **“Settings → MCP integration”**, copy the current address, Token, integration prompt, or Skill, and configure a client that supports HTTP MCP. A common local address is `http://127.0.0.1:10392/mcp`; requests use `Authorization: Bearer <Token>`. Use the actual address shown in Settings.

Enable **“Allow LAN access to MCP”** only when another device needs to connect, and use the LAN address provided in the interface. The Token can be reset. Do not include a real Token in public configurations or issue reports.

MCP tools provide read-only queries and do not offer WeChat sending. Media links are resource entry points; they do not mean that an external model has read the content. How external clients handle returned chat materials depends on those clients’ and model services’ configurations. The repository includes a [MCP Copilot Skill](skills/wechat-mcp-copilot/SKILL.md).

## Run from source

### Environment and startup

Use **Windows 10/11 x64, Python 3.11+, uv, and Node.js 22.12+**. The repository pins the Python development version to 3.11. Clone the repository, then install dependencies and start the application from the project root:

```powershell
git clone https://github.com/gongyuanshen/xwechat.git
Set-Location .\xwechat

uv sync --locked --extra voice-transcription
npm --prefix frontend ci
npm --prefix desktop ci
npm --prefix desktop run dev
```

`dev` starts the Nuxt development server, Electron, and the Python backend together. By default, it searches for available ports starting at 3000 for the frontend and 10392 for the backend. See the terminal output for the actual addresses.

To include Qwen GPU speech runtime components, use:

```powershell
uv sync --locked --extra voice-transcription --extra voice-transcription-gpu
npm --prefix desktop run dev:gpu
```

`dev:gpu` refers to speech runtime dependencies; it is not a graphics acceleration toggle for the three-dimensional interface. To start the backend separately, run `uv run --extra voice-transcription main.py`; the default API documentation is at `http://127.0.0.1:10392/docs`. Standalone Python uses `output` under the current working directory by default; environment variables can specify the data paths. Media processing also requires available FFmpeg and Node.js installations. Full desktop startup configures these runtime paths.

### Project structure

```text
xwechat/
├── frontend/                 # Nuxt 4 / Vue 3 interface
├── desktop/                  # Electron main process, desktop bridge, and packaging scripts
├── src/wechat_decrypt_tool/   # FastAPI, data processing, AI, media, and annual statistics
├── docs/                     # Topic guides and staged acceptance records
├── skills/                   # Companion Skills for MCP clients
├── tools/                    # Development and verification tools
├── main.py                   # Source backend entry point
├── pyproject.toml            # Python dependencies and version
└── uv.lock                   # Python dependency lockfile
```

### Common verification commands

The root `tests/` directory contains local private Python regression tests and is not included in the public source. Public users do not need it to build or run the application. `frontend/tests/` and `desktop/tests/` remain included in the public source.

The following checks can run from the public source:

```powershell
# Synthetic AI runtime checks; no real model calls
uv run --locked python tools/verify_ai_runtime.py

# Frontend and desktop automated tests
npm --prefix frontend test
node --test (Get-ChildItem -LiteralPath .\desktop\tests -Filter *.test.cjs).FullName

# Static frontend generation used by the desktop application
npm --prefix frontend run generate
```

Run Python regression tests only if you have the local root `tests/` directory:

```powershell
uv run --locked --group dev --extra voice-transcription python -m pytest tests
```

`tools/run_ai_acceptance.py` and `tools/verify_planned_work_cost.py` depend on the local root `tests/` directory and cannot complete acceptance checks in a checkout containing only the public source. References to root Python tests and historical passing results in topic guides describe local regression checks; they do not mean that those tests are included in the public repository.

Run the relevant checks for the scope of your changes. Automated tests, real model inference, real WeChat operations, and packaged application acceptance verify different layers and cannot substitute for one another.

## Windows packaging

After preparing the dependencies above, build the current Windows x64 base package with:

```powershell
npm --prefix desktop run dist
```

This command generates icons, generates and copies static frontend resources, builds the Python backend, and then packages an NSIS installer and ZIP. The base build collects CPU speech runtime components and RapidOCR 3.10.0 CPU OCR components and weights, but not the PyTorch/Transformers components required by Qwen GPU. Laya, speech, and semantic retrieval weights still require separate preparation. The v1.3.0 build includes local OCR and optional AMD acceleration support; older packages do not acquire these features automatically.

To include Qwen GPU runtime components, run these commands in order:

```powershell
npm --prefix desktop run build:icon
npm --prefix desktop run build:ui
npm --prefix desktop run build:backend:gpu
npm --prefix desktop run dist:fast
```

`dist:fast` only packages existing resources; it does not rebuild the frontend or backend. Complete the corresponding builds before first-time packaging or after source changes. Outputs are located at:

```text
desktop/dist/
├── xwechat-<version>-Setup.exe
├── xwechat-<version>-Setup.zip
└── win-unpacked/
```

CPU and GPU builds use the same output directory and filenames. Save each set of outputs separately before building the other variant. The version comes from `desktop/package.json` and must be synchronized with `pyproject.toml` before release. The current scripts disable code signing and use `--publish never`. **Packaging commands do not automatically publish to GitHub**.

Backend smoke checks during a build verify only API imports and process startup information. WXGF uses a separate decoding worker in the frozen backend and has been verified with static JPEG, transparent PNG, and multi-frame GIF outputs from the actual frozen executable. Installation, real account data, model inference, and WeChat interaction still require separate acceptance checks; successful packaging does not establish that all of them pass.

## Data directories and upgrades

| Run mode | Default data location |
| --- | --- |
| Fresh Windows installation | `%APPDATA%\xwechat`, with default output in its `output` subdirectory |
| Source Electron development build | `%APPDATA%\wechat-data-analysis-desktop`, with default output in its `output` subdirectory |
| Standalone Python backend | `output` under the current working directory |

If the installed application already has persistent state in `xwechat`, it reuses that location first. If only the old directory has persistent state, it reuses `%APPDATA%\wechat-data-analysis-desktop`. Data in both locations is not merged automatically. You can also specify an output directory in desktop settings; existing `WECHAT_TOOL_DATA_DIR`/`WECHAT_TOOL_OUTPUT_DIR` configurations remain effective. See [Desktop branding and persistent data directories (Chinese)](docs/desktop-brand-profile.md).

Before upgrading, fully exit the old application from the system tray, then install or fully extract the new version. **Keep existing user data directories and the output directory specified in Settings. Do not rename, move, or delete database, key, or snapshot directories because the application has been renamed. Existing accounts do not need to be decrypted again.**

Account archives are intended to back up databases and resources. They do not contain database keys, source paths, snapshot pointers, or all application settings. Keep keys, AI service configurations, and models separately and securely. Snapshot history consumes disk space; there is currently no fixed cumulative size limit, and actual available space is checked before building a snapshot.

## Known limitations

- **WeChat versions**: Features target Windows WeChat 4.x. Existing acceptance records with real data include version 4.1.15.13; this does not establish compatibility with every 4.x version. Key scanning, database formats, media, and window automation may all be affected by version changes.
- **Synchronization and timeliness**: Chats read decrypted, verified snapshots by default. The former real-time WCDB path has been removed. Periodic synchronization has a delay and does not provide a global cross-database transaction or instant message notifications.
- **WXGF and platforms**: Both Windows x64 source runs and the frozen desktop backend use the bundled `VoipEngine.dll` to decode WXGF in a separate subprocess. Transparent images and complete animations have been verified with controlled samples. Unknown formats, corrupt content, native crashes, and timeouts produce explicit errors. This does not establish compatibility with arbitrary WeChat media or other platforms.
- **Media and the three-dimensional interface**: Availability depends on image keys, original attachments, FFmpeg, and the graphics environment. When a video cannot be displayed, you can manually generate a local preview as prompted. The three-dimensional annual experience requires an available WebGL environment; detailed statistics and offline sharing are separate capabilities.
- **WeChat sending**: WeChat must be running, logged in, unlocked, and have an accessible window. Sending briefly activates WeChat. A send receipt does not mean that the recipient has received or read the message. If the result awaits confirmation, check WeChat first and do not repeatedly click to resend.
- **Automatic replies**: Account confirmation is supplied by the user; exact display-name routing is not recipient ID verification. Snapshot delays affect triggering. Unknown submissions remain pending review without automatic retries, and restarting does not reactivate replies.
- **Record page scope**: The mini program page displays information and does not run mini programs. The Channels page primarily shows local live-stream caches. The payments page displays records and does not execute transactions. Cached revocation candidates are not actual revocation notifications.
- **AI results and coverage**: Models can misclassify; citations and statistics should be checked against original messages. Missing attachments, insufficient index coverage, unfinished tasks, and unknown usage are separate issues and must not be treated as complete analysis.
- **Platforms and release status**: These instructions and packaging entry points target Windows x64. Experiments for other platforms and historical acceptance records in the repository do not mean this version offers the same packages and WeChat features. Older Releases and local build outputs do not update automatically when the source changes.

## Documentation

The following topic guides are in Chinese.

| Guide | Contents |
| --- | --- |
| [Source development](docs/development-windows.md) | Windows environment, data flow, snapshot synchronization, import/export, and staged acceptance |
| [Chat Agent](docs/chat-agent.md) | Standalone Agent, chat scope, tool calls, subtasks, original-message citations, and task recovery |
| [Chat AI](docs/chat-ai.md) | Summaries, automatic tasks, follow-up alerts, and desktop notifications |
| [Chat profiles and message labels](docs/chat-insights.md) | Laya/API, subject boundaries, label reuse, and quality limitations |
| [Local semantic search](docs/local-semantic-search.md) | Models, indexes, devices, downloads, and offline import |
| [Local acceleration](docs/local-acceleration.md) | CPU/CUDA/AMD DirectML settings, OCR, component isolation, and measured acceptance boundaries |
| [AI diagnostics](docs/ai-diagnostics.md) | Model calls and troubleshooting |
| [WeChat text sending](docs/wechat-qt-send.md) / [Attachment sending](docs/wechat-file-send.md) | Window adaptation, send confirmation, and attachment queues |
| [Annual frames and export](docs/wrapped-frames.md) | Desktop scene screenshots and frame processing |

Topic guides contain implementation and acceptance records from different stages. Distinguish the record date, source verification, and the actual capabilities of the corresponding packaged application when reading them.

## Report an issue

Submit reproducible problems through [GitHub Issues](https://github.com/gongyuanshen/xwechat/issues), including the application version or commit hash for source runs, Windows and WeChat versions, reproduction steps, the exact time of occurrence, and the complete error.

In the desktop application, locate the actual log through **“Settings → Desktop behavior → Log file → Open log”**. Provide complete relevant logs covering at least one minute before and after the problem. Remove keys, Tokens, chat text, and other sensitive information before uploading; do not upload WeChat databases. Redact screenshots and sample exports as well.

Code submissions should include only source relevant to the change, public frontend/desktop tests, dependency lockfiles, and necessary public resources. Keep the root `tests/` directory, databases, account keys, personal configurations, model weights, logs, caches, acceptance materials, and generated installation outputs local. `.gitignore` does not automatically remove files already tracked by Git; inspect the staging area before committing.

## Disclaimer and strict prohibition on commercial use

Before using this project, carefully read and fully understand this statement:

1. **Nature of the project**: This project is the product of personal hobbies, recreational exploration, and academic/technical research. It is **not an official product** and has no affiliation, partnership, authorization, or endorsement from WeChat’s parent company or its related entities.
2. **Commercialization and financial profit are strictly prohibited**:
   - This project is completely open source and free. **No organization, team, or individual has ever been authorized to sell it, offer paid agency services, charge group-entry fees, or provide commercial customization services**.
   - Use of all or any part of this project’s code for commercial profit or monetization is strictly prohibited.
3. **Use at your own risk**: This project is provided “As-Is,” without any express or implied warranties of continued availability, stability, or compatibility with WeChat versions. Users bear responsibility for data loss, account issues, or other losses caused by environment changes, protocol changes, or improper use.

---

## Acknowledgments and technical sources

This project relies on the pioneering work of the open-source community. Special thanks to the following foundational projects and technical contributors:

- **[LifeArchiveProject/WeChatDataAnalysis](https://github.com/LifeArchiveProject/WeChatDataAnalysis)**: The upstream basis for this project’s further development, providing the original WeChat 4.x data processing and annual analysis implementations and presentation assets.
- **[tswawa/WechatVibe](https://github.com/tswawa/WechatVibe)**: A reference for chat profiling, emotion, intent, and tentative MBTI inference from chats. This project implements its own analysis services, prompts, and interface, and integrates the local Laya workflow.
- **[mizchi/laya-mlx](https://github.com/mizchi/laya-mlx)**: One source for local Laya ONNX input construction and calibration logic. Relevant sources and licenses are retained in the [local license directory](src/wechat_decrypt_tool/resources/licenses).
- **[hicccc77/WeFlow](https://github.com/hicccc77/WeFlow)**: A reference for local chat export, media processing, annual reports, and related implementations.
- **[vuepont/ai-elements-vue](https://github.com/vuepont/ai-elements-vue)**: Source components included as needed for the AI conversation interface. See the [component notes](frontend/components/ai-elements/README.md) for the source, pinned commit, and license.
- **[hicccc77/Relink](https://github.com/hicccc77/Relink)**: An Agent implementation for in-depth relationship analysis.

The technology stack includes Nuxt 4, Vue 3, Tailwind CSS 4, Three.js, Electron, FastAPI, SQLite, and DeepAgents. Licenses and source notices for third-party code, resources, models, and runtime components must be preserved and are not changed by this project’s usage statement.
