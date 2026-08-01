---
name: apple-messages
description: Reading local Apple Messages data and guarded one-to-one sends.
category: local
---

# apple-messages

## Entry Point

- Verify read access: `apple-messages read status`
- List chats: `apple-messages read chats`
- Show one chat: `apple-messages read show --chat-id CHAT_ID`
- Search messages: `apple-messages read search "invoice"`
- List unread chats: `apple-messages read unread`
- List likely replies needed: `apple-messages read needs-reply`
- Show attachment metadata: `apple-messages read attachments --message-id MESSAGE_ID`
- Export bounded JSON: `apple-messages read export --days 7 --json`
- Verify send scripting access: `apple-messages send status`
- Dry-run a send: `apple-messages send send --chat-id CHAT_ID --body "Message"`
- Actually send a reviewed message: `apple-messages send send --chat-id CHAT_ID --body "Message" --confirm`

The same-name dispatcher also works:

```bash
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" read chats
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" send send --chat-id CHAT_ID --body "Message"
```

Read commands open `~/Library/Messages/chat.db` in read-only mode. Send commands use Messages.app AppleScript, are dry-runs unless `--confirm` is passed, and support one-to-one sends only. RCS can be read as an observed message service; sending cannot force RCS directly and maps through Messages.app's SMS AppleScript service path.

Message rows with attachments include an exact `attachment_command=...` value, including the active `--db` path. The attachment command prints `local_path`, `file_exists`, `trusted_messages_attachment`, and `access` so agents can inspect locally stored Messages attachment files without guessing where Messages keeps them. Attachment bytes are not printed to stdout.

## Validation

- Run `python3 $RUNDESK_SKILLS/apple-messages/scripts/apple-messages.d/test-apple-messages.py` for offline synthetic database and mocked-send tests.
- Run `apple-messages read status` as a live read-only smoke test.
- Run `apple-messages send status` as a live non-send AppleScript smoke test.

## Provider

This integration is self-contained: its provider contract lives in this reference, not in a separate file or shared folder. It treats local macOS Messages data as the source of truth and sends one-to-one messages through Messages.app.

The read CLI treats `~/Library/Messages/chat.db` as the local source of truth for Messages channel state. The send CLI uses Messages.app AppleScript so macOS and Messages own delivery, account selection, and iCloud consistency. Direct SQLite writes are forbidden.

### Safety Rule

Read commands may inspect local chat, message, unread, needs-reply, attachment metadata, and schema context. They must open the database read-only and must not mark conversations read.

Send commands are dry-runs unless `--confirm` is passed after the owner has explicitly approved the exact recipient, transport behavior, and body. Sending by `--chat-id` is allowed only for one-to-one chats. Group sends, historical message edits, deletes, archive changes, and mark-read mutations are unsupported in v1.

### Setup

Messages must already be signed in on the Mac. The terminal or Codex host app may need macOS privacy permissions:

- Full Disk Access for the process that serves the Rundesk agent, not only an interactive terminal,
  for direct reads under `~/Library/Messages/`. Restart the agent after changing this grant.
- Automation permission to control Messages.app for send status checks and confirmed sends.

Verify read access:

```bash
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" read status
```

Verify non-send Messages.app scripting access:

```bash
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" send status
```

### Read Workflow

Use compact text by default:

```bash
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" read chats --limit 25
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" read unread --limit 25
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" read needs-reply --days 14 --limit 25
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" read show --chat-id 123 --limit 20
```

Use JSON when another script or agent needs structured payloads:

```bash
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" read show --chat-id 123 --json
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" read export --days 7 --json
```

Attachment commands expose metadata only. They do not read or print attachment file contents.

When a message has attachments, text rows include an `attachment_command` field with the exact follow-up command to run, including the active `--db` path. The attachment command includes `local_path`, `file_exists`, `trusted_messages_attachment`, and `access` fields. If `access=read-local-file`, agents may inspect that local file path with normal filesystem/image/PDF/document tools after the owner asks for the attachment contents. If `access=missing-local-file`, Messages knows about the attachment but the file is not present locally. If `access=untrusted-local-file`, the row points at an existing file outside Messages' attachment root and agents must not treat it as a Messages attachment without separate owner confirmation.

### Send Workflow

Dry-run first:

```bash
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" send send --chat-id 123 --body "On my way"
```

Actually send only after the owner explicitly asks for that exact message:

```bash
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" send send --chat-id 123 --body "On my way" --confirm
```

Prefer `--chat-id` after reviewing a thread. The script inspects recent message rows and chooses the safest AppleScript service path. Use `--to` only when no chat ID exists.

### RCS And SMS

Messages SQLite can show `message.service = RCS`, and read commands preserve that value. AppleScript does not expose a direct RCS service selector. When requested or recent service is RCS, the send CLI uses Messages.app's SMS service path and prints a caveat that Messages.app chooses SMS/RCS availability.

### Notes

- The database contains personal communications. Do not commit command output, handles, message text, attachment names, or local IDs from real data.
- `unread` is based on incoming rows where `is_from_me = 0` and `is_read = 0`; treat it as a useful local hint.
- `needs-reply` is heuristic: it lists chats where the latest scanned non-empty message is incoming.
