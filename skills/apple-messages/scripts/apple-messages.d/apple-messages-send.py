#!/usr/bin/env python3
"""
Send one-to-one messages through Apple Messages.app with confirmation guards.

Usage:
  apple-messages send status [--json]
  apple-messages send send --chat-id 123 --body "Message" [--confirm] [--json]
  apple-messages send send --to "+15555551212" --body "Message" [--service auto|iMessage|SMS|RCS] [--confirm] [--json]

Inputs:
  Reads ~/Library/Messages/chat.db in read-only mode when resolving --chat-id or
  auto service selection. Uses Messages.app AppleScript for confirmed sends.

Outputs:
  Dry-run summaries by default. Use --json for structured output. This script
  never writes Messages SQLite directly; confirmed sends go through Messages.app.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
DEFAULT_DB = Path.home() / "Library" / "Messages" / "chat.db"
SERVICES = ("auto", "iMessage", "SMS", "RCS")


class AppleMessagesSendError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatRef:
    chat_id: int
    chat_guid: str
    label: str
    chat_identifier: str
    chat_service: str
    participants: list[str]


@dataclass(frozen=True)
class SendTarget:
    recipient: str
    requested_service: str
    recent_service: str
    apple_script_service: str
    source: str
    note: str


def text(value: Any, fallback: str = "-") -> str:
    if value is None:
        return fallback
    value = str(value).replace("\n", " ").strip()
    return value if value else fallback


def truncate(value: Any, limit: int = 180) -> str:
    value = text(value)
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path).expanduser()
    if not path.exists():
        raise AppleMessagesSendError(f"Messages database not found: {path}")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise AppleMessagesSendError(f"Unable to open Messages database read-only: {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    try:
        return conn.execute(query, params).fetchall()
    except sqlite3.Error as exc:
        raise AppleMessagesSendError(str(exc)) from exc


def chat_label(row: sqlite3.Row) -> str:
    for key in ("display_name", "chat_identifier", "participant_csv"):
        value = text(row[key], "")
        if value:
            return value
    return f"chat:{row['chat_id']}"


def resolve_chat(conn: sqlite3.Connection, chat_id: int) -> ChatRef:
    result = rows(
        conn,
        """
        select
            c.ROWID as chat_id,
            c.guid as chat_guid,
            c.display_name,
            c.chat_identifier,
            c.service_name as chat_service,
            group_concat(distinct h.id) as participant_csv
        from chat c
        left join chat_handle_join chj on chj.chat_id = c.ROWID
        left join handle h on h.ROWID = chj.handle_id
        where c.ROWID = ?
        group by c.ROWID
        """,
        (chat_id,),
    )
    if not result:
        raise AppleMessagesSendError("No matching chat found.")
    row = result[0]
    participants = [item for item in text(row["participant_csv"], "").split(",") if item]
    return ChatRef(
        chat_id=int(row["chat_id"]),
        chat_guid=text(row["chat_guid"], ""),
        label=chat_label(row),
        chat_identifier=text(row["chat_identifier"], ""),
        chat_service=text(row["chat_service"], ""),
        participants=participants,
    )


def recent_services_for_chat(conn: sqlite3.Connection, chat_id: int, limit: int = 30) -> list[str]:
    return [
        row["service"]
        for row in rows(
            conn,
            """
            select m.service
            from chat_message_join cmj
            join message m on m.ROWID = cmj.message_id
            where cmj.chat_id = ? and m.service is not null and m.service != ''
            order by m.date desc
            limit ?
            """,
            (chat_id, limit),
        )
        if row["service"]
    ]


def recent_services_for_handle(conn: sqlite3.Connection, handle: str, limit: int = 30) -> list[str]:
    return [
        row["service"]
        for row in rows(
            conn,
            """
            select m.service
            from message m
            join handle h on h.ROWID = m.handle_id
            where h.id = ? and m.service is not null and m.service != ''
            order by m.date desc
            limit ?
            """,
            (handle, limit),
        )
        if row["service"]
    ]


def preferred_observed_service(services: list[str], fallback: str = "iMessage") -> str:
    for service in services:
        if service in {"iMessage", "RCS", "SMS"}:
            return service
    return fallback


def apple_script_service_for(requested_service: str, recent_service: str) -> tuple[str, str]:
    observed = recent_service if requested_service == "auto" else requested_service
    if observed == "RCS":
        return "SMS", "RCS cannot be forced directly by AppleScript; Messages.app chooses SMS/RCS availability through the SMS service."
    if observed == "SMS":
        return "SMS", ""
    return "iMessage", ""


def target_from_chat(conn: sqlite3.Connection, chat_id: int, requested_service: str) -> SendTarget:
    chat = resolve_chat(conn, chat_id)
    if len(chat.participants) != 1:
        raise AppleMessagesSendError("Sending by --chat-id only supports one-to-one chats.")
    services = recent_services_for_chat(conn, chat_id)
    recent = preferred_observed_service(services, chat.chat_service or "iMessage")
    apple_service, note = apple_script_service_for(requested_service, recent)
    return SendTarget(
        recipient=chat.participants[0],
        requested_service=requested_service,
        recent_service=recent,
        apple_script_service=apple_service,
        source=f"chat_id={chat.chat_id}",
        note=note,
    )


def target_from_recipient(conn: sqlite3.Connection | None, recipient: str, requested_service: str) -> SendTarget:
    recent = ""
    if requested_service == "auto" and conn is not None:
        recent = preferred_observed_service(recent_services_for_handle(conn, recipient), "iMessage")
    elif requested_service != "auto":
        recent = requested_service
    else:
        recent = "iMessage"
    apple_service, note = apple_script_service_for(requested_service, recent)
    return SendTarget(
        recipient=recipient,
        requested_service=requested_service,
        recent_service=recent,
        apple_script_service=apple_service,
        source="explicit --to",
        note=note,
    )


def build_send_applescript() -> str:
    return textwrap.dedent(
        """
        on run argv
          set serviceName to item 1 of argv
          set recipientAddress to item 2 of argv
          set messageBody to item 3 of argv

          tell application "Messages"
            set targetService to 1st service whose service type = iMessage
            if serviceName is "SMS" then
              set targetService to 1st service whose service type = SMS
            end if
            set targetBuddy to buddy recipientAddress of targetService
            send messageBody to targetBuddy
          end tell
        end run
        """
    ).strip()


def build_status_applescript() -> str:
    return 'tell application "Messages" to count services'


def run_osascript(script: str, args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    command = ["osascript", "-e", script]
    if args:
        command.extend(args)
    try:
        return subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        error = (exc.stderr or exc.stdout or str(exc)).strip()
        raise AppleMessagesSendError(f"Messages AppleScript failed: {error}") from exc


def send_payload(target: SendTarget, body: str, confirm: bool) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "operation": "send",
        "dry_run": not confirm,
        "status": "ok",
        "recipient": target.recipient,
        "source": target.source,
        "requested_service": target.requested_service,
        "recent_service": target.recent_service,
        "apple_script_service": target.apple_script_service,
        "note": target.note,
        "body_preview": truncate(body, 120),
        "body_length": len(body),
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }
    if not confirm:
        payload["body"] = body
    return payload


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


def print_send_payload(payload: dict[str, Any]) -> None:
    prefix = "dry-run: would send Apple Messages" if payload["dry_run"] else "sent: Apple Messages"
    parts = [
        prefix,
        f"apple_script_service={payload['apple_script_service']}",
        f"requested_service={payload['requested_service']}",
        f"recent_service={payload['recent_service']}",
        f"to={payload['recipient']}",
        f"source={payload['source']}",
    ]
    if payload["dry_run"]:
        parts.append(f"body_json={json.dumps(payload.get('body', ''), ensure_ascii=False)}")
    else:
        parts.append(f"body={payload['body_preview']}")
    parts.extend([f"body_length={payload['body_length']}", f"body_sha256={payload['body_sha256']}"])
    if payload.get("note"):
        parts.append(f"note={payload['note']}")
    print(" | ".join(parts))
    if payload["dry_run"]:
        print("Pass --confirm only after the owner explicitly asks to send this exact message.")


def command_status(args: argparse.Namespace) -> int:
    result = run_osascript(build_status_applescript())
    raw_count = (result.stdout or "").strip()
    try:
        service_count = int(raw_count)
    except ValueError:
        service_count = 0
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "service_count": service_count,
    }
    if args.json:
        print_json(payload)
    else:
        print(f"Apple Messages send access ok | service_count={service_count}")
    return 0


def command_send(args: argparse.Namespace) -> int:
    if bool(args.to) == bool(args.chat_id):
        raise AppleMessagesSendError("Select exactly one send target with --chat-id or --to.")
    conn: sqlite3.Connection | None = None
    try:
        if args.chat_id or args.service == "auto":
            conn = connect(args.db)
        if args.chat_id:
            assert conn is not None
            target = target_from_chat(conn, args.chat_id, args.service)
        else:
            target = target_from_recipient(conn, args.to, args.service)
    finally:
        if conn is not None:
            conn.close()

    if args.confirm:
        run_osascript(build_send_applescript(), [target.apple_script_service, target.recipient, args.body])
    payload = send_payload(target, args.body, args.confirm)
    print_json(payload) if args.json else print_send_payload(payload)
    return 0


def add_db_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Messages chat.db path. Defaults to ~/Library/Messages/chat.db.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send one-to-one Apple Messages.app messages with confirmation guards.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Examples:
              apple-messages send status
              apple-messages send send --chat-id 123 --body "On my way"
              apple-messages send send --to "+15555551212" --body "On my way" --service auto
            """
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Verify Messages.app AppleScript access without sending.")
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=command_status)

    send = subparsers.add_parser("send", help="Send a one-to-one message. Dry-run unless --confirm is passed.")
    add_db_option(send)
    send.add_argument("--chat-id", type=int, help="One-to-one Messages chat ROWID.")
    send.add_argument("--to", help="Recipient phone number or Apple ID email.")
    send.add_argument("--body", required=True, help="Exact message body to send.")
    send.add_argument("--service", choices=SERVICES, default="auto", help="Requested service. RCS maps through AppleScript SMS.")
    send.add_argument("--confirm", action="store_true", help="Actually send the message.")
    send.add_argument("--json", action="store_true")
    send.set_defaults(handler=command_send)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except AppleMessagesSendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
