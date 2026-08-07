---
name: apple-mail
description: On-demand command, allowlist, confirmation, scheduled-delivery, and validation details for Apple Mail.
category: local
---

# apple-mail

## Entry Point

- Verify Mail.app automation and allowlist status: `apple-mail setup status`
- Discover configured accounts: `apple-mail setup accounts`
- Dry-run allowing an account: `apple-mail setup allow --account-id ACCOUNT_ID`
- Save an approved account allowance: `apple-mail setup allow --account-id ACCOUNT_ID --confirm`
- Verify read access: `apple-mail read status`
- List allowed accounts: `apple-mail read accounts`
- List mailboxes: `apple-mail read mailboxes`
- Show recent inbox messages as compact text: `apple-mail read inbox --limit 25`
- Show recent inbox messages as CSV: `apple-mail read inbox --limit 25 --format csv`
- Show unread inbox messages: `apple-mail read unread --limit 25`
- Search sender and subject metadata: `apple-mail read search "invoice" --days 30`
- Show one message: `apple-mail read show --account-id ACCOUNT_ID --mailbox INBOX --message-id MESSAGE_ID`
- Verify draft/send access: `apple-mail write status`
- Dry-run a draft, with optional local file attachments in the payload: `apple-mail write draft --payload email.json`
- Save an approved draft: `apple-mail write draft --payload email.json --confirm ONE_TIME_TOKEN`
- Dry-run a send: `apple-mail write send --payload email.json`
- Send an approved email: `apple-mail write send --payload email.json --confirm ONE_TIME_TOKEN`
- Dry-run a later send: `apple-mail write schedule --payload email.json --at 2026-08-05T09:00:00-04:00`
- Queue an approved later send: `apple-mail write schedule --payload email.json --at 2026-08-05T09:00:00-04:00 --confirm ONE_TIME_TOKEN`
- List the queue: `apple-mail write scheduled --pending`
- Dry-run a cancellation: `apple-mail write cancel --id SCHEDULE_ID`
- Cancel an approved queue entry: `apple-mail write cancel --id SCHEDULE_ID --confirm ONE_TIME_TOKEN`
- Report what a timer would deliver now: `apple-mail write run-due --dry-run`
- Deliver every due queue entry: `apple-mail write run-due`

The same-name dispatcher also works:

```bash
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" setup accounts
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read unread --limit 25
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write send --payload email.json
```

All message and mailbox commands are restricted to account IDs in
`${XDG_CONFIG_HOME:-$HOME/.config}/rundesk/integrations/apple-mail/accounts.json`.
The legacy `~/.config/workspace/apple-mail.json` remains readable only when the current
path does not exist. The config is created with owner-only permissions. Account discovery
is the only command that can show accounts before they are allowed, so the owner can review
stable Mail account IDs. Allow and revoke commands are dry-runs unless `--confirm` is passed
after approval of the exact IDs.

## Validation

- Run `python3 $RUNDESK_SKILLS/apple-mail/scripts/apple-mail.d/test-apple-mail.py` for offline tests with mocked Mail.app responses and temporary allowlists.
- Run `osascript -l JavaScript $RUNDESK_SKILLS/apple-mail/scripts/apple-mail.d/AppleMailBridge.js accounts` as a live automation smoke test.
- Run `apple-mail setup status` as a live setup smoke test.
- After at least one account is allowed, run `apple-mail read status` and `apple-mail read unread --limit 5` as live read smoke tests.
- Run `apple-mail write status` as a live non-mutating allowed-sender validation smoke test.
- Run `apple-mail write scheduled` and `apple-mail write run-due --dry-run` as live non-mutating queue smoke tests.

## Provider

Mail.app owns account configuration, local caching, remote sync, and message state.

Direct reads from Mail's private SQLite databases are intentionally excluded. Their schema and account mapping change across macOS releases and make account-boundary enforcement brittle. The bridge always starts from one exact allowed account, traverses only that account's mailbox tree, and never uses Mail's unified inbox, selected messages, message viewers, or smart mailboxes.

### Safety Rule

Read commands must not send, reply, forward, mark read, flag, move, delete, archive, download attachments, trigger new-mail checks, or change accounts or mailboxes. Message-list and search output includes only matched-message headers plus a whitespace-normalized body preview, 160 characters by default and capped at 500. Use `--preview-chars 0` to omit previews. The `show` command requires one exact `--account-id` before content is read. It retrieves one matching message body, 4,000 characters by default and capped at 20,000, plus useful headers and attachment metadata; it does not save attachment bytes.

Draft creation, sending, scheduling, and cancelling a scheduled send are dry-runs unless `--confirm ONE_TIME_TOKEN` is passed after the owner has explicitly approved the exact account/from address, recipients, subject, body, attachments, and whether the action is draft, send, or schedule. A dry-run records an owner-only, 15-minute confirmation challenge tied to the hash of every one of those fields, including each attachment's resolved path, byte size, and content hash; a schedule additionally binds the exact send time and expiry window, so moving the time invalidates the approval. Its token is consumed before Mail or the queue is touched and cannot be replayed. Approval to create a draft is not approval to send it, and approval to send now is not approval to schedule. The write tool does not support replies, forwarding, or bulk mail.

`run-due` is the one write command that acts without `--confirm`, because it must run unattended from a timer. Its authority comes entirely from the queue: every entry was already confirmed by a one-time token and carries the approval hash of that exact message and time, which is recomputed and compared before Mail is invoked. An entry whose stored message, attachment bytes, account allowance, or sender mapping changed after approval is failed, never sent. `run-due --dry-run` reports what a timer would deliver without claiming or sending anything.

Attachments are read from local files the owner names. Because a file's bytes are hashed into the confirmation challenge, replacing an attachment between the dry-run and the confirm invalidates the token instead of silently sending different content. Never attach a file the owner did not name.

Every mailbox or message read requires a nonempty local allowlist. `--account-id` can narrow the configured allowlist but can never override it. If Mail recreates an account with a new ID, that account must be approved again.

### Setup

Mail must already be configured on the Mac. The terminal or Codex host app needs macOS Automation permission to control Mail.app. Full Disk Access is not required because this integration does not open Mail's private database.

Review configured accounts:

```bash
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" setup accounts
```

Dry-run and then save the exact account IDs the owner approves:

```bash
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" setup allow --account-id ACCOUNT_ID
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" setup allow --account-id ACCOUNT_ID --confirm
```

Revoke access with the same guard:

```bash
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" setup revoke --account-id ACCOUNT_ID
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" setup revoke --account-id ACCOUNT_ID --confirm
```

Use `--config PATH` only for testing or an explicitly chosen alternate local allowlist.

### Draft And Send Workflow

Write commands accept an email object directly or under an `email` key:

```json
{
  "email": {
    "account_id": "ACCOUNT_ID",
    "from": "sender@example.test",
    "to": ["recipient@example.test"],
    "cc": [],
    "bcc": [],
    "subject": "Example subject",
    "body": "Example body",
    "attachments": ["/absolute/path/report.pdf"]
  }
}
```

The `from` address must belong to the exact allowed account. At least one recipient is required, and the body is capped at 100,000 characters. `attachments` is optional and holds local file paths; `~` is expanded and symlinks are resolved before use. Each path must resolve to an existing regular file, at most 10 files are accepted, and no single file or total payload may exceed 25 MB. Dry-run first and review the printed sender, recipients, subject, body preview, body length, body hash, and one `attachment[N]=` line per file with its name, byte size, content hash, and resolved path. Run with `--confirm` only after the owner approves that exact action:

```bash
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write draft --payload email.json
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write draft --payload email.json --confirm ONE_TIME_TOKEN

"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write send --payload email.json
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write send --payload email.json --confirm ONE_TIME_TOKEN
```

### Scheduled Send Workflow

Mail.app's own send-later is a user-interface feature. Its scripting dictionary exposes no deferred send date: an `outgoing message` carries only sender, subject, content, visibility, signature, and id, and responds only to `save`, `close`, and `send`. A scheduled send here is therefore a local queue entry plus a runner, not a Mail feature, and a queued message is not a Mail draft — it never appears in Drafts and editing Mail does not change what will be sent.

`schedule` takes the same payload as `draft` and `send` plus `--at`, and follows the same dry-run and one-time token flow:

```bash
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write schedule --payload email.json --at 2026-08-05T09:00:00-04:00
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write schedule --payload email.json --at 2026-08-05T09:00:00-04:00 --confirm ONE_TIME_TOKEN
```

`--at` is ISO 8601. A value without a UTC offset is read in the machine's local time zone; `Z` and explicit offsets are accepted. The time must be in the future and within 365 days. The dry-run prints the resolved time in both UTC and local time so the owner approves an unambiguous instant. `--expire-after-minutes` defaults to 1,440 and is capped at 43,200: a send more than that many minutes overdue when the runner finally fires is marked `expired` rather than delivered, so a sleeping Mac never sends yesterday's message today.

Inspect and cancel through the printed schedule id:

```bash
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write scheduled
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write scheduled --pending --json
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write cancel --id SCHEDULE_ID
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write cancel --id SCHEDULE_ID --confirm ONE_TIME_TOKEN
```

Listing shows locators, status, both times, sender, recipients, subject, attachment count, and any error; it never prints the body. An entry moves `pending` to `sending` to exactly one of `sent`, `failed`, `expired`, or `cancelled`, and only `pending` entries can be cancelled. Terminal entries are retained for seven days as an audit trail, then pruned. At most 200 entries may be pending or in flight at once, and one `run-due` invocation delivers at most 25.

`run-due` is what actually delivers, and nothing is delivered until the owner wires it to a timer. It claims due entries under a lock before invoking Mail, so a concurrent invocation cannot double-send, and it never retries on its own: an entry left `sending` by a crash or a killed process is reported as `indeterminate` and left for the owner to resolve against Sent and Outbox.

```bash
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write run-due --dry-run
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write run-due
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write run-due --json
```

A Rundesk schedule calls `run-due` on a clock, and it is an owner decision to add because it grants unattended Mail delivery:

```bash
"$RUNDESK_COMMAND" schedules add "$RUNDESK_AGENT" apple-mail-outbox \
  --when "*/5 * * * *" \
  --run "'$RUNDESK_SKILLS/apple-mail/scripts/apple-mail' write run-due"
```

`--run` takes the complete program and arguments as one string, starts no turn, and asks no model.
The launcher path is absolute because a gateway runs with almost no `PATH` and refuses a bare name.

- **The cron interval is the delivery resolution.** `*/5` lands a send within five minutes of its time, and stays well inside `--expire-after-minutes`; an interval wider than that window expires every entry unsent.
- **One schedule serves the machine.** The queue is a single file for the whole machine, so whichever agent's schedule fires delivers every agent's mail, and a second schedule delivers the same mail no faster — `run-due` claims entries under an exclusive lock. Name it for the outbox rather than for the agent.
- **The agent named owns the schedule record, not the queue.** Removing that agent removes the schedule and leaves every pending entry undelivered, so read `write scheduled --pending` first.

`"$RUNDESK_COMMAND" schedules list "$RUNDESK_AGENT"` lists it. After checking pending mail,
`"$RUNDESK_COMMAND" schedules remove "$RUNDESK_AGENT" apple-mail-outbox` removes it.

The queue lives beside the account allowlist at `${XDG_CONFIG_HOME:-$HOME/.config}/rundesk/integrations/apple-mail/scheduled.json`, owner-only, atomically replaced, and lock-guarded, and `--schedule-store PATH` or `APPLE_MAIL_SCHEDULE_STORE` names an alternate file. It holds full message bodies and attachment paths until delivery, so it is private data and is never committed, copied, or printed wholesale.

### Read Workflow

Use compact one-line text by default. Each row includes timestamp, account name and stable account ID, mailbox, message ID, unread state, sender and recipient addresses, subject, and a short body preview. The `account_id`, `mailbox`, and `id` values round-trip directly into `show`:

```bash
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read mailboxes --account-id ACCOUNT_ID
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read inbox --account-id ACCOUNT_ID --limit 25
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read unread --account-id ACCOUNT_ID --limit 25
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read search "planning" --account-id ACCOUNT_ID --days 14
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read show --account-id ACCOUNT_ID --mailbox MAILBOX --message-id ID
```

Use CSV for a machine-friendly flat list, or adjust/disable the preview budget:

```bash
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read inbox --days 7 --limit 25 --format csv
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read unread --preview-chars 100
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read search "planning" --preview-chars 0
```

Use JSON when another script or agent needs structured data:

```bash
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read unread --limit 25 --json
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read show --account-id ACCOUNT_ID --mailbox INBOX --message-id MESSAGE_ID --json
```

The single-message text view is optimized for reading and uses a header block followed by the bounded body. Increase its body budget only when needed:

```bash
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read show --account-id ACCOUNT_ID --mailbox INBOX --message-id MESSAGE_ID
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read show --account-id ACCOUNT_ID --mailbox INBOX --message-id MESSAGE_ID --body-chars 12000
```

Mailbox paths are exact paths returned by `mailboxes`; each segment is URL-encoded so names containing `/` or formula-leading punctuation round-trip safely. Locator fields (`account_id`, `mailbox`, and `id`) are emitted unchanged so they can be passed directly to `show`. Message scans default to the first 250 items in Mail's local mailbox order and are capped at 2,000 with `--scan-limit`; returned results are sorted newest-first and capped at 500 with `--limit`. Multi-account lists select the global result set from metadata first, then fetch previews only for rows that will be returned. Search matches sender and subject metadata, not message bodies. Compact text bounds recipients to three addresses plus a remainder count; non-locator CSV string cells are neutralized against spreadsheet formulas. Single-message details cap recipient metadata at 50 entries per field and attachment metadata at 20 entries, reporting omitted counts.

### Notes

- Reads may launch Mail.app. List/search commands may lazily fetch content for the globally selected preview rows; use `--preview-chars 0` for metadata-only queries. An explicit `show` may lazily fetch the selected full message content.
- Reading through Apple automation must not mark messages read; the bridge never assigns to `read status` or any other Mail property.
- Account names, addresses, message metadata, bodies, and identifiers are private data. Do not commit live output, local allowlist contents, or generated caches.
- Only the Python entry points are supported. The JXA bridge files remain privileged internal helpers and must never
  be invoked directly for mailbox reads, drafts, or sends.
- Attachment metadata on read reports whether Mail says an incoming attachment is downloaded, but reads never save attachment bytes. Outgoing attachments are a separate write-side feature.
- Mail accepts an outgoing attachment path without checking it and does not expose the attachment list of an unsaved outgoing message, so the existence, regular-file, size, and hash checks in `apple-mail-write.py` are the only guard. Mail decides where each file lands in the message, so attachment order is not preserved.
- A send timeout or malformed automation response is indeterminate: check Sent and Outbox before approving a retry. For draft failures, check Drafts first.
- A scheduled send is only as reliable as its timer, and an unwired queue delivers nothing. Confirm `run-due` is wired before reporting mail as scheduled, and read `write scheduled` rather than assuming a queued entry was delivered.
- A scheduled entry stores the attachment path, not the bytes. Moving, editing, or deleting the file after approval fails that entry instead of sending different content than was approved.
