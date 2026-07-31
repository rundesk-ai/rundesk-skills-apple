# Rundesk Apple Skills

Guarded, local-first Agent Skills for Apple Calendar, Contacts, Mail, and Messages on macOS.
Each skill includes its command, implementation, offline tests, and operating guidance as one
portable package.

```sh
rundesk skills install https://github.com/rundesk-ai/rundesk-skills-apple
rundesk skills install https://github.com/rundesk-ai/rundesk-skills-apple --confirm
rundesk skills grant <agent> apple-calendar
```

Installation makes all four skills available and grants none automatically.
If a custom skill already uses any declared name, the complete catalog installation fails and
leaves that custom package unchanged.

```sh
rundesk skills catalogs
rundesk skills update rundesk-skills-apple
rundesk skills remove rundesk-skills-apple
```

Every update restores the repository's complete package files, including scripts and executable
permissions. Configuration, permission grants, caches, and state remain outside those packages.
Removal requires `--yes` and is refused while any Apple skill is granted.

## Included skills

- `apple-calendar` — EventKit reads and guarded event mutations.
- `apple-contacts` — read-only AddressBook access and guarded Contacts.framework writes.
- `apple-mail` — allowlisted Mail.app reads, drafts, and guarded sends.
- `apple-messages` — read-only local message history and guarded one-to-one sends.

These integrations use macOS's system Python and frameworks. They do not create a virtual
environment or install packages. Permissions such as Full Disk Access, Contacts, Calendar,
and Automation remain explicit macOS user decisions.

Read [ENVIRONMENTS.md](ENVIRONMENTS.md) for configuration, cache, state, permission, and
dependency boundaries. Maintainers use [RELEASING.md](RELEASING.md).
