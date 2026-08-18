# Rundesk Apple Skills

Guarded, local-first Agent Skills for Apple Calendar, Contacts, Mail, and Messages on macOS. Each
skill is a portable package containing its command, implementation, operating guidance, and offline
tests.

## Skills

- `apple-calendar` - EventKit reads and guarded event mutations.
- `apple-contacts` - read-only AddressBook access and guarded Contacts.framework writes.
- `apple-mail` - allowlisted Mail.app reads, drafts, and guarded immediate or scheduled sends.
- `apple-messages` - read-only local message history and guarded one-to-one sends.

## Install

Rundesk CLI installs the complete catalog, preserves executable files, and keeps permissions,
configuration, caches, and state outside the catalog. Installation grants no skill automatically.

```sh
rundesk skills install https://github.com/rundesk-ai/rundesk-skills-apple
rundesk skills install https://github.com/rundesk-ai/rundesk-skills-apple --confirm
rundesk skills grant agent-name rundesk-skills-apple/apple-calendar
```

The first install command previews the exact change; `--confirm` applies it. Skills use the verified
`<catalog>/<skill>` grant syntax. Updates and removal follow the same preview-first contract:

```sh
rundesk skills update rundesk-skills-apple
rundesk skills update rundesk-skills-apple --confirm
rundesk skills remove rundesk-skills-apple
rundesk skills remove rundesk-skills-apple --confirm
```

To use a package without Rundesk, copy or symlink its complete `skills/<name>/` directory into the
skill directory supported by the agent runtime. Preserve executable bits, review an existing
same-name destination before replacing it, follow the package's `references/cli.md`, and start a new
agent session after installation.

Apple packages have no credential profiles. The profile command reports that directly, and doctor
checks one agent's grants and executable commands. Configuration is intentionally refused because
the package declares no credential values:

```sh
rundesk skills configure rundesk-skills-apple/apple-calendar
rundesk skills profiles rundesk-skills-apple/apple-calendar
rundesk skills doctor agent-name
```

## Requirements

- macOS with the documented Calendar, Contacts, Mail, Messages, Automation, Accessibility, or Full
  Disk Access permissions for the chosen command. Only the owner can grant these permissions.
- System Python 3.9+ and documented macOS frameworks. No package manager or virtual environment is
  required.
- No API credentials. These packages use local macOS permissions and bundled bridges, so they do not
  declare `rundesk.json` files.

Read [ENVIRONMENTS.md](ENVIRONMENTS.md) for the exact runtime, configuration, permission, cache, and
state contract. Never place personal Apple data or permission artifacts in this repository.

## Repository layout

```text
.
├── .github/
│   ├── ISSUE_TEMPLATE/{bug-report.md,change-proposal.md}
│   └── pull_request_template.md
├── skills/
│   └── <name>/
│       ├── SKILL.md
│       ├── references/cli.md
│       └── scripts/
│           ├── <name>
│           └── <name>.d/        Python, Apple bridges, metadata, and offline tests
├── tests/test_catalog.py
├── AGENTS.md
├── CLAUDE.md
├── ENVIRONMENTS.md
├── RELEASING.md
└── manifest.json
```

Each package is an independent runtime and permission boundary. Runtime files never depend on a
sibling package or a root-local library.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 skills/apple-calendar/scripts/apple-calendar.d/test-apple-calendar.py -q
skills/apple-calendar/scripts/apple-calendar --help
repository_root="$(pwd)"
(cd /tmp && "$repository_root/skills/apple-calendar/scripts/apple-calendar" --help)
git diff --check
```

The root suite is the catalog gate and runs every package's offline suite. A changed write path also
requires a safe live application probe; offline doubles alone do not prove an Apple write works.
Read [AGENTS.md](AGENTS.md) before contributing for approval, privacy, validation, and documentation
requirements.

## Creating a skill catalog

Use the organization-wide [skill catalog guide](https://github.com/rundesk-ai/rundesk-cli/blob/main/docs/catalogs.md)
for package structure, manifests, runtime isolation, public documentation, testing, and release
contracts. Extend an existing Apple package when it already owns the framework or command surface.

## Contributing

- Report reproducible incorrect behavior with the [bug report template](.github/ISSUE_TEMPLATE/bug-report.md).
- Propose a skill, command, or repository improvement with the [change proposal template](.github/ISSUE_TEMPLATE/change-proposal.md).
- Prepare changes with the [pull request template](.github/pull_request_template.md) and provide
  evidence for the exact head commit.

Contributions must keep `README.md`, `manifest.json`, `skills/`, and catalog tests aligned and must
contain no credentials, personal data, private identifiers, or owner-specific paths.

## Releases

Follow [RELEASING.md](RELEASING.md) for semantic versioning, tags, and publication. Changes to
published catalog contents or runtime behavior require the version treatment it defines.
Process-only guide or template changes, including `AGENTS.md`, `CLAUDE.md`, and GitHub templates, do
not require a manifest version bump.

## License

This repository is licensed under the [MIT License](LICENSE).
