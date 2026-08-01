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
