# Apple Integration Environments

## Runtime

Each skill is its own runtime unit. Its launcher resolves support files relative to itself and
uses `/usr/bin/env python3`; no shared Python environment, package manager, or machine-level
installation is required.

- Calendar and Contacts compile stable, background-only permission brokers plus replaceable Swift
  workers in the user's XDG cache directory. The broker app bundles carry fixed identifiers and
  embedded privacy purpose strings so macOS can present and retain Calendar and Contacts consent
  while worker code changes across catalog updates.
- Mail invokes the bundled JavaScript for Automation bridge with `/usr/bin/osascript`.
- Messages reads the owner's local SQLite database read-only and uses AppleScript for sends.

Compiled bridges and caches are disposable. They never belong in the catalog, a skill package,
or an agent's home.

Every launcher standing directly in a package's `scripts/` is executable in this repository, and
stays executable through an update. Rundesk treats one that is not as a fault against the skill,
so a launcher whose mode was lost is a broken skill rather than a cosmetic difference.

## Configuration and persistent state

Use an isolated directory per skill below:

```text
${XDG_CONFIG_HOME:-$HOME/.config}/rundesk/integrations/<skill>/
```

Apple Mail keeps its account allowlist, approval records, and scheduled-send queue there. The
queue holds approved message bodies and attachment paths until delivery, so it is owner-only
private data. Environment variables may name an alternate file when an owner already has managed
configuration. Legacy files below `~/.config/workspace/` remain readable for migration but are
never the new default.

Delivering a scheduled send needs a timer the owner installs and owns. A skill never registers a
launchd agent, a cron entry, or a Rundesk schedule on the owner's behalf; without one, queued mail
stays queued.

## Credentials

None. These integrations reach macOS through OS permissions and bundled bridges, not API tokens, so
no package declares a `rundesk.json` beside its `SKILL.md`. Rundesk therefore has nothing to prompt
for, no profiles to list, and no reason to report one of these skills as blocked. An empty or
invented declaration would be worse than none, because it would claim a requirement that does not
exist and block a skill that works.

Every environment variable these commands read is optional and has a default:

- `XDG_CACHE_HOME`, `XDG_CONFIG_HOME`, and `TZ` are OS conventions, not this catalog's settings.
- `APPLE_MAIL_CONFIG`, `APPLE_MAIL_APPROVAL_STORE`, and `APPLE_MAIL_SCHEDULE_STORE` name the
  alternate files described above, for an owner who already has managed configuration.
- `APPLE_CALENDAR_LIVE_TESTS`, `APPLE_CALENDAR_TEST_CALENDAR_ID`, and `APPLE_CONTACTS_LIVE_TESTS`
  opt a live test in and are never read by a command.

A declaration states what a skill **requires**. A value a script uses when it happens to be set is
the script's own business, so none of the above belongs in one.

Choosing between more than one account is Apple Mail's allowlist of Mail.app account IDs, kept in
the configuration directory above and selected with `--account`. It is not an environment-variable
convention, so rundesk's `<NAME>__<ACCOUNT>` profile form has nothing here to attach to, and no
resolver reads it.

If a later skill genuinely needs a value, declare it beside its `SKILL.md`:

```json
{"needs": {"SOME_TOKEN": "why it is needed, and where to get one"}}
```

Each name must match `^[A-Z][A-Z0-9_]*$` and must never contain `__`, which rundesk reads as the
account suffix in `SOME_TOKEN__WORK`. Values are prompted for in the order written, so write them in
the order somebody would supply them. `tests/test_catalog.py` enforces both rules.

Catalog updates replace package code only. They never write configuration, permissions,
credentials, local application databases, or durable approval records into the catalog tree.

## Permissions

Permissions are isolated by macOS and granted to the invoking terminal or agent process:

- Calendar: run `apple-calendar read status` in the agent session and approve the prompt from
  **Rundesk Apple Calendar**. Full Disk Access is not required for EventKit.
- Contacts: run `apple-contacts write status` in the agent session and approve the prompt from
  **Rundesk Apple Contacts**. Direct AddressBook SQLite reads separately require Full Disk Access
  for the process that serves the agent.
- Mail: Mail Automation access.
- Messages: Full Disk Access for history and Messages Automation access for sends. Grant Full Disk
  Access to the process that serves the Rundesk agent, not only to an interactive terminal, then
  restart that agent so the new grant applies.

Commands expose credential-free and permission-free `--help`. Status commands explain missing
access without attempting a mutation. Calendar and Contacts status checks may display the initial
macOS consent prompt; approving it is an owner action.

## Adding another Apple integration

Keep the complete package under `skills/<name>/`, provide one launcher under `scripts/`, and
use only package-relative paths. Prefer system frameworks and the Python standard library.
If a third-party dependency is unavoidable, publish it in a separate catalog revision only
after Rundesk has a declarative isolated-runtime contract; do not install into the machine's
Python or silently create a shared environment.
