---
name: apple-mail
description: Read Mail.app messages and safely draft or send mail through an explicit local account allowlist via the apple-mail CLI. Use when a task mentions Mail.app, email, inbox, drafts, or local mail accounts.
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

Allowlist, draft, and send actions are dry-runs without `--confirm`. Draft/send confirm
uses a one-time token from the dry-run. Never mark mail read, download attachments, or
send without the owner approving the exact account, recipients, subject, and body.
