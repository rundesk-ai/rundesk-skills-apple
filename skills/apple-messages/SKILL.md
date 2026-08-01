---
name: apple-messages
description: Read Apple Messages history or preview guarded one-to-one sends with the bundled CLI. Use for Messages.app, iMessage, SMS, RCS, chats, unread texts, or local message history.
---

# Apple Messages

Run the bundled CLI at `$RUNDESK_SKILLS/apple-messages/scripts/apple-messages`. Reads open
`~/Library/Messages/chat.db` read-only; sends use Messages.app AppleScript. Full Disk
Access for the process serving the Rundesk agent and Automation permission may be required.
Restart the agent after changing Full Disk Access. Read `references/cli.md` only for setup or
validation details.

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
