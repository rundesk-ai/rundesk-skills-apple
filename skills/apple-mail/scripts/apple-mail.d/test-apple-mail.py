#!/usr/bin/env python3
"""Offline tests for apple-mail."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch


TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ACCOUNTS = [
    {
        "id": "account-allowed",
        "name": "Synthetic Allowed",
        "email_addresses": ["allowed@example.test"],
        "account_type": "imap",
        "enabled": True,
    },
    {
        "id": "account-denied",
        "name": "Synthetic Denied",
        "email_addresses": ["denied@example.test"],
        "account_type": "imap",
        "enabled": True,
    },
]


MESSAGE = {
    "id": 42,
    "message_id": "synthetic-message@example.test",
    "subject": "Synthetic planning",
    "sender": "Alex Example <alex@example.test>",
    "date_received": "2026-07-21T14:00:00.000Z",
    "date_sent": "2026-07-21T13:59:00.000Z",
    "read": False,
    "flagged": False,
    "junk": False,
    "message_size": 128,
    "reply_to": "",
    "to": [{"name": "Taylor Example", "address": "taylor@example.test"}],
    "cc": [],
    "attachments": [],
    "preview": "Synthetic preview",
    "preview_truncated": False,
}


class AppleMailTest(unittest.TestCase):
    def setUp(self):
        self.library = load_module("apple_mail_lib", TOOL_DIR / "apple_mail_lib.py")
        self.setup_module = load_module("apple_mail_setup", TOOL_DIR / "apple-mail-setup.py")
        self.read_module = load_module("apple_mail_read", TOOL_DIR / "apple-mail-read.py")
        self.write_module = load_module("apple_mail_write", TOOL_DIR / "apple-mail-write.py")
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / "apple-mail.json"

    def tearDown(self):
        self.tmp.cleanup()

    def allow_account(self):
        self.library.save_config(self.config, ["account-allowed"])

    def test_config_defaults_to_deny_all_and_is_owner_only(self):
        self.assertEqual(self.library.load_config(self.config)["allowed_account_ids"], [])
        self.library.save_config(self.config, ["account-allowed", "account-allowed"])
        self.assertEqual(self.library.load_config(self.config)["allowed_account_ids"], ["account-allowed"])
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)

    def test_setup_accounts_marks_allowlist_without_message_reads(self):
        self.allow_account()
        output = io.StringIO()
        with patch.object(self.setup_module, "live_accounts", return_value=ACCOUNTS), redirect_stdout(output):
            self.assertEqual(self.setup_module.main(["--config", str(self.config), "accounts", "--json"]), 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload[0]["allowed"])
        self.assertFalse(payload[1]["allowed"])

    def test_setup_allow_is_dry_run_until_confirmed(self):
        with patch.object(self.setup_module, "live_accounts", return_value=ACCOUNTS), redirect_stdout(io.StringIO()):
            self.assertEqual(
                self.setup_module.main(["--config", str(self.config), "allow", "--account-id", "account-allowed"]),
                0,
            )
        self.assertFalse(self.config.exists())

        with patch.object(self.setup_module, "live_accounts", return_value=ACCOUNTS), redirect_stdout(io.StringIO()):
            self.assertEqual(
                self.setup_module.main(
                    ["--config", str(self.config), "allow", "--account-id", "account-allowed", "--confirm"]
                ),
                0,
            )
        self.assertEqual(self.library.load_config(self.config)["allowed_account_ids"], ["account-allowed"])

    def test_setup_rejects_unknown_account(self):
        stderr = io.StringIO()
        with patch.object(self.setup_module, "live_accounts", return_value=ACCOUNTS), redirect_stderr(stderr):
            rc = self.setup_module.main(
                ["--config", str(self.config), "allow", "--account-id", "not-real", "--confirm"]
            )
        self.assertEqual(rc, 1)
        self.assertFalse(self.config.exists())
        self.assertIn("Unknown Apple Mail account", stderr.getvalue())

    def test_read_fails_closed_without_allowed_accounts(self):
        stderr = io.StringIO()
        with patch.object(self.read_module, "live_accounts", return_value=ACCOUNTS), redirect_stderr(stderr):
            rc = self.read_module.main(["status", "--config", str(self.config)])
        self.assertEqual(rc, 1)
        self.assertIn("No Apple Mail accounts are allowed", stderr.getvalue())

    def test_read_rejects_account_outside_allowlist(self):
        self.allow_account()
        stderr = io.StringIO()
        with patch.object(self.read_module, "live_accounts", return_value=ACCOUNTS), redirect_stderr(stderr):
            rc = self.read_module.main(
                ["inbox", "--config", str(self.config), "--account-id", "account-denied"]
            )
        self.assertEqual(rc, 1)
        self.assertIn("not allowed", stderr.getvalue())

    def test_inbox_reads_only_allowed_account_and_excludes_content(self):
        self.allow_account()
        calls = []

        def fake_bridge(command, args=None):
            calls.append((command, args))
            return {"mailbox": "INBOX", "scanned": 1, "messages": [dict(MESSAGE)]}

        output = io.StringIO()
        with (
            patch.object(self.read_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.read_module, "run_bridge", side_effect=fake_bridge),
            redirect_stdout(output),
        ):
            rc = self.read_module.main(["inbox", "--config", str(self.config), "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(output.getvalue())
        self.assertNotIn("content", payload["messages"][0])
        self.assertEqual(calls[0][1][0], "account-allowed")
        self.assertEqual(calls[0][1][7], "none")
        self.assertEqual(calls[1][1][7], "preview")
        self.assertEqual(calls[1][1][9], "160")
        self.assertEqual(json.loads(calls[1][1][10]), ["42"])

    def test_unread_and_search_pass_bounded_filters(self):
        self.allow_account()
        calls = []

        def fake_bridge(command, args=None):
            calls.append(args)
            return {"mailbox": "INBOX", "scanned": 0, "messages": []}

        with (
            patch.object(self.read_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.read_module, "run_bridge", side_effect=fake_bridge),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                self.read_module.main(
                    ["unread", "--config", str(self.config), "--scan-limit", "100", "--limit", "10"]
                ),
                0,
            )
            self.assertEqual(
                self.read_module.main(["search", "invoice", "--config", str(self.config), "--days", "30"]),
                0,
            )
        self.assertEqual(calls[0][1:5], ["INBOX", "100", "10", "1"])
        self.assertEqual(calls[1][5], "invoice")
        self.assertTrue(calls[1][6])

    def test_multi_account_preview_selection_is_global_and_account_scoped(self):
        self.library.save_config(self.config, ["account-allowed", "account-denied"])
        calls = []
        metadata = {
            "account-allowed": [
                dict(MESSAGE, id=42, date_received="2026-07-21T14:00:00.000Z"),
                dict(MESSAGE, id=7, date_received="2026-07-19T14:00:00.000Z"),
            ],
            "account-denied": [
                dict(MESSAGE, id=42, date_received="2026-07-20T14:00:00.000Z"),
                dict(MESSAGE, id=8, date_received="2026-07-18T14:00:00.000Z"),
            ],
        }

        def fake_bridge(command, args=None):
            calls.append(list(args))
            account_id = args[0]
            mode = args[7]
            if mode == "none":
                rows = [
                    {key: value for key, value in message.items() if key not in {"preview", "preview_truncated", "to"}}
                    for message in metadata[account_id]
                ]
                return {"mailbox": "INBOX", "scanned": len(rows), "messages": rows}
            selected_ids = set(json.loads(args[10]))
            rows = []
            for message in metadata[account_id]:
                if str(message["id"]) in selected_ids:
                    rows.append(
                        dict(
                            message,
                            preview=f"preview-{account_id}",
                            preview_truncated=False,
                            to=[{"address": f"to-{account_id}@example.test"}],
                        )
                    )
            return {"mailbox": "INBOX", "scanned": len(metadata[account_id]), "messages": rows}

        output = io.StringIO()
        with (
            patch.object(self.read_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.read_module, "run_bridge", side_effect=fake_bridge),
            redirect_stdout(output),
        ):
            self.assertEqual(
                self.read_module.main(
                    ["inbox", "--config", str(self.config), "--limit", "2", "--json"]
                ),
                0,
            )

        messages = json.loads(output.getvalue())["messages"]
        self.assertEqual([message["account_id"] for message in messages], ["account-allowed", "account-denied"])
        self.assertEqual(messages[0]["preview"], "preview-account-allowed")
        self.assertEqual(messages[1]["preview"], "preview-account-denied")
        self.assertEqual([call[7] for call in calls[:2]], ["none", "none"])
        preview_calls = calls[2:]
        self.assertEqual({call[0] for call in preview_calls}, {"account-allowed", "account-denied"})
        self.assertTrue(all(json.loads(call[10]) == ["42"] for call in preview_calls))
        self.assertTrue(all("7" not in call[10] and "8" not in call[10] for call in preview_calls))

    def test_show_requests_content_for_exact_message(self):
        self.allow_account()
        detailed = dict(MESSAGE, content="Synthetic body", content_truncated=False)
        calls = []

        def fake_bridge(command, args=None):
            calls.append(args)
            return {"mailbox": "INBOX", "scanned": 1, "messages": [detailed]}

        output = io.StringIO()
        with (
            patch.object(self.read_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.read_module, "run_bridge", side_effect=fake_bridge),
            redirect_stdout(output),
        ):
            rc = self.read_module.main(
                [
                    "show",
                    "--config",
                    str(self.config),
                    "--account-id",
                    "account-allowed",
                    "--message-id",
                    "42",
                    "--json",
                ]
            )
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(output.getvalue())["message"]["content"], "Synthetic body")
        self.assertEqual(calls[0][7], "full")
        self.assertEqual(calls[0][8], "42")
        self.assertEqual(calls[0][9], "4000")

    def test_show_requires_one_exact_account_before_bridge_access(self):
        self.allow_account()
        stderr = io.StringIO()
        with (
            patch.object(self.read_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.read_module, "run_bridge") as bridge,
            redirect_stderr(stderr),
        ):
            rc = self.read_module.main(
                ["show", "--config", str(self.config), "--message-id", "42"]
            )
        self.assertEqual(rc, 1)
        bridge.assert_not_called()
        self.assertIn("exactly one --account-id", stderr.getvalue())

    def test_message_text_and_csv_are_compact_and_include_preview(self):
        self.allow_account()
        list_message = dict(
            MESSAGE,
            to=[{"address": f"recipient-{index}@example.test"} for index in range(10)],
            to_omitted=45,
        )

        def fake_bridge(command, args=None):
            if args[7] == "none":
                metadata = {
                    key: value
                    for key, value in list_message.items()
                    if key not in {"to", "to_omitted", "preview", "preview_truncated"}
                }
                return {"mailbox": "INBOX", "scanned": 1, "messages": [metadata]}
            return {"mailbox": "INBOX", "scanned": 1, "messages": [dict(list_message)]}

        text_output = io.StringIO()
        with (
            patch.object(self.read_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.read_module, "run_bridge", side_effect=fake_bridge),
            redirect_stdout(text_output),
        ):
            self.assertEqual(self.read_module.main(["inbox", "--config", str(self.config)]), 0)
        self.assertEqual(len(text_output.getvalue().splitlines()), 1)
        self.assertIn("from=Alex Example <alex@example.test>", text_output.getvalue())
        self.assertIn("account_id=account-allowed", text_output.getvalue())
        self.assertIn("to=recipient-0@example.test,recipient-1@example.test,recipient-2@example.test,…(+52)", text_output.getvalue())
        self.assertIn("preview=Synthetic preview", text_output.getvalue())

        csv_output = io.StringIO()
        with (
            patch.object(self.read_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.read_module, "run_bridge", side_effect=fake_bridge),
            redirect_stdout(csv_output),
        ):
            self.assertEqual(
                self.read_module.main(
                    ["inbox", "--config", str(self.config), "--format", "csv"]
                ),
                0,
            )
        rows = list(csv.DictReader(io.StringIO(csv_output.getvalue())))
        self.assertEqual(rows[0]["from"], "Alex Example <alex@example.test>")
        self.assertEqual(rows[0]["account_id"], "account-allowed")
        self.assertEqual(rows[0]["subject"], "Synthetic planning")
        self.assertEqual(rows[0]["preview"], "Synthetic preview")
        self.assertTrue(rows[0]["to"].endswith("…(+52)"))

    def test_recipient_rendering_is_bounded(self):
        recipients = [
            {"address": f"recipient-{index}@example.test"}
            for index in range(10)
        ]
        rendered = self.read_module.address_text(recipients)
        self.assertIn("recipient-0@example.test", rendered)
        self.assertIn("recipient-2@example.test", rendered)
        self.assertNotIn("recipient-3@example.test", rendered)
        self.assertTrue(rendered.endswith("…(+7)"))
        self.assertLessEqual(len(rendered), 240)
        capped_rendered = self.read_module.address_text(recipients, omitted=45)
        self.assertTrue(capped_rendered.endswith("…(+52)"))

    def test_locator_fields_round_trip_without_truncation(self):
        account_id = "A" * 120
        mailbox = "Nested/" + "M" * 220
        message = dict(
            MESSAGE,
            account_id=account_id,
            account_name="Synthetic",
            mailbox=mailbox,
        )
        text_output = io.StringIO()
        with redirect_stdout(text_output):
            self.read_module.print_messages([message])
        self.assertIn(f"account_id={account_id}", text_output.getvalue())
        self.assertIn(f"mailbox={mailbox}", text_output.getvalue())

        csv_output = io.StringIO()
        with redirect_stdout(csv_output):
            self.read_module.print_messages_csv([message])
        row = list(csv.DictReader(io.StringIO(csv_output.getvalue())))[0]
        self.assertEqual(row["account_id"], account_id)
        self.assertEqual(row["mailbox"], mailbox)

    def test_csv_neutralizes_formula_cells_and_text_stays_one_line(self):
        self.allow_account()
        adversarial_accounts = [dict(ACCOUNTS[0], name="\t=ACCOUNT()")]
        adversarial = dict(
            MESSAGE,
            sender="\r\n@sender.example.test",
            subject='=SUBJECT("quoted,cell")\u2028continued',
            preview=" +PREVIEW()\u2029continued",
            to=[{"name": "", "address": "-recipient@example.test"}],
        )

        def fake_bridge(command, args=None):
            return {"mailbox": "%20%2BMAILBOX()", "scanned": 1, "messages": [adversarial]}

        csv_output = io.StringIO()
        with (
            patch.object(self.read_module, "live_accounts", return_value=adversarial_accounts),
            patch.object(self.read_module, "run_bridge", side_effect=fake_bridge),
            redirect_stdout(csv_output),
        ):
            self.assertEqual(
                self.read_module.main(
                    ["inbox", "--config", str(self.config), "--format", "csv"]
                ),
                0,
            )
        row = list(csv.DictReader(io.StringIO(csv_output.getvalue())))[0]
        for field in ("account", "from", "to", "subject", "preview"):
            self.assertTrue(row[field].startswith("'"), field)
        self.assertEqual(row["mailbox"], "%20%2BMAILBOX()")
        self.assertIn('"quoted,cell"', row["subject"])

        text_output = io.StringIO()
        with (
            patch.object(self.read_module, "live_accounts", return_value=adversarial_accounts),
            patch.object(self.read_module, "run_bridge", side_effect=fake_bridge),
            redirect_stdout(text_output),
        ):
            self.assertEqual(self.read_module.main(["inbox", "--config", str(self.config)]), 0)
        self.assertEqual(len(text_output.getvalue().splitlines()), 1)
        self.assertNotIn("\u2028", text_output.getvalue())
        self.assertNotIn("\u2029", text_output.getvalue())

    def test_format_compatibility_and_conflicts(self):
        parser = self.read_module.build_parser()
        self.assertTrue(parser.parse_args(["inbox", "--json"]).json)
        self.assertEqual(parser.parse_args(["inbox", "--format", "csv"]).format, "csv")
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            parser.parse_args(["inbox", "--json", "--format", "csv"])
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            parser.parse_args(["show", "--account-id", "account-allowed", "--message-id", "42", "--format", "csv"])

    def test_show_text_is_header_block_plus_bounded_body(self):
        self.allow_account()
        detailed = dict(MESSAGE, content="Synthetic\nbody", content_truncated=False)

        def fake_bridge(command, args=None):
            return {"mailbox": "INBOX", "scanned": 1, "messages": [detailed]}

        output = io.StringIO()
        with (
            patch.object(self.read_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.read_module, "run_bridge", side_effect=fake_bridge),
            redirect_stdout(output),
        ):
            self.assertEqual(
                self.read_module.main(
                    [
                        "show",
                        "--config",
                        str(self.config),
                        "--account-id",
                        "account-allowed",
                        "--message-id",
                        "42",
                        "--body-chars",
                        "1200",
                    ]
                ),
                0,
            )
        self.assertIn("from: Alex Example <alex@example.test>", output.getvalue())
        self.assertIn("account_id: account-allowed", output.getvalue())
        self.assertIn("to: taylor@example.test", output.getvalue())
        self.assertIn("subject: Synthetic planning", output.getvalue())
        self.assertIn("body:\nSynthetic\nbody", output.getvalue())

    def test_cli_rejects_unbounded_limits(self):
        stderr = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(stderr):
            self.read_module.main(["inbox", "--limit", "501"])
        self.assertIn("limit must be between 1 and 500", stderr.getvalue())

    def test_content_budget_boundaries_and_zero_preview_mode(self):
        for value, accepted in (("0", True), ("1", True), ("500", True), ("501", False), ("-1", False)):
            with self.subTest(kind="preview", value=value):
                if accepted:
                    self.read_module.build_parser().parse_args(["inbox", "--preview-chars", value])
                else:
                    with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                        self.read_module.build_parser().parse_args(["inbox", "--preview-chars", value])
        for value, accepted in (("200", True), ("20000", True), ("199", False), ("20001", False)):
            with self.subTest(kind="body", value=value):
                argv = ["show", "--account-id", "account-allowed", "--message-id", "42", "--body-chars", value]
                if accepted:
                    self.read_module.build_parser().parse_args(argv)
                else:
                    with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                        self.read_module.build_parser().parse_args(argv)

        self.allow_account()
        calls = []

        def fake_bridge(command, args=None):
            calls.append(args)
            return {"mailbox": "INBOX", "scanned": 1, "messages": [dict(MESSAGE)]}

        with (
            patch.object(self.read_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.read_module, "run_bridge", side_effect=fake_bridge),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                self.read_module.main(
                    ["inbox", "--config", str(self.config), "--preview-chars", "0"]
                ),
                0,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][7], "none")

    def payload_path(self, **changes):
        payload = {
            "account_id": "account-allowed",
            "from": "allowed@example.test",
            "to": ["recipient@example.test"],
            "cc": [],
            "bcc": [],
            "subject": "Synthetic subject",
            "body": "Synthetic body",
        }
        payload.update(changes)
        path = Path(self.tmp.name) / f"payload-{len(list(Path(self.tmp.name).glob('payload-*')))}.json"
        path.write_text(json.dumps({"email": payload}), encoding="utf-8")
        return path

    def test_write_status_is_non_mutating(self):
        self.allow_account()
        output = io.StringIO()
        with patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS), redirect_stdout(output):
            self.assertEqual(self.write_module.main(["--config", str(self.config), "status"]), 0)
        self.assertIn("allowed_sender_accounts=1", output.getvalue())

    def test_draft_and_send_are_dry_run_by_default(self):
        self.allow_account()
        payload_path = self.payload_path()
        for operation in ("draft", "send"):
            with self.subTest(operation=operation):
                output = io.StringIO()
                with (
                    patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
                    patch.object(self.write_module, "run_write_bridge") as bridge,
                    redirect_stdout(output),
                ):
                    rc = self.write_module.main(
                        ["--config", str(self.config), operation, "--payload", str(payload_path)]
                    )
                self.assertEqual(rc, 0)
                bridge.assert_not_called()
                self.assertIn("dry-run", output.getvalue())
                self.assertIn("body_sha256=", output.getvalue())

    def test_confirmed_draft_and_send_invoke_exact_operation(self):
        self.allow_account()
        payload_path = self.payload_path()
        for operation in ("draft", "send"):
            with self.subTest(operation=operation):
                calls = []

                def fake_bridge(action, payload):
                    calls.append((action, payload))
                    return {"status": "ok", "operation": action, "attachments": len(payload["attachments"])}

                with patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS):
                    message = self.write_module.normalize_payload(
                        self.write_module.load_payload(payload_path), str(self.config)
                    )
                    action_hash = self.write_module.action_sha256(operation, message)
                    confirmation, _ = self.write_module.issue_confirmation(
                        action_hash, self.write_module.approval_store_for(str(self.config))
                    )

                with (
                    patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
                    patch.object(self.write_module, "run_write_bridge", side_effect=fake_bridge),
                    redirect_stdout(io.StringIO()),
                ):
                    rc = self.write_module.main(
                        [
                            "--config",
                            str(self.config),
                            operation,
                            "--payload",
                            str(payload_path),
                            "--confirm",
                            confirmation,
                        ]
                    )
                self.assertEqual(rc, 0)
                self.assertEqual(calls[0][0], operation)
                self.assertEqual(calls[0][1]["account_id"], "account-allowed")
                self.assertEqual(calls[0][1]["from"], "allowed@example.test")
                self.assertEqual(calls[0][1]["to"], ["recipient@example.test"])
                self.assertEqual(calls[0][1]["attachments"], [])

    def test_write_rejects_sender_not_on_selected_account(self):
        self.allow_account()
        payload_path = self.payload_path(**{"from": "denied@example.test"})
        stderr = io.StringIO()
        with (
            patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.write_module, "run_write_bridge") as bridge,
            redirect_stderr(stderr),
        ):
            rc = self.write_module.main(["--config", str(self.config), "send", "--payload", str(payload_path)])
        self.assertEqual(rc, 1)
        bridge.assert_not_called()
        self.assertIn("from address is not configured", stderr.getvalue())

    def test_write_rejects_disallowed_account_and_empty_recipient_list(self):
        self.allow_account()
        denied = self.payload_path(account_id="account-denied", **{"from": "denied@example.test"})
        empty = self.payload_path(to=[])
        for payload_path, expected in ((denied, "not allowed"), (empty, "at least one")):
            with self.subTest(expected=expected):
                stderr = io.StringIO()
                with (
                    patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
                    patch.object(self.write_module, "run_write_bridge") as bridge,
                    redirect_stderr(stderr),
                ):
                    rc = self.write_module.main(
                        ["--config", str(self.config), "draft", "--payload", str(payload_path)]
                    )
                self.assertEqual(rc, 1)
                bridge.assert_not_called()
                self.assertIn(expected, stderr.getvalue())

    def test_write_rejects_ambiguous_sender_across_accounts(self):
        self.allow_account()
        ambiguous_accounts = [dict(ACCOUNTS[0]), dict(ACCOUNTS[1], email_addresses=["allowed@example.test"])]
        stderr = io.StringIO()
        with patch.object(self.write_module, "live_accounts", return_value=ambiguous_accounts), redirect_stderr(stderr):
            rc = self.write_module.main(
                ["--config", str(self.config), "send", "--payload", str(self.payload_path())]
            )
        self.assertEqual(rc, 1)
        self.assertIn("map uniquely", stderr.getvalue())

    def test_confirmation_hash_binds_the_full_action(self):
        self.allow_account()
        payload_path = self.payload_path()
        with patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS):
            message = self.write_module.normalize_payload(
                self.write_module.load_payload(payload_path), str(self.config)
            )
        baseline = self.write_module.action_sha256("send", message)
        mutations = [
            ("draft", dict(message)),
            ("send", dict(message, account_id="different-account")),
            ("send", dict(message, **{"from": "different@example.test"})),
            ("send", dict(message, to=["different@example.test"])),
            ("send", dict(message, cc=["different@example.test"])),
            ("send", dict(message, bcc=["different@example.test"])),
            ("send", dict(message, subject="Different subject")),
            ("send", dict(message, body="Different body")),
        ]
        for operation, changed in mutations:
            with self.subTest(operation=operation, changed=changed):
                self.assertNotEqual(self.write_module.action_sha256(operation, changed), baseline)

        with (
            patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.write_module, "run_write_bridge") as bridge,
            redirect_stderr(io.StringIO()),
        ):
            rc = self.write_module.main(
                ["--config", str(self.config), "send", "--payload", str(payload_path), "--confirm", "0" * 64]
            )
        self.assertEqual(rc, 1)
        bridge.assert_not_called()

    def test_confirmed_action_rejects_invalid_bridge_success(self):
        self.allow_account()
        payload_path = self.payload_path()
        for response in (
            {},
            {"status": "ok", "operation": "draft"},
            ["ok"],
            {"status": "error", "operation": "send"},
            {"status": "ok", "operation": "send"},
            {"status": "ok", "operation": "send", "attachments": 1},
        ):
            with self.subTest(response=response):
                with patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS):
                    message = self.write_module.normalize_payload(
                        self.write_module.load_payload(payload_path), str(self.config)
                    )
                    action_hash = self.write_module.action_sha256("send", message)
                    confirmation, _ = self.write_module.issue_confirmation(
                        action_hash, self.write_module.approval_store_for(str(self.config))
                    )
                stderr = io.StringIO()
                with (
                    patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
                    patch.object(self.write_module, "run_write_bridge", return_value=response),
                    redirect_stderr(stderr),
                ):
                    rc = self.write_module.main(
                        [
                            "--config",
                            str(self.config),
                            "send",
                            "--payload",
                            str(payload_path),
                            "--confirm",
                            confirmation,
                        ]
                    )
                self.assertEqual(rc, 1)
                self.assertIn("valid success confirmation", stderr.getvalue())

    def test_confirmation_token_is_one_time(self):
        self.allow_account()
        payload_path = self.payload_path()
        with patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS):
            message = self.write_module.normalize_payload(
                self.write_module.load_payload(payload_path), str(self.config)
            )
            action_hash = self.write_module.action_sha256("send", message)
            token, _ = self.write_module.issue_confirmation(
                action_hash, self.write_module.approval_store_for(str(self.config))
            )
        bridge = Mock(return_value={"status": "ok", "operation": "send", "attachments": 0})
        command = [
            "--config",
            str(self.config),
            "send",
            "--payload",
            str(payload_path),
            "--confirm",
            token,
        ]
        with (
            patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.write_module, "run_write_bridge", bridge),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(self.write_module.main(command), 0)
        with (
            patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.write_module, "run_write_bridge", bridge),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(self.write_module.main(command), 1)
        self.assertEqual(bridge.call_count, 1)

    def attachment_file(self, name="report.txt", content="synthetic attachment"):
        path = Path(self.tmp.name) / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_attachment_dry_run_lists_every_file_for_approval(self):
        self.allow_account()
        first = self.attachment_file("first.txt", "one")
        second = self.attachment_file("second.txt", "two")
        payload_path = self.payload_path(attachments=[str(first), str(second)])
        output = io.StringIO()
        with (
            patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.write_module, "run_write_bridge") as bridge,
            redirect_stdout(output),
        ):
            rc = self.write_module.main(
                ["--config", str(self.config), "draft", "--payload", str(payload_path)]
            )
        self.assertEqual(rc, 0)
        bridge.assert_not_called()
        printed = output.getvalue()
        self.assertIn("attachments=2", printed)
        self.assertIn("attachment_bytes=6", printed)
        self.assertIn("attachment[0]=first.txt", printed)
        self.assertIn("attachment[1]=second.txt", printed)
        self.assertIn(f"path={second.resolve()}", printed)
        self.assertIn(
            "sha256=" + hashlib.sha256(b"one").hexdigest(),
            printed,
        )

    def test_confirmed_action_passes_resolved_attachment_paths(self):
        self.allow_account()
        attachment = self.attachment_file()
        link = Path(self.tmp.name) / "link-to-report.txt"
        link.symlink_to(attachment)
        payload_path = self.payload_path(attachments=[str(link)])
        calls = []

        def fake_bridge(action, payload):
            calls.append((action, payload))
            return {"status": "ok", "operation": action, "attachments": len(payload["attachments"])}

        with patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS):
            message = self.write_module.normalize_payload(
                self.write_module.load_payload(payload_path), str(self.config)
            )
            token, _ = self.write_module.issue_confirmation(
                self.write_module.action_sha256("draft", message),
                self.write_module.approval_store_for(str(self.config)),
            )
        with (
            patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.write_module, "run_write_bridge", side_effect=fake_bridge),
            redirect_stdout(io.StringIO()),
        ):
            rc = self.write_module.main(
                [
                    "--config",
                    str(self.config),
                    "draft",
                    "--payload",
                    str(payload_path),
                    "--confirm",
                    token,
                ]
            )
        self.assertEqual(rc, 0)
        self.assertEqual(calls[0][1]["attachments"], [str(attachment.resolve())])

    def test_confirmation_binds_attachment_contents(self):
        self.allow_account()
        attachment = self.attachment_file()
        payload_path = self.payload_path(attachments=[str(attachment)])
        with patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS):
            approved = self.write_module.normalize_payload(
                self.write_module.load_payload(payload_path), str(self.config)
            )
            token, _ = self.write_module.issue_confirmation(
                self.write_module.action_sha256("draft", approved),
                self.write_module.approval_store_for(str(self.config)),
            )
        self.assertNotEqual(
            self.write_module.action_sha256("draft", dict(approved, attachments=[])),
            self.write_module.action_sha256("draft", approved),
        )
        attachment.write_text("swapped after approval", encoding="utf-8")
        stderr = io.StringIO()
        with (
            patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.write_module, "run_write_bridge") as bridge,
            redirect_stderr(stderr),
        ):
            rc = self.write_module.main(
                [
                    "--config",
                    str(self.config),
                    "draft",
                    "--payload",
                    str(payload_path),
                    "--confirm",
                    token,
                ]
            )
        self.assertEqual(rc, 1)
        bridge.assert_not_called()
        self.assertIn("belongs to another action", stderr.getvalue())

    def test_attachment_payloads_fail_closed(self):
        self.allow_account()
        directory = Path(self.tmp.name) / "folder"
        directory.mkdir()
        oversize = Path(self.tmp.name) / "oversize.bin"
        with oversize.open("wb") as handle:
            handle.truncate(self.write_module.MAX_ATTACHMENT_BYTES + 1)
        half = Path(self.tmp.name) / "half.bin"
        with half.open("wb") as handle:
            handle.truncate(self.write_module.MAX_ATTACHMENT_TOTAL_BYTES // 2 + 1)
        readable = self.attachment_file()
        cases = (
            ("missing", [str(Path(self.tmp.name) / "absent.txt")], "does not exist"),
            ("directory", [str(directory)], "not a regular file"),
            ("not-a-list", str(readable), "must be a list"),
            ("blank", [" "], "nonempty local file path"),
            ("not-a-string", [42], "nonempty local file path"),
            ("control-character", [f"{readable.parent}/\nreport.txt"], "control characters"),
            ("too-many", [str(readable)] * (self.write_module.MAX_ATTACHMENTS + 1), "at most"),
            ("oversize", [str(oversize)], "byte limit"),
            ("total-oversize", [str(half), str(half)], "total limit"),
        )
        for label, attachments, expected in cases:
            with self.subTest(case=label):
                stderr = io.StringIO()
                with (
                    patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
                    patch.object(self.write_module, "run_write_bridge") as bridge,
                    redirect_stderr(stderr),
                ):
                    rc = self.write_module.main(
                        [
                            "--config",
                            str(self.config),
                            "draft",
                            "--payload",
                            str(self.payload_path(attachments=attachments)),
                        ]
                    )
                self.assertEqual(rc, 1)
                bridge.assert_not_called()
                self.assertIn(expected, stderr.getvalue())

    def test_write_rejects_subject_control_characters(self):
        self.allow_account()
        stderr = io.StringIO()
        with patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS), redirect_stderr(stderr):
            rc = self.write_module.main(
                [
                    "--config",
                    str(self.config),
                    "draft",
                    "--payload",
                    str(self.payload_path(subject="Synthetic\nforged")),
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("control characters", stderr.getvalue())

    SCHEDULE_NOW = 1_800_000_000

    def at_offset(self, seconds):
        return datetime.fromtimestamp(self.SCHEDULE_NOW + seconds, tz=timezone.utc).isoformat()

    def schedule_store_path(self):
        return self.write_module.schedule_store_for(str(self.config))

    def stored_items(self):
        return self.write_module.read_scheduled(self.schedule_store_path())

    def replace_stored(self, items):
        self.write_module.write_scheduled(self.schedule_store_path(), items)

    def schedule_command(self, at, expire, payload_path):
        return [
            "--config", str(self.config), "schedule",
            "--payload", str(payload_path), "--at", at,
            "--expire-after-minutes", str(expire), "--json",
        ]

    def schedule_token(self, at, expire, payload_path):
        with patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS):
            message = self.write_module.normalize_payload(
                self.write_module.load_payload(payload_path), str(self.config)
            )
            schedule = {
                "send_at": self.write_module.parse_send_at(at),
                "expire_after_minutes": expire,
            }
            token, _ = self.write_module.issue_confirmation(
                self.write_module.action_sha256("schedule", message, schedule),
                self.write_module.approval_store_for(str(self.config)),
            )
        return token

    def enqueue(self, at, expire=1440, payload_path=None, now=None):
        payload_path = payload_path or self.payload_path()
        token = self.schedule_token(at, expire, payload_path)
        output = io.StringIO()
        with (
            patch.object(self.write_module, "now_epoch", return_value=now or self.SCHEDULE_NOW),
            patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.write_module, "run_write_bridge") as bridge,
            redirect_stdout(output),
        ):
            rc = self.write_module.main(
                self.schedule_command(at, expire, payload_path) + ["--confirm", token]
            )
        self.assertEqual(rc, 0)
        bridge.assert_not_called()
        return json.loads(output.getvalue())

    def run_due(self, now, bridge=None, extra=()):
        bridge = bridge or Mock(return_value={"status": "ok", "operation": "send", "attachments": 0})
        output = io.StringIO()
        with (
            patch.object(self.write_module, "now_epoch", return_value=now),
            patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.write_module, "run_write_bridge", bridge),
            redirect_stdout(output),
        ):
            rc = self.write_module.main(["--config", str(self.config), "run-due", "--json", *extra])
        self.assertEqual(rc, 0)
        return json.loads(output.getvalue()), bridge

    def test_schedule_is_dry_run_and_queues_nothing_until_confirmed(self):
        self.allow_account()
        at = self.at_offset(3600)
        output = io.StringIO()
        with (
            patch.object(self.write_module, "now_epoch", return_value=self.SCHEDULE_NOW),
            patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.write_module, "run_write_bridge") as bridge,
            redirect_stdout(output),
        ):
            rc = self.write_module.main(
                ["--config", str(self.config), "schedule",
                 "--payload", str(self.payload_path()), "--at", at]
            )
        self.assertEqual(rc, 0)
        bridge.assert_not_called()
        printed = output.getvalue()
        self.assertIn("dry-run: would schedule Apple Mail send", printed)
        self.assertIn(f"send_at={at}", printed)
        self.assertIn("expire_after_minutes=1440", printed)
        self.assertIn("confirmation_token=", printed)
        self.assertEqual(self.stored_items(), [])

    def test_confirmed_schedule_queues_without_sending_and_stays_owner_only(self):
        self.allow_account()
        payload = self.enqueue(self.at_offset(3600))
        self.assertTrue(payload["schedule_id"].startswith("sch_"))
        self.assertEqual(self.schedule_store_path().stat().st_mode & 0o777, 0o600)
        items = self.stored_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "pending")
        self.assertEqual(items[0]["send_at"], self.SCHEDULE_NOW + 3600)
        self.assertEqual(items[0]["message"]["body"], "Synthetic body")

    def test_schedule_confirmation_binds_the_time_and_the_expiry_window(self):
        self.allow_account()
        payload_path = self.payload_path()
        with patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS):
            message = self.write_module.normalize_payload(
                self.write_module.load_payload(payload_path), str(self.config)
            )
        baseline = self.write_module.action_sha256(
            "schedule", message, {"send_at": self.SCHEDULE_NOW + 3600, "expire_after_minutes": 1440}
        )
        self.assertNotEqual(
            baseline,
            self.write_module.action_sha256(
                "schedule", message, {"send_at": self.SCHEDULE_NOW + 7200, "expire_after_minutes": 1440}
            ),
        )
        self.assertNotEqual(
            baseline,
            self.write_module.action_sha256(
                "schedule", message, {"send_at": self.SCHEDULE_NOW + 3600, "expire_after_minutes": 60}
            ),
        )
        self.assertNotEqual(baseline, self.write_module.action_sha256("send", message))

        token = self.schedule_token(self.at_offset(3600), 1440, payload_path)
        stderr = io.StringIO()
        with (
            patch.object(self.write_module, "now_epoch", return_value=self.SCHEDULE_NOW),
            patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
            patch.object(self.write_module, "run_write_bridge") as bridge,
            redirect_stderr(stderr),
        ):
            rc = self.write_module.main(
                self.schedule_command(self.at_offset(7200), 1440, payload_path) + ["--confirm", token]
            )
        self.assertEqual(rc, 1)
        bridge.assert_not_called()
        self.assertIn("belongs to another action", stderr.getvalue())
        self.assertEqual(self.stored_items(), [])

    def test_schedule_rejects_unusable_times(self):
        self.allow_account()
        cases = (
            (self.at_offset(-60), 1440, "must be in the future"),
            (self.at_offset(400 * 24 * 3600), 1440, "within 365 days"),
            ("next tuesday", 1440, "ISO 8601"),
            (self.at_offset(3600), 0, "expire-after-minutes"),
        )
        for at, expire, expected in cases:
            with self.subTest(at=at, expire=expire):
                stderr = io.StringIO()
                with (
                    patch.object(self.write_module, "now_epoch", return_value=self.SCHEDULE_NOW),
                    patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
                    patch.object(self.write_module, "run_write_bridge") as bridge,
                    redirect_stderr(stderr),
                ):
                    rc = self.write_module.main(
                        self.schedule_command(at, expire, self.payload_path())
                    )
                self.assertEqual(rc, 1)
                bridge.assert_not_called()
                self.assertIn(expected, stderr.getvalue())
        self.assertEqual(self.stored_items(), [])

    def test_run_due_sends_only_at_the_scheduled_time_and_only_once(self):
        self.allow_account()
        identifier = self.enqueue(self.at_offset(3600))["schedule_id"]

        early, bridge = self.run_due(self.SCHEDULE_NOW + 60)
        self.assertEqual(early["sent"], 0)
        bridge.assert_not_called()
        self.assertEqual(self.stored_items()[0]["status"], "pending")

        due, bridge = self.run_due(self.SCHEDULE_NOW + 3600)
        self.assertEqual(due["sent"], 1)
        self.assertEqual(bridge.call_count, 1)
        operation, sent_payload = bridge.call_args[0]
        self.assertEqual(operation, "send")
        self.assertEqual(sent_payload["from"], "allowed@example.test")
        self.assertEqual(sent_payload["to"], ["recipient@example.test"])
        self.assertEqual(sent_payload["subject"], "Synthetic subject")
        self.assertEqual(due["results"][0]["id"], identifier)

        again, bridge = self.run_due(self.SCHEDULE_NOW + 7200)
        self.assertEqual(again["sent"], 0)
        bridge.assert_not_called()
        self.assertEqual(self.stored_items()[0]["status"], "sent")

    def test_run_due_expires_an_overdue_send_instead_of_sending_it(self):
        self.allow_account()
        self.enqueue(self.at_offset(3600), expire=60)
        payload, bridge = self.run_due(self.SCHEDULE_NOW + 3600 + 61 * 60)
        bridge.assert_not_called()
        self.assertEqual(payload["expired"], 1)
        self.assertEqual(self.stored_items()[0]["status"], "expired")
        self.assertIn("Not sent within 60 minutes", self.stored_items()[0]["error"])

    def test_run_due_dry_run_reports_without_claiming_or_sending(self):
        self.allow_account()
        self.enqueue(self.at_offset(3600))
        payload, bridge = self.run_due(self.SCHEDULE_NOW + 3600, extra=["--dry-run"])
        bridge.assert_not_called()
        self.assertEqual(payload["due"], 1)
        self.assertEqual(self.stored_items()[0]["status"], "pending")

    def test_run_due_on_an_empty_queue_writes_nothing(self):
        self.allow_account()
        payload, bridge = self.run_due(self.SCHEDULE_NOW)
        bridge.assert_not_called()
        self.assertEqual(payload["sent"], 0)
        self.assertFalse(self.schedule_store_path().exists())

    def test_run_due_refuses_a_queue_entry_edited_after_approval(self):
        self.allow_account()
        self.enqueue(self.at_offset(3600))
        items = self.stored_items()
        items[0]["message"]["to"] = ["attacker@example.test"]
        self.replace_stored(items)
        payload, bridge = self.run_due(self.SCHEDULE_NOW + 3600)
        bridge.assert_not_called()
        self.assertEqual(payload["failed"], 1)
        self.assertIn("no longer matches the approved action", payload["results"][0]["error"])
        self.assertEqual(self.stored_items()[0]["status"], "failed")

    def test_run_due_refuses_an_attachment_changed_after_approval(self):
        self.allow_account()
        attachment = self.attachment_file("scheduled.txt", "approved bytes")
        self.enqueue(self.at_offset(3600), payload_path=self.payload_path(attachments=[str(attachment)]))
        attachment.write_text("swapped after approval", encoding="utf-8")
        payload, bridge = self.run_due(self.SCHEDULE_NOW + 3600)
        bridge.assert_not_called()
        self.assertEqual(payload["failed"], 1)
        self.assertIn("no longer matches the approved action", payload["results"][0]["error"])

    def test_run_due_fails_closed_when_the_account_allowance_is_revoked(self):
        self.allow_account()
        self.enqueue(self.at_offset(3600))
        self.library.save_config(self.config, [])
        payload, bridge = self.run_due(self.SCHEDULE_NOW + 3600)
        bridge.assert_not_called()
        self.assertEqual(payload["failed"], 1)
        self.assertIn("No Apple Mail accounts are allowed", payload["results"][0]["error"])

    def test_run_due_leaves_an_in_flight_send_alone(self):
        self.allow_account()
        self.enqueue(self.at_offset(3600))
        items = self.stored_items()
        items[0]["status"] = "sending"
        items[0]["attempt_started_at"] = self.SCHEDULE_NOW + 3600
        self.replace_stored(items)
        payload, bridge = self.run_due(self.SCHEDULE_NOW + 7200)
        bridge.assert_not_called()
        self.assertEqual(payload["indeterminate"], 1)
        self.assertEqual(self.stored_items()[0]["status"], "sending")

    def test_cancel_is_dry_run_until_confirmed_and_then_nothing_sends(self):
        self.allow_account()
        identifier = self.enqueue(self.at_offset(3600))["schedule_id"]
        output = io.StringIO()
        with (
            patch.object(self.write_module, "now_epoch", return_value=self.SCHEDULE_NOW),
            redirect_stdout(output),
        ):
            rc = self.write_module.main(["--config", str(self.config), "cancel", "--id", identifier])
        self.assertEqual(rc, 0)
        self.assertIn("dry-run: would cancel scheduled Apple Mail send", output.getvalue())
        self.assertEqual(self.stored_items()[0]["status"], "pending")

        token = output.getvalue().split("confirmation_token=")[1].splitlines()[0]
        with (
            patch.object(self.write_module, "now_epoch", return_value=self.SCHEDULE_NOW),
            redirect_stdout(io.StringIO()),
        ):
            rc = self.write_module.main(
                ["--config", str(self.config), "cancel", "--id", identifier, "--confirm", token]
            )
        self.assertEqual(rc, 0)
        self.assertEqual(self.stored_items()[0]["status"], "cancelled")

        payload, bridge = self.run_due(self.SCHEDULE_NOW + 3600)
        bridge.assert_not_called()
        self.assertEqual(payload["sent"], 0)

    def test_cancel_refuses_an_in_flight_or_finished_send(self):
        self.allow_account()
        identifier = self.enqueue(self.at_offset(3600))["schedule_id"]
        for status, expected in (("sending", "cannot be cancelled"), ("sent", "no longer pending")):
            with self.subTest(status=status):
                items = self.stored_items()
                items[0]["status"] = status
                self.replace_stored(items)
                stderr = io.StringIO()
                with (
                    patch.object(self.write_module, "now_epoch", return_value=self.SCHEDULE_NOW),
                    redirect_stderr(stderr),
                ):
                    rc = self.write_module.main(
                        ["--config", str(self.config), "cancel", "--id", identifier, "--confirm", "0" * 64]
                    )
                self.assertEqual(rc, 1)
                self.assertIn(expected, stderr.getvalue())

    def test_scheduled_listing_shows_locators_without_the_body(self):
        self.allow_account()
        identifier = self.enqueue(self.at_offset(3600))["schedule_id"]
        output = io.StringIO()
        with (
            patch.object(self.write_module, "now_epoch", return_value=self.SCHEDULE_NOW),
            redirect_stdout(output),
        ):
            rc = self.write_module.main(["--config", str(self.config), "scheduled", "--pending"])
        self.assertEqual(rc, 0)
        printed = output.getvalue()
        self.assertIn(f"id={identifier}", printed)
        self.assertIn("status=pending", printed)
        self.assertIn("subject=Synthetic subject", printed)
        self.assertNotIn("Synthetic body", printed)
        self.assertIn("scheduled sends: 1", printed)

    def test_duplicate_schedules_of_the_same_action_are_refused(self):
        self.allow_account()
        at = self.at_offset(3600)
        payload_path = self.payload_path()
        self.enqueue(at, payload_path=payload_path)
        token = self.schedule_token(at, 1440, payload_path)
        stderr = io.StringIO()
        with (
            patch.object(self.write_module, "now_epoch", return_value=self.SCHEDULE_NOW),
            patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
            redirect_stderr(stderr),
        ):
            rc = self.write_module.main(
                self.schedule_command(at, 1440, payload_path) + ["--confirm", token]
            )
        self.assertEqual(rc, 1)
        self.assertIn("already scheduled", stderr.getvalue())
        self.assertEqual(len(self.stored_items()), 1)

    def test_schedule_rejects_a_sender_outside_the_allowlist(self):
        self.allow_account()
        stderr = io.StringIO()
        with (
            patch.object(self.write_module, "now_epoch", return_value=self.SCHEDULE_NOW),
            patch.object(self.write_module, "live_accounts", return_value=ACCOUNTS),
            redirect_stderr(stderr),
        ):
            rc = self.write_module.main(
                self.schedule_command(
                    self.at_offset(3600), 1440,
                    self.payload_path(account_id="account-denied", **{"from": "denied@example.test"}),
                )
            )
        self.assertEqual(rc, 1)
        self.assertIn("not allowed", stderr.getvalue())
        self.assertEqual(self.stored_items(), [])

    def run_jxa(self, script, *args):
        result = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", str(TOOL_DIR / script), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return json.loads(result.stdout)

    def test_read_bridge_filters_before_content_and_truncates_matching_content(self):
        fixture = dict(MESSAGE, content="x" * 20001)
        rejected = self.run_jxa(
            "AppleMailBridge.js", "_test_record", json.dumps(fixture), "", "0", "different-id", "1"
        )
        self.assertFalse(rejected["matched"])
        self.assertEqual(rejected["accesses"]["content"], 0)

        matched = self.run_jxa(
            "AppleMailBridge.js", "_test_record", json.dumps(fixture), "", "0", "42", "1"
        )
        self.assertTrue(matched["matched"])
        self.assertEqual(matched["accesses"]["content"], 1)
        self.assertEqual(len(matched["record"]["content"]), 20000)
        self.assertTrue(matched["record"]["content_truncated"])

        preview_fixture = dict(MESSAGE, content="\ufffcSynthetic\npreview body")
        preview = self.run_jxa(
            "AppleMailBridge.js", "_test_record", json.dumps(preview_fixture), "", "0", "42", "preview", "12"
        )
        self.assertEqual(preview["record"]["preview"], "Synthetic pr")
        self.assertNotIn("\ufffc", preview["record"]["preview"])
        self.assertTrue(preview["record"]["preview_truncated"])

        emoji_fixture = dict(MESSAGE, content="a" * 159 + "😀" + "tail")
        emoji = self.run_jxa(
            "AppleMailBridge.js", "_test_record", json.dumps(emoji_fixture), "", "0", "42", "preview", "160"
        )
        self.assertEqual(emoji["record"]["preview"], "a" * 159 + "😀")
        emoji["record"]["preview"].encode("utf-8")

        bounded_fixture = dict(
            MESSAGE,
            content="bounded",
            to=[{"address": f"to-{index}@example.test"} for index in range(55)],
            cc=[{"address": f"cc-{index}@example.test"} for index in range(55)],
            attachments=[
                {
                    "id": f"attachment-{index}",
                    "name": "n" * 300,
                    "mime_type": "application/octet-stream",
                    "file_size": index,
                    "downloaded": True,
                }
                for index in range(25)
            ],
        )
        bounded = self.run_jxa(
            "AppleMailBridge.js", "_test_record", json.dumps(bounded_fixture), "", "0", "42", "full", "4000"
        )["record"]
        self.assertEqual(len(bounded["to"]), 50)
        self.assertEqual(bounded["to_omitted"], 5)
        self.assertEqual(len(bounded["cc"]), 50)
        self.assertEqual(bounded["cc_omitted"], 5)
        self.assertEqual(len(bounded["attachments"]), 20)
        self.assertEqual(bounded["attachments_omitted"], 5)
        self.assertEqual(len(bounded["attachments"][0]["name"]), 240)

    def test_read_bridge_scopes_exact_account_and_nested_mailbox_before_content(self):
        fixture = {
            "accounts": [
                {
                    "id": "account-allowed",
                    "mailboxes": [
                        {
                            "name": "Projects",
                            "mailboxes": [
                                {
                                    "name": "Client/Work",
                                    "messages": [
                                        dict(MESSAGE, id=7, content="Other body"),
                                        dict(MESSAGE, content="Exact body"),
                                    ],
                                }
                            ],
                        }
                    ],
                },
                {
                    "id": "account-denied",
                    "mailboxes": [{"name": "INBOX", "messages": [dict(MESSAGE, id=99, content="Denied body")]}],
                },
            ]
        }
        scoped = self.run_jxa(
            "AppleMailBridge.js",
            "_test_scope",
            json.dumps(fixture),
            "account-allowed",
            "Projects/Client%2FWork",
            "42",
        )
        self.assertEqual(scoped["payload"]["messages"][0]["content"], "Exact body")
        self.assertEqual(scoped["accesses"]["content"], 1)

        missing = self.run_jxa(
            "AppleMailBridge.js",
            "_test_scope",
            json.dumps(fixture),
            "account-allowed",
            "Projects/Client%2FWork",
            "not-present",
        )
        self.assertEqual(missing["payload"]["messages"], [])
        self.assertEqual(missing["accesses"]["content"], 0)

        denied = self.run_jxa(
            "AppleMailBridge.js", "_test_scope", json.dumps(fixture), "not-present", "INBOX", "99"
        )
        self.assertIn("No unique Mail account", denied["error"])
        self.assertEqual(denied["accesses"]["content"], 0)

        fixture["accounts"].append(dict(fixture["accounts"][0]))
        duplicate = self.run_jxa(
            "AppleMailBridge.js", "_test_scope", json.dumps(fixture), "account-allowed", "Projects", "42"
        )
        self.assertIn("No unique Mail account", duplicate["error"])
        self.assertEqual(duplicate["accesses"]["content"], 0)

    def test_read_bridge_hyphen_mailbox_path_is_safe_and_round_trips(self):
        account_fixture = {
            "id": "account-allowed",
            "mailboxes": [
                {
                    "name": "-Danger",
                    "messages": [dict(MESSAGE, content="Hyphen body")],
                    "mailboxes": [{"name": "Sub/Path", "messages": []}],
                }
            ],
        }
        mailboxes = self.run_jxa(
            "AppleMailBridge.js", "_test_mailboxes", json.dumps(account_fixture)
        )
        self.assertEqual(mailboxes[0]["path"], "%2DDanger")
        self.assertEqual(mailboxes[1]["path"], "%2DDanger/Sub%2FPath")

        scope_fixture = {"accounts": [account_fixture]}
        scoped = self.run_jxa(
            "AppleMailBridge.js",
            "_test_scope",
            json.dumps(scope_fixture),
            "account-allowed",
            mailboxes[0]["path"],
            "42",
        )
        self.assertEqual(scoped["payload"]["messages"][0]["content"], "Hyphen body")

    def test_write_bridge_revalidates_account_and_cleans_failed_send(self):
        payload = {
            "account_id": "account-allowed",
            "from": "allowed@example.test",
            "to": ["recipient@example.test"],
            "cc": [],
            "bcc": [],
            "subject": "Synthetic",
            "body": "Body",
            "synthetic_accounts": [{"id": "account-allowed", "email_addresses": ["allowed@example.test"]}],
            "test_operation": "send",
            "test_scenario": "send-fails",
        }
        failed = self.run_jxa("AppleMailWriteBridge.js", "_test_compose", json.dumps(payload))
        self.assertIn("did not confirm", failed["error"])
        self.assertEqual(failed["events"], ["push", "content:plain", "recipient", "send", "delete"])

        payload["synthetic_accounts"].append(
            {"id": "account-other", "email_addresses": ["allowed@example.test"]}
        )
        ambiguous = self.run_jxa("AppleMailWriteBridge.js", "_test_compose", json.dumps(payload))
        self.assertIn("exactly one", ambiguous["error"])
        self.assertEqual(ambiguous["events"], [])

        payload["synthetic_accounts"] = [
            {"id": "account-other", "email_addresses": ["allowed@example.test"]}
        ]
        wrong_account = self.run_jxa("AppleMailWriteBridge.js", "_test_compose", json.dumps(payload))
        self.assertIn("approved Mail account", wrong_account["error"])
        self.assertEqual(wrong_account["events"], [])

        payload["synthetic_accounts"] = [
            {"id": "account-allowed", "email_addresses": ["allowed@example.test"]}
        ]
        payload["test_operation"] = "draft"
        payload["test_scenario"] = "save-fails"
        failed_draft = self.run_jxa("AppleMailWriteBridge.js", "_test_compose", json.dumps(payload))
        self.assertIn("synthetic save failure", failed_draft["error"])
        self.assertEqual(
            failed_draft["events"],
            ["push", "content:plain", "recipient", "save", "delete"],
        )

    def test_write_bridge_attaches_after_insertion_and_cleans_failed_attachments(self):
        payload = {
            "account_id": "account-allowed",
            "from": "allowed@example.test",
            "to": ["recipient@example.test"],
            "cc": [],
            "bcc": [],
            "subject": "Synthetic",
            "body": "Body",
            "attachments": ["/tmp/synthetic-one.txt", "/tmp/synthetic-two.txt"],
            "synthetic_accounts": [{"id": "account-allowed", "email_addresses": ["allowed@example.test"]}],
            "test_operation": "draft",
            "test_scenario": "ok",
        }
        saved = self.run_jxa("AppleMailWriteBridge.js", "_test_compose", json.dumps(payload))
        self.assertEqual(saved["result"], {"status": "ok", "operation": "draft", "attachments": 2})
        self.assertEqual(
            saved["events"],
            ["push", "content:separated", "recipient", "attach", "attach", "save"],
        )
        self.assertNotIn("constructor-content", saved["events"])

        payload["test_scenario"] = "attach-fails"
        failed = self.run_jxa("AppleMailWriteBridge.js", "_test_compose", json.dumps(payload))
        self.assertIn("synthetic attach failure", failed["error"])
        self.assertEqual(
            failed["events"],
            ["push", "content:separated", "recipient", "attach", "delete"],
        )

        payload["test_scenario"] = "ok"
        payload["attachments"] = ["relative/report.txt"]
        relative = self.run_jxa("AppleMailWriteBridge.js", "_test_compose", json.dumps(payload))
        self.assertIn("absolute local file paths", relative["error"])
        self.assertEqual(relative["events"], ["push", "content:separated", "recipient", "delete"])


if __name__ == "__main__":
    unittest.main()
