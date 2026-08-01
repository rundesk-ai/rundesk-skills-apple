---
name: apple-mail
description: Reading Mail.app data and safely creating drafts or sending mail through an explicit local account allowlist.
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
- Dry-run a draft: `apple-mail write draft --payload email.json`
- Save an approved draft: `apple-mail write draft --payload email.json --confirm ONE_TIME_TOKEN`
- Dry-run a send: `apple-mail write send --payload email.json`
- Send an approved email: `apple-mail write send --payload email.json --confirm ONE_TIME_TOKEN`

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

## Provider

This integration is self-contained: its provider contract lives in this reference. It reads the signed-in Mac's Mail.app accounts through Apple's scripting interface. Mail.app owns account configuration, local caching, remote sync, and message state.

Direct reads from Mail's private SQLite databases are intentionally excluded. Their schema and account mapping change across macOS releases and make account-boundary enforcement brittle. The bridge always starts from one exact allowed account, traverses only that account's mailbox tree, and never uses Mail's unified inbox, selected messages, message viewers, or smart mailboxes.

### Safety Rule

Read commands must not send, reply, forward, mark read, flag, move, delete, archive, download attachments, trigger new-mail checks, or change accounts or mailboxes. Message-list and search output includes only matched-message headers plus a whitespace-normalized body preview, 160 characters by default and capped at 500. Use `--preview-chars 0` to omit previews. The `show` command requires one exact `--account-id` before content is read. It retrieves one matching message body, 4,000 characters by default and capped at 20,000, plus useful headers and attachment metadata; it does not save attachment bytes.

Draft creation and sending are dry-runs unless `--confirm ONE_TIME_TOKEN` is passed after the owner has explicitly approved the exact account/from address, recipients, subject, body, and whether the action is draft or send. A dry-run records an owner-only, 15-minute confirmation challenge tied to the hash of every one of those fields. Its token is consumed before Mail is invoked and cannot be replayed. Approval to create a draft is not approval to send it. The write tool does not support attachments, replies, forwarding, or bulk mail in v1.

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
    "body": "Example body"
  }
}
```

The `from` address must belong to the exact allowed account. At least one recipient is required, and the body is capped at 100,000 characters. Dry-run first and review the printed sender, recipients, subject, body preview, body length, and body hash. Run with `--confirm` only after the owner approves that exact action:

```bash
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write draft --payload email.json
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write draft --payload email.json --confirm ONE_TIME_TOKEN

"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write send --payload email.json
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write send --payload email.json --confirm ONE_TIME_TOKEN
```

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
- Attachment metadata reports whether Mail says an attachment is downloaded, but v1 neither reads nor saves attachment bytes.
- A send timeout or malformed automation response is indeterminate: check Sent and Outbox before approving a retry. For draft failures, check Drafts first.
