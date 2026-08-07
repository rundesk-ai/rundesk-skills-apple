# AGENTS

Rules for every agent working in this repository. These rules are law; where they conflict with your
general habits, this file wins.

This repository publishes **Rundesk's macOS-only Apple integration skills** — Calendar, Contacts,
Mail, and Messages, each packaged with its command, its offline tests, and its operating guidance.
`README.md` is what a person reads, `ENVIRONMENTS.md` is the runtime, permission, and state
contract, `RELEASING.md` is how a version ships. This file defines how you build here.

## Before you work

1. **Read `README.md`, `ENVIRONMENTS.md`, and the `SKILL.md` of every package you are touching.**
   Read a file before editing it.
2. **Load the skill that governs the artifact you are about to write.** Each one is law for that
   artifact, the same as this file:

   | Writing or changing | Follow |
   |---|---|
   | any `SKILL.md` | `writing-skills` |
   | any `.py` under `skills/` | `python-patterns` |
   | any `test-*.py` or `tests/test_catalog.py` | `python-patterns` (testing) |
   | a pull request | `managing-github` (pull requests) |
   | a version bump, tag, or release | `RELEASING.md`, then `managing-github` (releases) |

   An agent that does not hold one of these skills still follows the rule; say in your report which
   ones you could not load, because silence reads as compliance.
3. **Check whether an existing package already owns the surface** before adding one. Extend it
   rather than shipping a second command against the same Apple framework.
4. When the owner raises a concern, investigate before contradicting — evidence, not a hunch.

## Hard gates — require explicit approval

- **A new mutation command.** These packages reach a person's real mail, calendar, contacts, and
  message history. A verb that sends, writes, or deletes is the owner's call, not a convenience.
- **A dependency.** Commands use the system Python's standard library and documented macOS
  frameworks only. A catalog installer copies files and executes no setup code, so a package that
  needs `pip` cannot be installed at all.
- **Anything that broadens a macOS permission prompt** — Full Disk Access, Automation, Contacts,
  Calendar. Those stay explicit user decisions and are never worked around.
- **Deletions.** Do not delete a package, a command, or a file outside the task's immediate scope.
- **Commits.** Do not commit or push unless told to.
- **This file.** Never modify `AGENTS.md` without approval.

## Never

- **Never trust a green offline suite as proof an Apple write works.** The test doubles here can
  pass while the real path fails — AppleScript, JXA, and the frameworks reject at runtime what a
  double happily accepts. Prove any Mail, Calendar, Contacts, or Messages write against the live
  application, then make the double mirror what the live probe actually showed. An offline suite
  that has never been reconciled with a real run is a suite that tests itself.
- **Never let the catalog's public surface drift.** Adding, removing, or renaming a skill changes
  `manifest.json`, `README.md`, and the catalog suite **in the same commit**. A README naming three
  skills for a catalog of four is how a reader learns the repository cannot be trusted, and it hides
  in a diff that only adds files. `tests/test_catalog.py` enforces this, so the rule survives an
  agent who forgets it.
- **Never put a runtime file outside its package.** Everything a command needs — Python, JXA
  bridges, helpers — lives under `skills/<name>/`, so removing one skill can never break another.
- **Never let a test reach the network or a real mailbox.** CI runs on a machine with no accounts,
  no permissions granted, and no data.
- **Never commit personal data, a message or mail excerpt, a local database copy, an account
  identifier, an email address, a phone number, or an absolute owner path.** Examples use
  `example.test`.
- **Never let a command report success it did not earn.** Work that did not happen writes to stderr
  and exits non-zero — especially a send that a permission prompt silently blocked.
- **Never widen a read silently.** A truncated list says so on stderr, so a partial answer is never
  presented as a complete one.

## The package contract

```text
skills/<name>/
├── SKILL.md              when to reach for it, the safest defaults, the boundaries
├── references/cli.md     setup, permissions, output contract, validation — read on demand
└── scripts/
    ├── <name>            a launcher that resolves paths from its own location
    └── <name>.d/
        ├── <name>.py       the command surface
        ├── <name>-*.py     one concern each — read, write, setup
        ├── *.js            JXA bridges, beside the code that runs them
        └── test-<name>.py  the offline suite
```

- Credential-free `--help` exits 0.
- Reads are bounded by default. Mutations are dry-runs until an exact `--confirm` request.
- Configuration, caches, and state live outside this repository, as `ENVIRONMENTS.md` sets out,
  because a catalog update replaces the package tree atomically.

## Tech stack

- **Runtime:** the system Python, 3.9+ — the floor CI pins, because it is what a fresh macOS ships.
- **Dependencies:** the standard library and documented macOS frameworks. See the hard gate above.
- **Tests:** `unittest`, offline, run directly.

## Build, test & run

```sh
python3 -m unittest discover -s tests -v                    # the gate
python3 skills/<name>/scripts/<name>.d/test-<name>.py -q    # one package on its own
skills/<name>/scripts/<name> --help                         # exits 0 with nothing configured
```

The catalog suite runs each package's own suite, so a package added to `manifest.json` is in the
gate the day it lands. Run the bundled command once from a directory outside the source tree to
prove the launcher resolves its own files.

CI runs the same command on macOS with Python 3.9, without permissions or accounts — so the suite
passes on this code and never on a machine's configuration.

## Documentation duties

Keep the documentation true in the same task that changes reality.

- A skill added, removed, or renamed → `manifest.json`, `README.md`, and the expected-skill
  assertions in `tests/test_catalog.py`.
- A change to runtime, configuration, state, or a permission → `ENVIRONMENTS.md`.
- A change to the release process → `RELEASING.md`.
- Setup, permission steps, output contracts, and validation belong in the package's
  `references/cli.md`, never in `SKILL.md`. `SKILL.md` carries triggers, defaults, boundaries, and
  the gotchas an agent could not infer.

## Definition of done

1. `python3 -m unittest discover -s tests -v` passes, and CI is green.
2. Every rule here held — no dependency, no network in a test, no personal data or owner path
   committed.
3. Any Apple write you changed was proven against the live application, and its double mirrors what
   that run showed.
4. `README.md` and `manifest.json` agree with what the repository actually ships.
5. The governing skills in **Before you work** were followed, or your report names the ones you
   could not load.
