---
name: apple-contacts
description: Use when the user asks to find or update a person, contact detail, or contact group in macOS Contacts. It supplies bounded local reads and guarded exact-record Contacts mutations. Do not use for a CRM, organizational directory, or inferred identity lookup.
---

# Apple Contacts

Run `$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts`. Reads need Full Disk Access for the
agent host; writes need macOS Contacts approval. Read `references/cli.md` only for permission setup,
payload fields, group behavior, or validation.

Start with access checks:

```sh
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" read sources
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" write status
```

Bounded reads by default:

```sh
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" read list --limit 50
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" read search '<name>'
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" read show --id <CONTACT_ID>
```

Use `--json` only when full structured contact data is required. Create/update/delete
and group mutations are dry-runs without `--confirm`. Never write AddressBook SQLite
directly. Never confirm a mutation unless the owner approved that exact contact or
group change.
