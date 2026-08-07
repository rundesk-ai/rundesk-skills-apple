---
name: apple-contacts
description: On-demand command, permission, payload, and validation details for local Apple Contacts.
category: local
---

# apple-contacts

## Entry Point

- Read source databases: `apple-contacts read sources`
- List contacts: `apple-contacts read list`
- Search contacts: `apple-contacts read search "Alex Example"`
- Show a contact: `apple-contacts read show --id CONTACT_ID`
- Export full JSON: `apple-contacts read export --json`
- Dry-run a create: `apple-contacts write create --payload contact.json`
- Actually save a reviewed create: `apple-contacts write create --payload contact.json --confirm`
- Manage groups: `apple-contacts write groups list`
- List group members: `apple-contacts write groups members --id GROUP_ID`

The same-name dispatcher also works:

```bash
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" read list
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" write groups list
```

Read commands open AddressBook SQLite in read-only mode. Write commands use a stable background-only
permission broker to launch the replaceable Contacts.framework worker from the user's XDG cache.
`groups remove-contact` verifies the Contacts.framework removal and falls back
to Apple's legacy AddressBook.framework only when Contacts.framework reports success but leaves
the member in place. Mutation commands are dry-runs unless `--confirm` is passed.

## Validation

- Run `python3 $RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts.d/test-apple-contacts.py` for offline tests with synthetic AddressBook data.
- The offline suite compiles, signs, and verifies the permission broker and Contacts worker. Its
  catalog-update regression rebuilds changed worker source while proving the approved broker's
  bytes, stable identifier, signature, and embedded privacy purpose string do not change.
- Run `apple-contacts read sources` as a live read smoke test.
- Run `apple-contacts write status` as a live Contacts.framework permission smoke test.
- Optional live mutation test: `APPLE_CONTACTS_LIVE_TESTS=1 python3 $RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts.d/test-apple-contacts.py`. This creates, updates, groups, ungroups, and deletes one synthetic contact with a unique marker.

## Provider

The read CLI treats Apple's local AddressBook SQLite files as the source of truth for exhaustive local contact context. The write CLI mutates through Contacts.framework so macOS and iCloud Contacts own sync consistency. For group member removal, it verifies the Contacts.framework result and can fall back to Apple's legacy AddressBook.framework because `CNSaveRequest.removeMember` can report success without removing the member on this Mac.

### Safety Rule

Never write directly to AddressBook SQLite. Direct DB reads are allowed; direct DB writes are forbidden. All create, update, delete, and group mutations must go through `apple-contacts-write.py`, which is dry-run by default and only saves when `--confirm` is passed after the user has asked for that exact change.

### Setup

The local Mac must have Contacts configured. The terminal or Codex host app may need macOS privacy permissions:

- Full Disk Access for the process that serves the Rundesk agent, not only an interactive terminal,
  for direct reads under `~/Library/Application Support/AddressBook/`. Restart the agent after
  changing this grant.
- Contacts permission for the **Rundesk Apple Contacts** helper. Run `write status` from the agent
  session and approve its prompt. The stable identifier is `ai.rundesk.apple-contacts.bridge`.
  If access was previously denied, enable Rundesk Apple Contacts under System Settings > Privacy
  & Security > Contacts, then rerun status.

Verify read access:

```bash
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" read sources
```

Verify Contacts.framework access:

```bash
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" write status
```

The status command names the macOS Contacts authorization state (`authorized`, `restricted`,
`denied`, or `notDetermined`). It exits zero with `status=ok` only when authorized; every other
state exits nonzero with `status=not_authorized`.

### Read Workflow

Use compact text by default:

```bash
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" read list
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" read search "Alex Example"
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" read show --id CONTACT_ID
```

Use JSON when another script or agent needs the full normalized contact payload:

```bash
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" read export --json
```

Blob/image data is summarized by default. Only use `--include-blobs` when binary payloads are explicitly needed.

### Write Workflow

Write commands accept JSON payload files and are dry-runs by default:

```bash
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" write create --payload contact.json
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" write update --id CONTACT_ID --payload patch.json
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" write delete --id CONTACT_ID
```

Save only after the user explicitly asks for that exact change:

```bash
"$RUNDESK_SKILLS/apple-contacts/scripts/apple-contacts" write update --id CONTACT_ID --payload patch.json --confirm
```

Deletes require an exact `apple_contact_id` from the read tool. Do not delete by search result text or inferred identity.

### Payload Shape

Create and update payloads may be the contact object directly or `{ "contact": { ... } }`. Supported fields include:

- Scalars: `name_prefix`, `given_name`, `middle_name`, `family_name`, `previous_family_name`, `name_suffix`, `nickname`, `organization_name`, `department_name`, `job_title`, `note`, `birthday`.
- Arrays: `phones`, `emails`, `addresses` or `postal_addresses`, `urls`, `social_profiles`, `instant_messages`, `relations`, `dates`.

Updates use patch semantics for scalar fields and replacement semantics for arrays that are present. Omitted fields are left unchanged.

### Notes

- The read command can expose personal contact data. Do not commit output or cache files containing real names, phone numbers, emails, notes, addresses, or identifiers.
- Notes are read from AddressBook SQLite and written through Contacts.framework. If Contacts.framework rejects a note write, the tool should fail rather than falling back to a direct DB write.
- Live tests create and delete only synthetic contacts with unique markers.
