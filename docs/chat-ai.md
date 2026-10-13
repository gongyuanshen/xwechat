# Chat AI Summaries and Attention Alerts

For cross-platform runtime policy, physical Mac results, and uncovered items, see the historical macOS AI compatibility and acceptance record (not distributed with the public source).

## Usage

1. Open "Settings → AI services", select a provider, and enter its address and key. Leaving the input field automatically fetches models from the provider; click "Fetch from provider" to refresh. Search or use the dropdown to select a model and save. You do not need to enter a model name first. Providers prefill only the address and protocol, without a built-in model list.
2. Opening an existing configuration automatically refreshes the model list while retaining the selected model; if it is absent, the UI asks you to select again. New configurations prefer a model whose image capability is explicitly declared by the provider, otherwise the first returned model. If the provider supplies no capability information, manually confirm image capability and test the connection. After saving a service, select the service, model, and supported thinking settings below the AI conversation input. Connection tests make inference calls; fetching the list does not.
3. Open a group or private chat and click AI in the upper-right corner. Select one or more conversations and a recent-message count, past-hour count, or start/end time, then start the summary.
4. Under "Automatic tasks", create interval, daily schedule, or new-message-count rules. Under "Attention alerts", enter natural-language conditions and select a detection frequency. Background execution starts only after enabling the rule.
5. View results and match reasons in "History", and click a source to locate its original message. Rules and tasks stay on the local machine; navigation and minimizing to the tray do not interrupt execution.

AI conversation history shows saved conversations for the current chat. Open the list from the history button at the top left or "More → Conversation history". After clicking "New conversation", existing answers remain accessible from the list; an empty conversation is created when its first question is sent. Switching chats updates the history list to that chat's AI conversations.

"AI suggestion" in the chat input uses the service, model, and thinking settings currently selected in the AI conversation above. It drafts a short reply from the current conversation's latest 10 messages and inserts it into the draft. The request carries the model selection from the moment of the click, so even an unsaved selection cannot accidentally use the previous model. Clients that omit a selection use the saved global selection. A WeChat message is sent only after the user reviews the draft and clicks "Send". Deleting the selected service requires selecting again; it does not automatically switch to an old default service. An unavailable model configuration returns 422 with its specific reason; a program error while reading configuration returns `AI_MODEL_RESOLVE_FAILED` (500), with the exception stack retained in backend logs.

Cloud services receive the selected messages and media used in analysis. A compatible local service can also be used. Keys are managed only by the backend, are not returned to the browser, and do not enter model-workflow checkpoints. External LangSmith tracing is disabled by default.

If a provider does not support model lists or temporarily cannot connect, retry and "Manual input (alternative)" are available. After changing a service address or protocol, reenter the key before fetching models; a saved key is not automatically sent to the new address.

### AI Service Presets

"Add service" offers 14 presets: DeepSeek, Claude, Kimi, OpenAI, Google Gemini, Qwen (Alibaba Cloud Model Studio), Zhipu GLM, Doubao (Volcengine Ark), SiliconFlow, OpenRouter, Groq, Ollama, LM Studio, and Custom. Search by name or alias; example Chinese aliases are `\u8c37\u6b4c` (Google), `\u5343\u95ee` (Qwen), `GLM`, and `\u706b\u5c71` (Volcengine). Custom is last in the list.

Presets fill the configuration name, API address, and protocol; the address remains editable. Chinese platforms default to their general domestic API addresses rather than Coding Plan endpoints. Gemini uses the `/v1beta/openai` compatibility endpoint. Switching presets clears the key, model selection, and image capability, requiring reconfirmation; it does not reuse the previous service's key.

Before selecting Ollama or LM Studio, start its local service and prepare a model. Their default addresses are `http://127.0.0.1:11434/v1` and `http://127.0.0.1:1234/v1`, respectively. Keys may be empty when authentication is disabled, and models are fetched automatically after selection. Enter a key if authentication is enabled, and change the address if the port differs. If the connection fails, check the service, address, port, and authentication settings, then click "Fetch from provider". You can also enable "Manual input (alternative)", enter the actual model name, and save; saving a configuration does not establish that its connection has been verified.

## Content and Runtime Boundaries

- Supports text, quotes, card titles, existing voice transcripts, JPEG/PNG/WebP/GIF (first frame), PDF (including scanned pages), DOCX, XLSX, PPTX, TXT, MD, and CSV.
- Automatic transcription of new voice messages, video understanding, legacy Office files, and archives are outside this version's scope.
- Attachments must be readable locally; missing, corrupted, encrypted, and oversized files show processing status. The default per-attachment limit is 20 MB, adjustable to 200 MB; total Office decompression is limited to 512 MB.
- Long material is processed in segments without silent truncation. Images are scaled to a longest edge of 1600 pixels before submission to the vision model.
- Imported snapshots support summaries of existing messages; live attention monitoring requires an available live data source.
- Detection defaults to once per minute, with 10-second, 5-minute, and custom intervals available. No new messages means no model call; the new-message threshold counts all conversations in the rule together.
- New rules establish their monitoring baseline when enabled. Fully exiting the application stops execution; restarting restores checkpoints and combines missed scheduled executions into one.
- Failed automatic tasks retry with backoff, and 3 consecutive failures pause the corresponding rule. Authentication errors pause it immediately. Failed conversations do not advance successful checkpoints.
- Manual retries retain the original message time range, resolve the current model configuration again, and record its version. Configuration changes clear old-model segment results; newly available attachments also trigger reanalysis of changed content.
- Desktop notifications show the conversation name and alert reason by default, with options to disable them or hide content. Results remain stored in the application if operating-system notifications are disabled.

## Development and Verification

Shared backend capabilities are in `src/wechat_decrypt_tool/ai/`, with endpoints in `routers/ai.py`. LangChain adapts models, LangGraph orchestrates tasks, and SQLite stores business records and checkpoints. New features should reference global configuration through the shared model service.

Model discovery follows [Codex++ model catalog fetching](https://github.com/BigPizzaV3/CodexPlusPlus/blob/48d43158688f5096c7059c690f8cd1daab340681/crates/codex-plus-core/src/model_catalog.rs): normalize endpoint addresses, handle arrays and `data/models/items` wrappers, accept `id/model/name` identifiers, and deduplicate. This project additionally handles Claude pagination and image-capability metadata without guessing capabilities from model names. Credential changes clear the previous list, and late responses are ignored.

Install runtime dependencies with `uv sync --locked --extra voice-transcription`. The frontend requires a Node version meeting current Nuxt/Vite requirements; Node 18 cannot build the current dependencies. Further installer and portable-archive releases are paused. For the current runtime method, see [Windows Source Development](development-windows.md).

Windows source startup uses only the project's independent data implementation and does not download or load the old client/broker. Install npm dependencies in `desktop`, then run `node scripts/dev.cjs` with Node 22.12 or later; the Nuxt child process uses the same Node. Runtime verification after the legacy-mode removal had not yet been performed in the historical 2026-10-04 cleanup record.

Model tests can use simulated services without real keys. Root-level `tests/` contains local private Python regression tests and is not distributed with the public source; it is not needed to build or run the application.

Public source checks (run frontend commands in `frontend`, and desktop commands at the project root):

```text
npx vitest run tests/ai-panel.test.js
node --test desktop/tests/ai-notifications.test.cjs
```

Local Python regressions (require root-level `tests/`; run at the project root):

```text
python -m pytest tests/test_ai_services.py tests/test_ai_api.py
```

`desktop/scripts/smoke-ai-notifications.cjs` runs through Electron and verifies native notifications with synthetic messages, writing results to `logs/ai-notification-smoke.json`. It does not read WeChat data or call a model.

## Usage Audit

Settings → AI services shows per-call records with call time, model and configuration version, account, task ID, retry count, image count, input/output tokens, and status. Loaded records can be exported as JSON. Each retry is counted separately, and response-format validation failures still record usage returned by the provider. Authentication failures, timeouts, or cancellations without returned usage show "Unknown", which does not mean free. Records unfinished at forced exit show "Running / unfinished after interruption". Provider billing is authoritative.

The audit does not save keys, prompts, chat bodies, or raw provider exceptions. Fields unavailable in older records show unknown. Metadata endpoints, such as model-list fetching, do not count as model inference calls.
