## Summary

<!-- State what changes and why in one or two lines. -->

-

## Scope and compatibility

- Packages changed:
- User-visible behavior:
- Preserved behavior:
- Dependencies added: none
- macOS permission, configuration, cache, or mutable-state changes: none
- Apple resource mutations: none

## Critical risk

<!-- Required for writes, permissions, privacy, destructive commands, or other critical risk. Write "None" when no critical risk applies. -->

- Risk:
- Guard:

## Validation

- [ ] `python3 -m unittest discover -s tests -v`
- [ ] Every touched package suite passes with its exact command recorded below, or no package changed.
- [ ] Every touched launcher returns zero for credential-free `--help`, or no launcher changed.
- [ ] Every touched launcher resolves from outside the repository, or no launcher changed.
- [ ] Every touched Apple write was proven against the live application and its test double reconciled with that result, or no Apple write changed.
- [ ] `git diff --check`
- [ ] Required GitHub checks pass for the exact head commit.

```text
# Exact package, launcher, and manual verification commands with observed results
```

## Repository gates

- [ ] The diff contains no personal data, message or mail excerpt, local database copy, account identifier, email address, phone number, owner-specific path, or unrelated artifact.
- [ ] Reads remain bounded by default and report truncation explicitly.
- [ ] Every mutation remains a preview until the owner approves the exact target and effect and supplies the package's exact confirmation input.
- [ ] Commands report unearned work as failure.
- [ ] No package imports, executes, or depends on a sibling package.
- [ ] Runtime code remains system Python 3.9+, standard-library and documented macOS-framework only, unless the owner approved a dependency.
- [ ] Tests remain offline and do not reach a real account, mailbox, or Apple service.
- [ ] Permission prompts remain explicit user decisions and no permission surface broadened without owner approval.
- [ ] `README.md`, `manifest.json`, `tests/test_catalog.py`, and `skills/` agree.
- [ ] Any required semantic `manifest.json` version change follows `RELEASING.md` and is stated below.

## Release

- Manifest version: `<before>` → `<after>`
- SemVer reason:
- Release or follow-up required after merge:

## Manual user path

<!-- Give the shortest representative command and expected result. State clearly when no live Apple application or framework call was made. -->

```text

```
