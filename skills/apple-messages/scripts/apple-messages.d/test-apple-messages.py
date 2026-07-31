#!/usr/bin/env python3
"""Tests for apple-messages."""

from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


TOOL_DIR = Path(__file__).resolve().parent
READ_SCRIPT = TOOL_DIR / "apple-messages-read.py"
SEND_SCRIPT = TOOL_DIR / "apple-messages-send.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def apple_ns(timestamp: int) -> int:
    return (timestamp - 978_307_200) * 1_000_000_000


def create_messages_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            create table chat (
                ROWID integer primary key,
                guid text,
                display_name text,
                chat_identifier text,
                service_name text
            );
            create table handle (
                ROWID integer primary key,
                id text,
                service text,
                uncanonicalized_id text,
                person_centric_id text
            );
            create table message (
                ROWID integer primary key,
                guid text,
                text text,
                attributedBody blob,
                handle_id integer,
                date integer,
                is_from_me integer,
                is_read integer,
                is_sent integer,
                is_delivered integer,
                is_empty integer,
                service text,
                reply_to_guid text
            );
            create table chat_handle_join (chat_id integer, handle_id integer);
            create table chat_message_join (chat_id integer, message_id integer, message_date integer, index_state integer);
            create table attachment (
                ROWID integer primary key,
                guid text,
                filename text,
                transfer_name text,
                uti text,
                mime_type text,
                total_bytes integer,
                transfer_state integer,
                is_outgoing integer,
                is_sticker integer
            );
            create table message_attachment_join (message_id integer, attachment_id integer);
            """
        )
        first = apple_ns(1_735_689_600)
        second = apple_ns(1_735_693_200)
        third = apple_ns(1_735_696_800)
        rich_body = "streamtyped | NSString | 周楳⁷楬氠扥⁷楴桩渠牵湤敳欿 | NSDictionary".encode()

        conn.execute("insert into chat values (1, 'chat-guid-1', 'Alex Example', '+15550100001', 'SMS')")
        conn.execute("insert into chat values (2, 'chat-guid-2', 'Handled Example', '+15550100002', 'SMS')")
        conn.execute("insert into chat values (3, 'chat-guid-3', 'Family Group', 'family-guid', 'RCS')")
        conn.execute("insert into chat values (4, 'chat-guid-4', 'RCS Example', '+15550100004', 'RCS')")
        conn.execute("insert into chat values (5, 'chat-guid-5', 'iMessage Example', '+15550100005', 'iMessage')")
        conn.execute("insert into handle values (1, '+15550100001', 'iMessage', '+15550100001', 'p1')")
        conn.execute("insert into handle values (2, '+15550100002', 'SMS', '+15550100002', 'p2')")
        conn.execute("insert into handle values (3, '+15550100003', 'RCS', '+15550100003', 'p3')")
        conn.execute("insert into handle values (4, '+15550100004', 'RCS', '+15550100004', 'p4')")
        conn.execute("insert into handle values (5, '+15550100005', 'iMessage', '+15550100005', 'p5')")
        conn.execute("insert into chat_handle_join values (1, 1)")
        conn.execute("insert into chat_handle_join values (2, 2)")
        conn.execute("insert into chat_handle_join values (3, 1)")
        conn.execute("insert into chat_handle_join values (3, 3)")
        conn.execute("insert into chat_handle_join values (4, 4)")
        conn.execute("insert into chat_handle_join values (5, 5)")

        conn.execute(
            "insert into message values (1, 'msg-1', 'hello', null, 1, ?, 0, 0, 1, 1, 0, 'iMessage', null)",
            (first,),
        )
        conn.execute(
            "insert into message values (2, 'msg-2', 'reply', null, null, ?, 1, 1, 1, 1, 0, 'iMessage', null)",
            (second,),
        )
        conn.execute(
            "insert into message values (3, 'msg-3', null, ?, 1, ?, 0, 0, 1, 1, 0, 'RCS', null)",
            (rich_body, third),
        )
        conn.execute(
            "insert into message values (4, 'msg-4', 'already handled', null, null, ?, 1, 1, 1, 1, 0, 'SMS', null)",
            (third + 1,),
        )
        conn.execute(
            "insert into message values (5, 'msg-5', 'group hello', null, 3, ?, 0, 0, 1, 1, 0, 'RCS', null)",
            (third + 2,),
        )
        conn.execute(
            "insert into message values (6, 'msg-6', 'rcs only', null, 4, ?, 0, 0, 1, 1, 0, 'RCS', null)",
            (third + 3,),
        )
        conn.execute(
            "insert into message values (7, 'msg-7', 'imessage only', null, 5, ?, 0, 0, 1, 1, 0, 'iMessage', null)",
            (third + 4,),
        )
        for chat_id, message_id, date in [
            (1, 1, first),
            (1, 2, second),
            (1, 3, third),
            (2, 4, third + 1),
            (3, 5, third + 2),
            (4, 6, third + 3),
            (5, 7, third + 4),
        ]:
            conn.execute("insert into chat_message_join values (?, ?, ?, 0)", (chat_id, message_id, date))

        conn.execute(
            "insert into attachment values (1, 'att-1', '/tmp/synthetic-attachment.txt', 'synthetic.txt', 'public.text', 'text/plain', 42, 0, 0, 0)"
        )
        conn.execute("insert into message_attachment_join values (3, 1)")
        conn.commit()
    finally:
        conn.close()


class AppleMessagesReadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.read_module = load_module("apple_messages_read", READ_SCRIPT)
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "chat.db"
        create_messages_db(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_connect_opens_database_read_only(self) -> None:
        with self.read_module.connect(self.db_path) as conn:
            self.assertEqual(self.read_module.single_value(conn, "select count(*) from message"), 7)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("insert into handle values (9, 'blocked', 'SMS', '', '')")

    def test_status_chats_show_search_unread_and_needs_reply(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.read_module.main(["status", "--db", str(self.db_path)]), 0)
        self.assertIn("Apple Messages access ok", output.getvalue())

        with self.read_module.connect(self.db_path) as conn:
            chats = self.read_module.list_chats(conn, 10)
            rcs_chats = self.read_module.list_chats(conn, 10, service="RCS")
            messages = self.read_module.fetch_messages(conn, 1, 5)
            search = self.read_module.search_messages(conn, "hello", 10)
            rich_search = self.read_module.search_messages(conn, "rundesk", 10)
            unread = self.read_module.list_unread_chats(conn, 10)
            needs_reply = self.read_module.list_needs_reply_chats(conn, 10)

        self.assertEqual(len(chats), 5)
        self.assertEqual({chat["chat_id"] for chat in rcs_chats}, {1, 3, 4})
        self.assertEqual(messages[-1]["text"], "This will be within rundesk?")
        self.assertEqual(messages[-1]["service"], "RCS")
        self.assertTrue(any(item["chat_id"] == 1 for item in search))
        self.assertTrue(any(item["chat_id"] == 1 and item["service"] == "RCS" for item in rich_search))
        self.assertTrue(any(chat["chat_id"] == 1 for chat in unread))
        self.assertTrue(any(chat["chat_id"] == 4 for chat in unread))
        self.assertTrue(any(chat["chat_id"] == 4 and chat["service"] == "RCS" for chat in needs_reply))

    def test_text_output_points_agents_to_attachment_files(self) -> None:
        show_output = io.StringIO()
        with redirect_stdout(show_output):
            self.assertEqual(self.read_module.main(["show", "--db", str(self.db_path), "--chat-id", "1"]), 0)

        self.assertIn(
            f"attachment_command=apple-messages read attachments --db {self.db_path} --message-id 3",
            show_output.getvalue(),
        )

        attachments_output = io.StringIO()
        with redirect_stdout(attachments_output):
            self.assertEqual(
                self.read_module.main(["attachments", "--db", str(self.db_path), "--message-id", "3"]),
                0,
            )

        self.assertIn("access=missing-local-file", attachments_output.getvalue())
        self.assertIn("trusted_messages_attachment=false", attachments_output.getvalue())
        self.assertIn("local_path=/tmp/synthetic-attachment.txt", attachments_output.getvalue())

    def test_existing_attachment_paths_outside_messages_are_untrusted(self) -> None:
        outside_attachment = Path(self.tmp.name) / "outside-messages.txt"
        outside_attachment.write_text("synthetic", encoding="utf-8")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("update attachment set filename = ? where ROWID = 1", (str(outside_attachment),))
            conn.commit()

        with self.read_module.connect(self.db_path) as conn:
            attachments = self.read_module.attachment_metadata(conn, 3)

        self.assertTrue(attachments[0]["file_exists"])
        self.assertFalse(attachments[0]["trusted_messages_attachment"])
        self.assertEqual(attachments[0]["access"], "untrusted-local-file")

    def test_show_supports_group_chats_and_json(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            rc = self.read_module.main(["show", "--db", str(self.db_path), "--chat-id", "3", "--json"])

        self.assertEqual(rc, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["chat"]["label"], "Family Group")
        self.assertEqual(len(payload["chat"]["participants"]), 2)
        self.assertEqual(payload["messages"][0]["service"], "RCS")

    def test_attachments_schema_and_export(self) -> None:
        with self.read_module.connect(self.db_path) as conn:
            attachments = self.read_module.attachment_metadata(conn, 3)
            schema = self.read_module.schema_rows(conn)
            payload = self.read_module.export_payload(conn, str(self.db_path), days=30_000, all_history=False)

        self.assertEqual(attachments[0]["transfer_name"], "synthetic.txt")
        self.assertEqual(attachments[0]["mime_type"], "text/plain")
        self.assertEqual(attachments[0]["access"], "missing-local-file")
        self.assertEqual(attachments[0]["local_path"], "/tmp/synthetic-attachment.txt")
        self.assertFalse(attachments[0]["trusted_messages_attachment"])
        self.assertFalse(attachments[0]["file_exists"])
        self.assertIn("message", {row["table"] for row in schema})
        self.assertEqual(payload["counts"]["messages"], 7)
        self.assertEqual(payload["counts"]["attachments"], 1)

    def test_read_commands_reject_unbounded_negative_limits(self) -> None:
        stderr = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(stderr):
            self.read_module.main(["chats", "--db", str(self.db_path), "--limit", "-1"])

        self.assertIn("limit must be between 1 and 500", stderr.getvalue())

    def test_attributed_body_artifacts_are_not_preferred(self) -> None:
        clean = "imagine knowing you have a synthetic helper while you sleep"
        artifact = "imagine knowing you have a synthetic helper while you sleeIpKiDNcSiintroya_"

        self.assertEqual(self.read_module.strip_attributed_wrappers("Kimagine synthetic helper"), "imagine synthetic helper")
        self.assertEqual(self.read_module.strip_attributed_wrappers("$So synthetic helper"), "So synthetic helper")
        self.assertFalse(self.read_module.plausible_decoded_body("streamtype@dANtSiturebStrdntgiONjScbte O+OGiOS"))
        self.assertFalse(self.read_module.plausible_decoded_body('So synthetic helper i "I _i_kIMMessagePartAttributeName NSValu*e'))
        self.assertGreater(self.read_module.readable_score(clean), self.read_module.readable_score(artifact))

    def test_read_commands_work_through_cli_json_handlers(self) -> None:
        commands = [
            (["chats", "--db", str(self.db_path), "--json"], lambda payload: payload[0]["chat_id"]),
            (["search", "--db", str(self.db_path), "hello", "--json"], lambda payload: payload[0]["message_id"]),
            (["unread", "--db", str(self.db_path), "--json"], lambda payload: payload[0]["chat_id"]),
            (["needs-reply", "--db", str(self.db_path), "--days", "30000", "--json"], lambda payload: payload[0]["chat_id"]),
            (["attachments", "--db", str(self.db_path), "--message-id", "3", "--json"], lambda payload: payload[0]["attachment_id"]),
            (["schema", "--db", str(self.db_path), "--json"], lambda payload: next(row for row in payload if row["table"] == "message")["table"]),
            (["export", "--db", str(self.db_path), "--days", "30000", "--json"], lambda payload: payload["counts"]["messages"]),
        ]
        for argv, assertion_value in commands:
            with self.subTest(argv=argv):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(self.read_module.main(argv), 0)
                payload = json.loads(output.getvalue())
                self.assertTrue(assertion_value(payload))

    def test_export_requires_days_or_all(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = self.read_module.main(["export", "--db", str(self.db_path)])

        self.assertEqual(rc, 1)
        self.assertIn("export requires --days N or explicit --all", stderr.getvalue())


class AppleMessagesSendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.send_module = load_module("apple_messages_send", SEND_SCRIPT)
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "chat.db"
        create_messages_db(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_send_connect_opens_database_read_only(self) -> None:
        with self.send_module.connect(self.db_path) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("insert into handle values (9, 'blocked', 'SMS', '', '')")

    def test_status_uses_applescript_without_sending(self) -> None:
        def fake_run(script, args=None):
            self.assertIsNone(args)
            return type("Result", (), {"stdout": "E:example@example.test, SMS"})()

        output = io.StringIO()
        with patch.object(self.send_module, "run_osascript", side_effect=fake_run), redirect_stdout(output):
            self.assertEqual(self.send_module.main(["status"]), 0)

        self.assertIn("Apple Messages send access ok", output.getvalue())

    def test_send_to_is_dry_run_by_default(self) -> None:
        output = io.StringIO()
        with patch.object(self.send_module, "run_osascript") as run_osascript, redirect_stdout(output):
            rc = self.send_module.main(
                ["send", "--db", str(self.db_path), "--to", "+15550100001", "--body", "hello\nagain", "--service", "iMessage"]
            )

        self.assertEqual(rc, 0)
        run_osascript.assert_not_called()
        self.assertIn("dry-run", output.getvalue())
        self.assertIn("apple_script_service=iMessage", output.getvalue())
        self.assertIn('body_json="hello\\nagain"', output.getvalue())
        self.assertIn("body_length=11", output.getvalue())
        self.assertIn("body_sha256=", output.getvalue())

    def test_confirmed_send_invokes_applescript(self) -> None:
        calls: list[tuple[str, list[str] | None]] = []

        def fake_run(script, args=None):
            calls.append((script, args))
            return type("Result", (), {"stdout": ""})()

        with patch.object(self.send_module, "run_osascript", side_effect=fake_run), redirect_stdout(io.StringIO()):
            rc = self.send_module.main(
                [
                    "send",
                    "--db",
                    str(self.db_path),
                    "--to",
                    "+15550100001",
                    "--body",
                    "confirmed",
                    "--service",
                    "SMS",
                    "--confirm",
                ]
            )

        self.assertEqual(rc, 0)
        self.assertEqual(calls[0][1], ["SMS", "+15550100001", "confirmed"])

    def test_send_by_chat_id_resolves_one_to_one_and_uses_latest_transport(self) -> None:
        output = io.StringIO()
        with patch.object(self.send_module, "run_osascript") as run_osascript, redirect_stdout(output):
            rc = self.send_module.main(["send", "--db", str(self.db_path), "--chat-id", "1", "--body", "hello"])

        self.assertEqual(rc, 0)
        run_osascript.assert_not_called()
        self.assertIn("to=+15550100001", output.getvalue())
        self.assertIn("requested_service=auto", output.getvalue())
        self.assertIn("recent_service=RCS", output.getvalue())
        self.assertIn("apple_script_service=SMS", output.getvalue())

    def test_send_by_imessage_chat_id_auto_selects_imessage_service(self) -> None:
        output = io.StringIO()
        with patch.object(self.send_module, "run_osascript") as run_osascript, redirect_stdout(output):
            rc = self.send_module.main(["send", "--db", str(self.db_path), "--chat-id", "5", "--body", "hello"])

        self.assertEqual(rc, 0)
        run_osascript.assert_not_called()
        self.assertIn("to=+15550100005", output.getvalue())
        self.assertIn("recent_service=iMessage", output.getvalue())
        self.assertIn("apple_script_service=iMessage", output.getvalue())

    def test_send_by_sms_chat_id_auto_selects_sms_service(self) -> None:
        output = io.StringIO()
        with patch.object(self.send_module, "run_osascript") as run_osascript, redirect_stdout(output):
            rc = self.send_module.main(["send", "--db", str(self.db_path), "--chat-id", "2", "--body", "hello"])

        self.assertEqual(rc, 0)
        run_osascript.assert_not_called()
        self.assertIn("recent_service=SMS", output.getvalue())
        self.assertIn("apple_script_service=SMS", output.getvalue())

    def test_group_chat_send_fails(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = self.send_module.main(["send", "--db", str(self.db_path), "--chat-id", "3", "--body", "hello"])

        self.assertEqual(rc, 1)
        self.assertIn("only supports one-to-one chats", stderr.getvalue())

    def test_recent_rcs_maps_to_sms_applescript_service(self) -> None:
        output = io.StringIO()
        with patch.object(self.send_module, "run_osascript") as run_osascript, redirect_stdout(output):
            rc = self.send_module.main(["send", "--db", str(self.db_path), "--chat-id", "4", "--body", "hello"])

        self.assertEqual(rc, 0)
        run_osascript.assert_not_called()
        self.assertIn("recent_service=RCS", output.getvalue())
        self.assertIn("apple_script_service=SMS", output.getvalue())
        self.assertIn("RCS cannot be forced directly", output.getvalue())

    def test_explicit_rcs_to_maps_to_sms_applescript_service(self) -> None:
        output = io.StringIO()
        with patch.object(self.send_module, "run_osascript") as run_osascript, redirect_stdout(output):
            rc = self.send_module.main(["send", "--to", "+15550100004", "--body", "hello", "--service", "RCS"])

        self.assertEqual(rc, 0)
        run_osascript.assert_not_called()
        self.assertIn("requested_service=RCS", output.getvalue())
        self.assertIn("apple_script_service=SMS", output.getvalue())
        self.assertIn("RCS cannot be forced directly", output.getvalue())


if __name__ == "__main__":
    unittest.main()
