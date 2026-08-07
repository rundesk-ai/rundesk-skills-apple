# Rundesk Apple Skills

Guarded, local-first Agent Skills for Apple Calendar, Contacts, Mail, and Messages on macOS.
Each skill includes its command, implementation, offline tests, and operating guidance as one
portable package.

```sh
rundesk skills install https://github.com/rundesk-ai/rundesk-skills-apple            # says what it would do
rundesk skills install https://github.com/rundesk-ai/rundesk-skills-apple --confirm
rundesk skills grant <agent> rundesk-skills-apple/apple-calendar
```

Installation makes all four skills available and grants none automatically. A skill is addressed
`<catalog>/<skill>`, so these names never clash with a skill of your own; `--as` stands a grant
under another name when one agent would otherwise hold two of a name.

```sh
rundesk skills catalogs
rundesk skills update rundesk-skills-apple             # preview
rundesk skills update rundesk-skills-apple --confirm   # apply
rundesk skills remove rundesk-skills-apple             # preview
rundesk skills remove rundesk-skills-apple --confirm   # apply
```

Every update restores the repository's complete package files, including each launcher and its
executable permission; a launcher that would not run as it stands is reported by
`rundesk skills doctor`, which names `chmod +x` as the fix. Configuration, permission grants,
caches, and state remain outside those packages. Removal takes the whole catalog, revokes every
grant on its skills, and names each agent that lost one.

## Credentials

None. These skills reach macOS through OS permissions and bundled bridges rather than API tokens,
so no package here declares a `rundesk.json`. `rundesk skills profiles
rundesk-skills-apple/<skill>` reports that there are no profiles; `rundesk skills configure
rundesk-skills-apple/<skill>` is refused because there is nothing to configure. `rundesk skills
doctor [<agent>]` still checks that grants and scripts are usable. Each skill also requires macOS
permissions only the owner can grant.

## Included skills

- `apple-calendar` — EventKit reads and guarded event mutations.
- `apple-contacts` — read-only AddressBook access and guarded Contacts.framework writes.
- `apple-mail` — allowlisted Mail.app reads, drafts, and guarded immediate or scheduled sends.
- `apple-messages` — read-only local message history and guarded one-to-one sends.

These integrations use macOS's system Python and frameworks. They do not create a virtual
environment or install packages. Permissions such as Full Disk Access, Contacts, Calendar,
and Automation remain explicit macOS user decisions.

Read [ENVIRONMENTS.md](ENVIRONMENTS.md) for configuration, cache, state, permission, and
dependency boundaries. Maintainers use [RELEASING.md](RELEASING.md).
