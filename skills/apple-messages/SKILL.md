---
name: apple-messages
description: Use when the user asks to inspect local message history, find a text thread, or send a specific one-to-one message from this Mac. It supplies bounded Messages history reads and guarded Messages.app sends. Do not use for group sends, history mutation, or messages unavailable in the local store.
---

# Apple Messages

Run `$RUNDESK_SKILLS/apple-messages/scripts/apple-messages`. Reads need Full Disk Access for the
agent host; sends need Messages Automation permission. Restart the agent after changing Full Disk
Access. Read `references/cli.md` only for permission setup, attachment access, transport behavior,
or validation.

Start with access checks:

```sh
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" read status
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" send status
```

Bounded reads by default:

```sh
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" read chats --limit 25
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" read unread --limit 25
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" read needs-reply --days 14 --limit 25
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" read show --chat-id <CHAT_ID> --limit 20
"$RUNDESK_SKILLS/apple-messages/scripts/apple-messages" read search '<term>' --days 30
```

Reads never mark messages read. Sends are dry-runs without `--confirm` and support
one-to-one only; group sends are unsupported. Prefer `--chat-id` after reviewing a
thread. Never confirm a send unless the owner approved the exact recipient and body.
