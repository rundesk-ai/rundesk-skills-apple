"""The Apple catalog and every packaged command, entirely offline."""

import hashlib
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ALLOWED = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: What a skill may stand beside its `SKILL.md` to say what it needs from the environment.
WANTS = "rundesk.json"

#: A name rundesk will hold a value under. `__` is excluded separately: rundesk reads it as the
#: account suffix in `SOME_TOKEN__WORK`, so a declared name containing one would be indistinguishable
#: from an account of something else.
NAMED = re.compile(r"^[A-Z][A-Z0-9_]*$")
CATALOG_GUIDE = "https://github.com/rundesk-ai/rundesk-cli/blob/main/docs/catalogs.md"
AGENT_GUIDE_HEADINGS = tuple("""# AGENTS
## Purpose
## Before you work
## Repository layout
## Package and artifact contract
## Safety and approval gates
## Delegation
## Architecture and conventions
## Documentation duties
## Build, test, and run
## Pull requests and releases
## Definition of done""".splitlines())
README_HEADINGS = tuple("""# Rundesk Apple Skills
## Skills
## Install
## Requirements
## Repository layout
## Development
## Creating a skill catalog
## Contributing
## Releases
## License""".splitlines())
PR_HEADINGS = tuple("""## Summary
## Scope and compatibility
## Critical risk
## Validation
## Repository gates
## Release
## Manual user path
## Agent""".splitlines())
ISSUE_TEMPLATE_CONTRACTS = {
    "bug-report.md": (
        ("name: Bug report", "about: Report reproducible incorrect behavior",
         'title: "[Bug] "', 'labels: ""', 'assignees: ""'),
        ("## Problem", "## Reproduction", "## Expected behavior", "## Evidence",
         "## Environment", "## Scope and privacy"),
        "747da5c0682a73adc61c35407327fb174c648630e80278c275af4a4542da6caf",
    ),
    "change-proposal.md": (
        ("name: Change proposal",
         "about: Propose a skill, integration, command, or repository improvement",
         'title: "[Proposal] "', 'labels: ""', 'assignees: ""'),
        ("## Problem", "## Desired outcome", "## Users and value",
         "## Scope and compatibility", "## Alternatives", "## Validation"),
        "2fe6a1d651ce91af2c3d19e98eea150ca26f41ad9a1ed95a6466a692b73eb4d7",
    ),
}
AGENT_GUIDE_ANCHORS = {
    "runtime": ("Python 3.9", "standard library"),
    "offline boundary": ("offline test", "network"),
    "package isolation": ("Packages do not depend on sibling packages",),
    "secret redaction": ("commit or print secrets",),
    "bounded reads": ("Bound every read", "truncation"),
    "mutation confirmation": ("Preview every mutation", "exact confirmation input"),
    "permissions": ("broadening a macOS permission prompt",),
    "live write proof": ("requires a live application probe",),
    "validation commands": (
        "python3 -m unittest discover -s tests -v",
        "python3 skills/apple-calendar/scripts/apple-calendar.d/test-apple-calendar.py -q",
        "skills/apple-calendar/scripts/apple-calendar --help",
        '(cd /tmp && "$repository_root/skills/apple-calendar/scripts/apple-calendar" --help)',
    ),
    "privacy evidence": ("inspect the complete diff and commit-visible artifacts",),
    "diff check": ("git diff --check",),
    "exact head": ("exact pull request head commit",),
}
README_ANCHORS = (
    "rundesk skills install https://github.com/rundesk-ai/rundesk-skills-apple",
    "rundesk skills install https://github.com/rundesk-ai/rundesk-skills-apple --confirm",
    "rundesk skills grant agent-name rundesk-skills-apple/apple-calendar",
    "rundesk skills configure rundesk-skills-apple/apple-calendar",
    "rundesk skills profiles rundesk-skills-apple/apple-calendar",
    ".github/ISSUE_TEMPLATE/bug-report.md",
    ".github/ISSUE_TEMPLATE/change-proposal.md",
    ".github/pull_request_template.md",
    "python3 skills/apple-calendar/scripts/apple-calendar.d/test-apple-calendar.py -q",
    '(cd /tmp && "$repository_root/skills/apple-calendar/scripts/apple-calendar" --help)',
)
PR_CHECKLIST_ANCHORS = (
    "Every mutation remains a preview until the owner approves the exact target and effect and "
    "supplies the package's exact confirmation input.",
    "Required GitHub checks pass for the exact head commit.",
    "`git diff --check`",
    "Reads remain bounded by default and report truncation explicitly.",
    "No package imports, executes, or depends on a sibling package.",
    "Runtime code remains system Python 3.9+, standard-library and documented macOS-framework only, unless the owner approved a dependency.",
    "Tests remain offline and do not reach a real account, mailbox, or Apple service.",
    "Every touched Apple write was proven against the live application",
    "Commands report unearned work as failure.",
    "The diff contains no personal data, message or mail excerpt, local database copy, account identifier, email address, phone number, owner-specific path, or unrelated artifact.",
)


class AppleCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))

    @staticmethod
    def markdown_headings(path):
        headings = []
        in_fence = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("```"):
                in_fence = not in_fence
            elif not in_fence and re.fullmatch(r"#{1,2} .+", line):
                headings.append(line)
        return tuple(headings)

    @staticmethod
    def shell_fences(text):
        return re.findall(r"(?ms)^```sh\n(.*?)^```$", text)

    def test_repository_guides_are_identical_and_structured(self):
        agents = ROOT / "AGENTS.md"
        claude = ROOT / "CLAUDE.md"
        self.assertTrue(claude.is_file())
        self.assertFalse(claude.is_symlink())
        self.assertEqual(agents.read_bytes(), claude.read_bytes())
        self.assertEqual(AGENT_GUIDE_HEADINGS, self.markdown_headings(agents))
        text = agents.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        self.assertIn(CATALOG_GUIDE, text)
        for purpose, anchors in AGENT_GUIDE_ANCHORS.items():
            with self.subTest(contract=purpose):
                for anchor in anchors:
                    self.assertIn(" ".join(anchor.split()), normalized)
        for fence in self.shell_fences(text):
            self.assertNotRegex(fence, r"<[^>\n]+>")

    def test_repository_templates_follow_the_contract(self):
        pull_request = ROOT / ".github" / "pull_request_template.md"
        self.assertEqual(PR_HEADINGS, self.markdown_headings(pull_request))
        self.assertIn("🤖 by <Agent>", pull_request.read_text(encoding="utf-8"))
        pull_request_text = pull_request.read_text(encoding="utf-8")
        normalized_pull_request = " ".join(pull_request_text.split())
        for anchor in PR_CHECKLIST_ANCHORS:
            self.assertIn(" ".join(f"- [ ] {anchor}".split()), normalized_pull_request)
        issue_root = ROOT / ".github" / "ISSUE_TEMPLATE"
        self.assertEqual(
            set(ISSUE_TEMPLATE_CONTRACTS) | {"config.yml"},
            {path.name for path in issue_root.iterdir() if path.is_file()},
        )
        self.assertEqual(
            b"blank_issues_enabled: false\n",
            (issue_root / "config.yml").read_bytes(),
        )
        for filename, (frontmatter, headings, digest) in ISSUE_TEMPLATE_CONTRACTS.items():
            with self.subTest(template=filename):
                path = issue_root / filename
                raw = path.read_bytes()
                text = raw.decode("utf-8")
                self.assertEqual(["", *frontmatter], text.split("---", 2)[1].splitlines())
                self.assertEqual(headings, self.markdown_headings(path))
                self.assertEqual(digest, hashlib.sha256(raw).hexdigest())

    def test_readme_follows_the_catalog_contract(self):
        readme = ROOT / "README.md"
        self.assertEqual(README_HEADINGS, self.markdown_headings(readme))
        text = readme.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        self.assertIn(CATALOG_GUIDE, text)
        self.assertNotIn("<agent>", text)
        for anchor in README_ANCHORS:
            self.assertIn(" ".join(anchor.split()), normalized)
        for fence in self.shell_fences(text):
            self.assertNotRegex(fence, r"<[^>\n]+>")

    def test_public_repository_docs_contain_no_private_material(self):
        forbidden = (
            "/Users/", "BEGIN OPENSSH PRIVATE KEY", "BEGIN RSA PRIVATE KEY",
        )
        paths = [ROOT / "AGENTS.md", ROOT / "CLAUDE.md", ROOT / "README.md"]
        paths.extend((ROOT / ".github").rglob("*.md"))
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                text = path.read_text(encoding="utf-8").lower()
                self.assertFalse(any(value.lower() in text for value in forbidden))

    def test_manifest_declares_every_complete_skill(self):
        self.assertEqual(1, self.manifest["schema"])
        self.assertEqual("rundesk-skills-apple", self.manifest["name"])
        self.assertRegex(self.manifest["version"], r"^\d+\.\d+\.\d+$")
        declared = {entry["name"]: entry["path"] for entry in self.manifest["skills"]}
        self.assertEqual(
            {"apple-calendar", "apple-contacts", "apple-mail", "apple-messages"},
            set(declared),
        )
        self.assertEqual(
            sorted(declared),
            sorted(path.name for path in (ROOT / "skills").iterdir() if path.is_dir()),
        )
        for name, relative in declared.items():
            with self.subTest(skill=name):
                self.assertRegex(name, ALLOWED)
                package = ROOT / relative
                self.assertEqual(name, package.name)
                page = (package / "SKILL.md").read_text(encoding="utf-8")
                self.assertRegex(page, rf"(?m)^name: {re.escape(name)}$")
                frontmatter = page.split("---", 2)[1]
                keys = [line.split(":", 1)[0] for line in frontmatter.splitlines()
                        if line and not line.startswith(" ")]
                self.assertEqual(["name", "description"], keys)
                description = re.search(
                    r"(?m)^description: (.+)$", frontmatter
                ).group(1)
                self.assertLessEqual(len(description), 1024)
                self.assertIn("Use ", description)
                self.assertLess(len(page.splitlines()), 500)
                self.assertFalse((package / "README.md").exists())
                self.assertFalse((package / "CHANGELOG.md").exists())
                self.assertTrue((package / "scripts" / name).is_file())

    def test_readme_lists_exactly_the_declared_skills(self):
        """A catalog that ships a skill its README never mentions is a catalog nobody trusts."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        listed = set(re.findall(r"(?m)^- `([a-z0-9-]+)`", readme))
        declared = {entry["name"] for entry in self.manifest["skills"]}
        self.assertEqual(declared, listed, "README.md and manifest.json disagree")

    def test_readme_uses_the_current_rundesk_command_surface(self):
        """A README naming a flag rundesk dropped sends a reader to a command that refuses them.

        Checked because it already rotted once: removal took `--yes`, and takes `--confirm`. The
        addressed form is asserted too, since a bare skill name is now refused outright.
        """
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("--yes", readme)
        for verb in ("install", "update", "remove"):
            with self.subTest(verb=verb):
                self.assertRegex(readme, rf"(?m)^rundesk skills {verb} \S+ --confirm$")
        self.assertRegex(readme, r"(?m)^rundesk skills grant \S+ rundesk-skills-apple/[a-z-]+$")

    def test_every_launcher_is_executable(self):
        """Rundesk faults a skill whose launcher will not run, and it is right to.

        To an agent a launcher that is present and not executable looks exactly like one that works,
        right up until it tries. Only what stands directly in `scripts/` is a command; everything in
        `<name>.d/` is reached by one of these.
        """
        for entry in self.manifest["skills"]:
            scripts = ROOT / entry["path"] / "scripts"
            for command in sorted(one for one in scripts.iterdir() if one.is_file()):
                with self.subTest(script=command.relative_to(ROOT)):
                    self.assertTrue(os.access(command, os.X_OK), "needs chmod +x")

    def test_any_declared_needs_are_legal(self):
        """What a package says it needs has to be something rundesk can actually hold.

        **No package here declares anything today, and that is the correct state, not an omission.**
        These skills reach macOS through OS permissions and bundled bridges, not tokens. Every
        environment variable they read is an OS convention, a path override, or a test opt-in — each
        optional, each with a default — and a declaration is for what is *required*, so none of them
        belongs in one. A package with no `rundesk.json` declares nothing, needs nothing, and is
        never reported as blocked; an empty or invented one would claim a requirement that does not
        exist. `ENVIRONMENTS.md` records the reasoning.

        So this stands as the guard for the day one is added: a name rundesk cannot hold a value
        under, or one carrying the `__` that marks an account, would make every profile the install
        then found one that nobody made.
        """
        for entry in self.manifest["skills"]:
            declared = ROOT / entry["path"] / WANTS
            with self.subTest(skill=entry["name"]):
                if not declared.exists():
                    continue
                said = json.loads(declared.read_text(encoding="utf-8"))
                self.assertIsInstance(said, dict)
                self.assertEqual(["needs"], list(said), "the file carries exactly one key")
                self.assertTrue(said["needs"], "an empty declaration is worse than no file")
                for name, why in said["needs"].items():
                    with self.subTest(needs=name):
                        self.assertRegex(name, NAMED)
                        self.assertNotIn("__", name, "that is how an account is written")
                        self.assertTrue(str(why).strip(), "say what it is and where to get one")

    def test_every_launcher_has_credential_free_help(self):
        for entry in self.manifest["skills"]:
            command = ROOT / entry["path"] / "scripts" / entry["name"]
            with self.subTest(skill=entry["name"]):
                completed = subprocess.run(
                    [str(command), "--help"], capture_output=True, text=True, check=False,
                )
                self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                self.assertIn("usage:", (completed.stdout + completed.stderr).lower())

    def test_every_package_offline_suite_passes(self):
        for entry in self.manifest["skills"]:
            support = ROOT / entry["path"] / "scripts" / f"{entry['name']}.d"
            tests = list(support.glob("test-*.py"))
            with self.subTest(skill=entry["name"]):
                self.assertEqual(1, len(tests))
                completed = subprocess.run(
                    [sys.executable, str(tests[0]), "-q"],
                    capture_output=True, text=True, check=False,
                    env={key: value for key, value in os.environ.items()
                         if not key.endswith("_LIVE_TESTS")},
                )
                self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)

    def test_repository_contains_no_owner_paths_or_credentials(self):
        forbidden = ("/Users/", "BEGIN OPENSSH PRIVATE KEY", "BEGIN RSA PRIVATE KEY")
        for path in (ROOT / "skills").rglob("*"):
            if (path.is_file() and ".git" not in path.parts
                    and "__pycache__" not in path.parts and path.suffix != ".pyc"):
                with self.subTest(path=path.relative_to(ROOT)):
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    self.assertFalse(any(value in text for value in forbidden))
                    self.assertNotIn("## Use When", text)
                    self.assertNotIn("in this README", text)


if __name__ == "__main__":
    unittest.main()
