# Matter Status Panel

Matters are stored as `matter` records in the existing AIStore and isolated by WeChat account. This feature does not call a model or automatically infer relationships between later messages and existing matters.

## Usage

1. In an original chat message's menu, select "Save Matter / Update Deadline", or select "Save Matter" next to a to-do in an AI summary. The dialog rereads and displays the local original message, then saves the title, source, and optional deadline after confirmation.
2. In the "Matter Status Panel", switch between To Do, In Progress, and Completed, and inspect the original text and complete change history. The user explicitly selects the deadline; text such as "Friday" is not automatically interpreted as a date.
3. When a rescheduling message arrives, open the same dialog from that message's menu, select "Use as Rescheduling Evidence for an Existing Matter", choose the matter, and confirm the new deadline. The previous deadline, rescheduling time, and message evidence are retained. Completed matters must be reopened before they can be rescheduled.
4. For a matter without a deadline, use "Confirm Deadline from This Original Text" in the panel to set one. Due reminders are off by default and must be enabled by the user.

## Reminder and Data Contracts

- The feature reuses the existing AIService scheduler and desktop notification channel. Each reminder revision is queued only once; the notification body contains neither the matter title nor original chat text.
- Completing a matter, disabling reminders, or confirming a new deadline transactionally deletes that matter's old, undelivered notifications. Notifications already handed to the operating system cannot be recalled.
- Due matters are processed while the application runs; no notifications are sent while it is closed. On reopening, overdue matters that remain incomplete and have not yet been notified are processed. The standalone web interface can manage matters; system notifications require the desktop client.
- Every write carries the matter revision number. Concurrent or stale operations return 409 and require review and confirmation again, without overwriting newer state.
- Original text is checked again on submission. If it changes after being displayed, the operation returns 409. The existing account work lease protects account deletion against original-text reads and saves.
- Change history and original text stay local and are not deleted with the original summary task. Account deletion uses AIStore's existing whole-account cleanup.

## API

The endpoint prefix is `/api/matters`. Existing local-machine access checks apply, and all endpoints require the `account` query parameter.

| Method and path | Purpose |
| --- | --- |
| `GET /api/matters` | All matters and their history for the current account |
| `POST /api/matters/source` | Validate message identity and return original text |
| `POST /api/matters` | Create a matter from original text, with an optional deadline |
| `PATCH /api/matters/{id}` | Update the title, status, and reminder switch using revision |
| `POST /api/matters/{id}/reschedule` | Confirm rescheduling with `confirmed: true`, the new deadline, revision, and message evidence |

Deadlines are stored as Unix seconds; the interface uses the system's local time zone for input and display. Ordinary PATCH requests do not accept deadline fields, preventing bypass of original-text confirmation.

## Verification Scope

Local automation covers source consistency, account isolation, concurrent revision conflicts, due-reminder deduplication, cancellation of old notifications after completion/rescheduling, private notification bodies, stale frontend responses, confirmation interactions, and desktop notification target validation. This scope does not establish real WeChat, real model, or package acceptance; the matter feature itself requires no model inference. Actual desktop system notification display and clicking still require acceptance in a running client.
