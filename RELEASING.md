# Releasing Rundesk Apple Skills

1. Put the intended package changes and one semantic `manifest.json` version bump in a pull
   request against `main`.
2. Run `python3 -m unittest discover -s tests -v` on macOS and wait for the build workflow.
3. Merge only after the manifest, permission boundaries, dry-run behavior, and package tests
   are reviewed together.
4. Tag the merge commit with the manifest version prefixed by `v` and push the tag.

```sh
version=$(python3 -c 'import json; print(json.load(open("manifest.json"))["version"])')
git tag "v$version" <merge-commit>
git push origin "v$version"
```

The release workflow refuses a mismatched tag, reruns the suite on macOS, and creates the
GitHub Release. Never move or reuse a published tag.
