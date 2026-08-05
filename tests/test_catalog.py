"""The Apple catalog and every packaged command, entirely offline."""

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


class AppleCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))

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
