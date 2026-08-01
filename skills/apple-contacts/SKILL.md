---
name: apple-contacts
description: Read or safely manage Apple Contacts data with the bundled CLI. Use for contacts, AddressBook, phone numbers, people lookup, contact groups, or Contacts.app.
---

# Apple Contacts

Run the bundled CLI at `$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts`. Reads use
local AddressBook SQLite; writes use Contacts.framework. Full Disk Access and Contacts
permission may be required. Read `references/cli.md` only for setup, payload, or validation
details.

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
