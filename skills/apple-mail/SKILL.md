---
name: apple-mail
description: Use when the user asks to inspect Mail.app mail, prepare a draft, send approved email, or queue an approved message for later delivery from an allowed local account. It supplies bounded allowlisted reads and hash-bound guarded writes. Do not use for replies, forwarding, non-Mail providers, or unattended delivery unless the owner approves its timer.
---

# Apple Mail

Run the bundled CLI at `$RUNDESK_SKILLS/apple-mail/scripts/apple-mail`. It controls
Mail.app through Apple automation and is deny-by-default: only accounts in the local
allowlist can be read or used as senders. Read `references/cli.md` only for setup,
payload, or validation details.

Start with setup and access checks:

```sh
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" setup status
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" setup accounts
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read status
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" write status
```

Bounded reads after accounts are allowed:

```sh
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read accounts
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read mailboxes --account-id <ACCOUNT_ID>
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read inbox --limit 25
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read unread --limit 25
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read search '<term>' --days 30
"$RUNDESK_SKILLS/apple-mail/scripts/apple-mail" read show --account-id <ACCOUNT_ID> --mailbox <MAILBOX> --message-id <ID>
```

Allowlist, draft, send, schedule, and cancel actions are dry-runs without `--confirm`; mail writes
use a one-time token bound to the reviewed action. Never mark mail read, download attachments, or
write without the owner approving the exact account, recipients, subject, body, attachments, and
delivery timing.

Read `references/cli.md` before attaching files or drafting, sending, scheduling, cancelling, or
wiring scheduled delivery. A scheduled message is a hash-bound local queue entry, not a Mail draft,
and delivers only through an owner-approved timer. Confirm both the pending entry and that timer
before reporting mail as scheduled.

An attachment-bearing draft briefly opens Mail's native composer. Mail then owns the body styling
and places each attachment after it. Accessibility permission is required to verify the From
address and save/close that composer. These drafts temporarily support To recipients only; add Cc
or Bcc in Mail. Attachment-bearing sends and schedules are temporarily refused; create the guarded
draft, inspect it in Mail, and send it there.
