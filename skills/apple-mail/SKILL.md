---
name: apple-mail
description: Read Mail.app messages or safely draft and send allowlisted mail, with local file attachments, using the bundled CLI. Use for email, inboxes, drafts, attachments, local accounts, or any task mentioning Mail.app.
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
send without the owner approving the exact account, recipients, subject, body, and
attachments.

Attach local files by adding absolute paths to the payload; the dry-run prints each
attachment's name, size, and hash for approval:

```json
{"email": {"account_id": "ACCOUNT_ID", "from": "sender@example.test",
  "to": ["recipient@example.test"], "subject": "Example", "body": "Example",
  "attachments": ["/absolute/path/report.pdf"]}}
```
