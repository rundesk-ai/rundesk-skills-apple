# AGENTS

This repository publishes Rundesk's macOS-only Apple integration skills.

- Every package is complete under `skills/<name>/`; no runtime file may live outside its skill.
- Commands use the system Python standard library and documented macOS frameworks only.
- Reads are bounded by default. Mutations are dry-runs until an exact `--confirm` request.
- Never include credentials, personal data, local database copies, account identifiers, or absolute owner paths.
- Configuration and caches live outside the repository according to `ENVIRONMENTS.md`.
- `manifest.json` is the catalog name, schema, version, and complete skill list.
- A version change updates `manifest.json`; release tags use that version prefixed with `v`.
- Run the catalog suite and every package's offline suite before publishing.
