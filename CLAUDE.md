# AGENTS

Rules for every agent working in this repository. These instructions define how to work here;
where they conflict with general habits, this file wins.

## Purpose

This repository publishes Rundesk's macOS-only Apple integration skills for Calendar, Contacts,
Mail, and Messages. Each package ships its command, operating guidance, and offline tests.

- `README.md` defines the public catalog and install surface.
- `ENVIRONMENTS.md` defines runtime, configuration, permissions, and mutable-state boundaries.
- `RELEASING.md` defines versioning and releases.
- Each package's `SKILL.md` and `references/cli.md` define its agent and command contracts.
- The [skill catalog guide](https://github.com/rundesk-ai/rundesk-cli/blob/main/docs/catalogs.md)
  defines the organization-wide catalog contract.

Keep these sources of truth aligned with the shipped files and behavior.

## Before you work

1. Read `README.md`, `ENVIRONMENTS.md`, and `RELEASING.md` when the task touches their contracts.
   Read every `SKILL.md`, reference, script, and test you will change before editing it.
2. Inspect the skills supplied by the runtime and load the smallest complete set that applies. Use
   `writing-skills` for `SKILL.md`, applicable runtime or testing guidance for code and tests,
   `naming-grammar-conventions` for recurring or cross-layer terminology, and `managing-github` for
   pull requests or releases.
3. Search before creating. Reuse or extend the package that already owns an Apple framework or
   command surface instead of introducing a competing path.
4. Inspect `git status` and the relevant diff before editing. Preserve unrelated work, keep shared
   worktrees safe, and never undo another contributor's changes.
5. Investigate an owner concern with repository evidence before contradicting it. Do not guess about
   a framework contract, permission, runtime result, or public surface.

## Repository layout

```text
.
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug-report.md
│   │   └── change-proposal.md
│   ├── pull_request_template.md
│   └── workflows/       CI workflows
├── skills/              independently installable Apple skill packages
├── tests/               catalog-level structure and contract checks
├── AGENTS.md            agent instructions
├── CLAUDE.md            byte-identical copy of AGENTS.md
├── ENVIRONMENTS.md      runtime, configuration, permission, and state contract
├── README.md            public catalog documentation
├── RELEASING.md         release procedure
└── manifest.json        published catalog inventory and version
```

Do not add root runtime code or shared package libraries. Scratch work belongs outside the
repository and must be removed when the task ends.

## Package and artifact contract

```text
skills/<name>/
├── SKILL.md
├── references/
│   └── cli.md
└── scripts/
    ├── <name>
    └── <name>.d/
        ├── <name>.py
        ├── <name>-*.py
        ├── platform bridges and metadata when required
        └── test-<name>.py
```

- Keep the complete runtime under its owning package. Launchers resolve support files relative to
  their own location and work when invoked outside the repository.
- Use the system Python 3.9+ standard library and documented macOS frameworks only. Do not require
  `pip`, a virtual environment, repository setup code, or a shared runtime.
- Keep configuration, compiled bridges, caches, approval records, queues, and other mutable state in
  the locations defined by `ENVIRONMENTS.md`, never in the catalog tree.
- Apple packages currently require no credentials and therefore carry no `rundesk.json`. Do not add
  an empty or invented credential declaration.
- Credential-free and permission-free `--help` must exit zero. Status commands explain missing
  access without performing a mutation.
- Bound every read by default, make truncation explicit on stderr, and emit JSON only when requested.
- Preview every mutation first. Execute it only after the owner approves the exact target and effect
  and the command receives the package's exact confirmation input.

## Safety and approval gates

Obtain explicit owner approval before:

- adding, removing, or changing a dependency;
- adding a command or option that writes, sends, deletes, or otherwise mutates real Apple data;
- broadening a macOS permission prompt or requiring Full Disk Access, Automation, Contacts, Calendar,
  or Accessibility access in a new place;
- deleting a package, command, or any file outside the task's immediate scope;
- editing `AGENTS.md` or `CLAUDE.md`; or
- committing, pushing, tagging, releasing, or otherwise changing external state unless the request
  already authorizes that exact action.

Never:

- commit or print secrets, personal data, message or mail excerpts, local database copies, account
  identifiers, email addresses, phone numbers, private project names, or absolute owner paths;
- bypass a macOS permission boundary or grant a permission on the owner's behalf;
- let an offline test contact a network, real mailbox, calendar, contacts store, or Messages database;
- use destructive Git commands, history rewrites, broad restore operations, or another contributor's
  work to clean a shared worktree;
- report success for work the live application did not perform, or hide a refusal, permission error,
  partial result, or truncation; or
- treat a green offline suite as proof that an Apple write works.

Any changed Mail, Calendar, Contacts, or Messages write path requires a live application probe with
safe test data and the required permissions. Reconcile the offline double with the observed live
behavior. If live proof is unavailable, report validation as incomplete instead of claiming success.

## Delegation

- Delegate only bounded, self-contained work with non-overlapping file ownership when it materially
  helps. Give each worker the applicable rules, exact scope, prohibited changes, and required proof.
- Keep requirements, architecture decisions, integration, and final verification in the parent
  context. Review every delegated result before using it.
- Delegation never expands authority. A worker may not commit, push, mutate live data, broaden
  permissions, or make another gated change unless the original request authorized it.
- In a shared worktree, coordinate ownership explicitly, preserve concurrent edits, and never revert
  files to resolve overlap.

## Architecture and conventions

- A package is the runtime, permission, test, and removal boundary. Packages do not depend on sibling
  packages or root-local helpers.
- Calendar and Contacts may use their package-local Swift permission brokers and workers. Mail may
  use package-local JavaScript for Automation and AppKit bridges. Messages may use a package-local
  read-only SQLite path and AppleScript send bridge. Preserve the permission model documented in
  `ENVIRONMENTS.md`.
- Use `from __future__ import annotations` where Python modules need modern annotations while retaining
  the Python 3.9 floor. Prefer standard-library types and `unittest`.
- Keep command text compact and deterministic. Send operational errors, refusals, and truncation
  notices to stderr and return non-zero when requested work did not happen.
- Comments explain non-obvious decisions, invariants, ordering, platform behavior, and security
  boundaries. Do not narrate mechanics already clear from the code.
- Use lowercase hyphenated package and command names. Keep the directory, manifest name, launcher,
  frontmatter `name`, and documented command spelling aligned.
- Use `naming-grammar-conventions` when a term recurs across the CLI, Python, bridges, output, and
  documentation. Preserve fixed Apple framework terminology at its boundary.

## Documentation duties

Keep documentation true in the same change that changes behavior:

- Add, remove, or rename a skill: update `manifest.json`, the README skill list, and catalog tests.
- Change runtime, configuration, state, or permissions: update `ENVIRONMENTS.md`.
- Change the release process: update `RELEASING.md`.
- Change setup, permission steps, output, confirmation, or validation: update the package's
  `references/cli.md`.
- Change triggers, safe defaults, boundaries, or non-obvious agent guidance: update `SKILL.md` using
  `writing-skills`.
- Change either root agent guide: make `AGENTS.md` and `CLAUDE.md` byte-identical in the same change.

Do not duplicate detailed CLI reference material in `SKILL.md`. Keep public examples synthetic and
use reserved domains such as `example.test`.

## Build, test, and run

```sh
python3 -m unittest discover -s tests -v
python3 skills/apple-calendar/scripts/apple-calendar.d/test-apple-calendar.py -q
skills/apple-calendar/scripts/apple-calendar --help
repository_root="$(pwd)"
(cd /tmp && "$repository_root/skills/apple-calendar/scripts/apple-calendar" --help)
git diff --check
```

- Run the root catalog suite for every change. It runs each package's offline suite.
- Run each touched package suite directly and report its exact command, test count, skips, and result.
- Exercise credential-free, permission-free `--help` for a touched launcher.
- Invoke a touched launcher from a directory outside the repository to prove package-relative launch.
- Keep CI offline and compatible with macOS and Python 3.9.
- For a changed mutation, also perform the live application proof required by the safety section.
- Run focused checks for documentation or catalog-test changes, compare the two guide files byte for
  byte, inspect the final diff, and run `git diff --check`.

## Pull requests and releases

- Use `.github/pull_request_template.md` for every pull request. Preserve its headings and checklists.
- Base every claim on the exact pull request head commit. Record exact commands and observed results,
  and explain any unavailable or inapplicable check.
- Before handoff or merge, inspect the complete diff and commit-visible artifacts for credentials,
  personal data, owner or customer identifiers, private-project language, and owner-specific paths.
- Require the configured CI checks for the exact head. A prior run, local green result, or different
  commit is not pull request evidence.
- Process-only guide or template changes, including `AGENTS.md`, `CLAUDE.md`, and GitHub templates,
  do not require a manifest version bump. Changes to published catalog contents or runtime behavior
  follow `RELEASING.md`.
- Do not merge, tag, or release unless the request explicitly authorizes it.

## Definition of done

1. Complete the full requested scope and preserve every gate in this file.
2. Run the root catalog suite, every touched package suite, applicable launcher checks, guide parity
   check, focused tests, and `git diff --check`; report exact observed results.
3. Keep `README.md`, `manifest.json`, package directories, tests, `ENVIRONMENTS.md`, `RELEASING.md`,
   `AGENTS.md`, and `CLAUDE.md` synchronized wherever the change touches their contracts.
4. Prove every changed Apple write against the live application, or explicitly report that required
   proof as unavailable and validation as incomplete.
5. Inspect the final diff for unrelated changes, secrets, personal or private identifiers, owner
   paths, debug residue, placeholders, and temporary files.
6. Report what changed, exact checks and counts, live or manual observations, governing skills used,
   and every unrun check or remaining limitation.
