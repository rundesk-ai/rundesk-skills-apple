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

## Configuration and persistent state

Use an isolated directory per skill below:

```text
${XDG_CONFIG_HOME:-$HOME/.config}/rundesk/integrations/<skill>/
```

Apple Mail keeps its account allowlist and approval records there. Environment variables may
name an alternate file when an owner already has managed configuration. Legacy files below
`~/.config/workspace/` remain readable for migration but are never the new default.

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
