#!/usr/bin/env python3
"""
Read local Apple Messages.app data for workspace agents.

Usage:
  apple-messages read status [--json]
  apple-messages read chats [--limit 50] [--query QUERY] [--service iMessage|SMS|RCS] [--json]
  apple-messages read show --chat-id ID [--limit 20] [--json]
  apple-messages read search "term" [--days 30] [--json]
  apple-messages read unread [--limit 25] [--json]
  apple-messages read needs-reply [--days 14] [--limit 25] [--json]
  apple-messages read attachments --message-id ID [--json]
  apple-messages read schema
  apple-messages read export --days 7 --json

Inputs:
  Reads the local Messages SQLite database at ~/Library/Messages/chat.db by default.
  Override with --db for tests or forensic copies.

Outputs:
  Prints compact text by default. Use --json for structured payloads. This
  script always opens the Messages database read-only and never mutates local
  Messages or iCloud state. Message rows with attachments include the exact
  attachment lookup command; attachment rows include local file paths when
  Messages has stored the file locally.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sqlite3
import string
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
DEFAULT_DB = Path.home() / "Library" / "Messages" / "chat.db"
DEFAULT_ATTACHMENT_ROOT = Path.home() / "Library" / "Messages" / "Attachments"
APPLE_EPOCH_OFFSET = 978_307_200
DEFAULT_MESSAGE_LIMIT = 20
SERVICES = ("iMessage", "SMS", "RCS")
PRINTABLE_BODY_CHARS = set(string.printable) | {"\u2019", "\u201c", "\u201d", "\u2014", "\u2026", "\u00a0"}
ATTRIBUTED_BODY_METADATA = (
    "__kIMMessagePartAttributeName",
    "NSMutableAttributedString",
    "NSAttributedString",
    "NSMutableString",
    "NSDictionary",
    "NSObject",
    "NSString",
    "NSNumber",
    "NSValue",
    "streamtyped",
)
ATTRIBUTED_BODY_ARTIFACT_MARKERS = (
    "asttirubet",
    "immessagepartattribute",
    "kimmessage",
    "nsvalu",
    "tsermaytep",
    "streamtype",
    "snidtcoianyr",
    "nsmueb",
    "snunbmre",
    "nsrtni",
    "nsvalue",
    "k_miemssg",
)


class AppleMessagesReadError(RuntimeError):
    pass


def generated_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text(value: Any, fallback: str = "-") -> str:
    if value is None:
        return fallback
    value = str(value).replace("\ufffc", "").replace("\n", " ").strip()
    return value if value else fallback


def empty_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def truncate(value: Any, limit: int = 220) -> str:
    value = text(value)
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def join_values(values: list[Any], limit: int = 8) -> str:
    values = [text(value, "") for value in values if text(value, "")]
    if not values:
        return "-"
    shown = values[:limit]
    suffix = f";+{len(values) - limit}" if len(values) > limit else ""
    return ";".join(shown) + suffix


def apple_time_to_datetime(value: Any, local: bool = True) -> str:
    if value in (None, "", 0):
        return "-"
    try:
        seconds = int(value) / 1_000_000_000 + APPLE_EPOCH_OFFSET
    except (TypeError, ValueError):
        return "-"
    dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
    if local:
        dt = dt.astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def cutoff_for_days(days: int | None) -> int | None:
    if days is None:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return int((cutoff.timestamp() - APPLE_EPOCH_OFFSET) * 1_000_000_000)


def bounded_int(value: str, *, minimum: int, maximum: int, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise argparse.ArgumentTypeError(f"{label} must be between {minimum} and {maximum}")
    return parsed


def positive_limit(value: str) -> int:
    return bounded_int(value, minimum=1, maximum=500, label="limit")


def positive_days(value: str) -> int:
    return bounded_int(value, minimum=1, maximum=36500, label="days")


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path).expanduser()
    if not path.exists():
        raise AppleMessagesReadError(f"Messages database not found: {path}")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise AppleMessagesReadError(f"Unable to open Messages database read-only: {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    try:
        return conn.execute(query, params).fetchall()
    except sqlite3.Error as exc:
        raise AppleMessagesReadError(str(exc)) from exc


def single_value(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> Any:
    result = rows(conn, query, params)
    return result[0][0] if result else None


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in rows(conn, f"pragma table_info({table})")}


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in rows(
            conn,
            "select name from sqlite_master where type = 'table'",
        )
    }


def column_expr(columns: set[str], alias: str, column: str, output: str | None = None, default: str = "null") -> str:
    output = output or column
    if column in columns:
        return f"{alias}.{column} as {output}"
    return f"{default} as {output}"


def row_value(row: sqlite3.Row, key: str, fallback: Any = None) -> Any:
    return row[key] if key in row.keys() else fallback


def normalize_message_text(value: str) -> str:
    for marker in ATTRIBUTED_BODY_METADATA:
        value = value.replace(marker, " ")
    value = value.replace("\x00", " ")
    value = "".join(char if char in PRINTABLE_BODY_CHARS else " " for char in value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" |")


def strip_attributed_wrappers(value: str) -> str:
    plus_parts = re.findall(r"(?:^|\s)\+([^|]+?)(?=\s+(?:iI|Ii|[A-Z]?iI)\b|\s+\*|$)", value)
    if plus_parts:
        value = max(plus_parts, key=len).strip()
    value = re.sub(r"^(?:@|[A-Za-z]?@)\s*", "", value).strip()
    value = re.sub(r"\s*(?:[A-Z]?iI|Ii)\S*(?:\s+\S+)*$", "", value).strip()
    value = value.strip(" |+*/$")
    prefixes = (
        "this",
        "thats",
        "that's",
        "it",
        "i ",
        "imagine",
        "the",
        "so",
        "you",
        "your",
        "we",
        "rundesk",
        "cant",
        "can't",
    )
    if len(value) >= 3 and value[0].isupper():
        without_marker = value[1:].lstrip()
        if without_marker.lower().startswith(prefixes):
            value = without_marker
    return value.strip(" |+*/$")


def packed_utf16_ascii(value: str) -> str:
    output = bytearray()
    for char in value:
        codepoint = ord(char)
        if codepoint <= 0xFF:
            output.append(codepoint)
            continue
        if codepoint <= 0xFFFF:
            high = codepoint >> 8
            low = codepoint & 0xFF
            if 32 <= high <= 126 or high in (9, 10, 13):
                output.append(high)
            if 32 <= low <= 126 or low in (9, 10, 13):
                output.append(low)
    return output.decode("utf-8", errors="ignore")


def unswap_adjacent_characters(value: str) -> str:
    chars = list(value)
    for index in range(0, len(chars) - 1, 2):
        chars[index], chars[index + 1] = chars[index + 1], chars[index]
    return "".join(chars)


def readable_score(value: str) -> tuple[int, int, int]:
    words = re.findall(r"[A-Za-z][A-Za-z']+", value)
    common = sum(
        1
        for word in words
        if word.lower()
        in {
            "the",
            "this",
            "that",
            "will",
            "with",
            "within",
            "rundesk",
            "work",
            "agent",
            "agents",
            "automation",
            "command",
            "center",
            "yes",
            "you",
            "your",
            "tim",
        }
    )
    return (common, len(words), -len(value))


def plausible_decoded_body(value: str) -> bool:
    lower = value.lower()
    if any(marker in lower for marker in ATTRIBUTED_BODY_ARTIFACT_MARKERS):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z']+", value)
    if len(value) >= 40 and len(words) < 3:
        return False
    return True


def decoded_attributed_body(blob: bytes | None) -> str:
    if not blob:
        return ""
    raw_candidates = [blob.decode(encoding, errors="ignore") for encoding in ("utf-8", "utf-16-be", "utf-16-le", "latin-1")]
    candidates: list[str] = []
    for raw in raw_candidates:
        packed = packed_utf16_ascii(raw)
        for candidate in (raw, packed, unswap_adjacent_characters(raw), unswap_adjacent_characters(packed)):
            cleaned = strip_attributed_wrappers(normalize_message_text(candidate))
            if cleaned and plausible_decoded_body(cleaned):
                candidates.append(cleaned)
    candidates = [
        candidate
        for candidate in candidates
        if len(candidate) >= 2 and not all(piece in ATTRIBUTED_BODY_METADATA for piece in candidate.split())
    ]
    return max(candidates, key=readable_score) if candidates else ""


def message_body(row: sqlite3.Row) -> str:
    body = empty_text(row_value(row, "body"))
    if body:
        return truncate(body, 260)
    body = decoded_attributed_body(row_value(row, "attributed_body"))
    if body:
        return truncate(body, 260)
    attachment_count = int(row_value(row, "attachment_count", 0) or 0)
    if attachment_count:
        return f"[attachment-only message: {attachment_count}]"
    if row_value(row, "is_empty", 0):
        return "[empty message]"
    return "[rich message or unsupported body]"


def sender_label(row: sqlite3.Row) -> str:
    if row_value(row, "is_from_me", 0):
        return "me"
    return text(row_value(row, "sender"), "unknown")


def chat_label(row: sqlite3.Row) -> str:
    for key in ("display_name", "chat_identifier", "participant_csv"):
        value = text(row_value(row, key), "")
        if value:
            return truncate(value, 90)
    return f"chat:{row_value(row, 'rowid', row_value(row, 'chat_id', '-'))}"


def participant_rows(conn: sqlite3.Connection, chat_id: int) -> list[dict[str, Any]]:
    handle_cols = table_columns(conn, "handle")
    result = rows(
        conn,
        f"""
        select
            h.ROWID as handle_id,
            {column_expr(handle_cols, "h", "id", "handle")},
            {column_expr(handle_cols, "h", "service", "service")},
            {column_expr(handle_cols, "h", "uncanonicalized_id", "uncanonicalized_id")},
            {column_expr(handle_cols, "h", "person_centric_id", "person_centric_id")}
        from chat_handle_join chj
        join handle h on h.ROWID = chj.handle_id
        where chj.chat_id = ?
        order by h.ROWID
        """,
        (chat_id,),
    )
    return [
        {
            "handle_id": row["handle_id"],
            "handle": text(row["handle"], ""),
            "service": text(row["service"], ""),
            "uncanonicalized_id": text(row["uncanonicalized_id"], ""),
            "person_centric_id": text(row["person_centric_id"], ""),
        }
        for row in result
    ]


def participant_summary(participants: list[dict[str, Any]], limit: int = 5) -> str:
    values = []
    for item in participants[:limit]:
        label = item["handle"] or "-"
        if item.get("service"):
            label = f"{label} ({item['service']})"
        values.append(label)
    if len(participants) > limit:
        values.append(f"+{len(participants) - limit}")
    return ";".join(values) if values else "-"


def service_filter_clause(service: str | None, chat_alias: str = "c", message_alias: str = "m") -> tuple[str, list[Any]]:
    if not service:
        return "", []
    return f"coalesce({message_alias}.service, {chat_alias}.service_name) = ?", [service]


def list_chats(conn: sqlite3.Connection, limit: int, query: str | None = None, service: str | None = None) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if query:
        like = f"%{query}%"
        clauses.append(
            """(
                c.display_name like ?
                or c.chat_identifier like ?
                or exists (
                    select 1
                    from chat_handle_join chj2
                    join handle h2 on h2.ROWID = chj2.handle_id
                    where chj2.chat_id = c.ROWID and h2.id like ?
                )
            )"""
        )
        params.extend([like, like, like])
    if service:
        clauses.append(
            """(
                c.service_name = ?
                or exists (
                    select 1
                    from chat_message_join cmj2
                    join message m2 on m2.ROWID = cmj2.message_id
                    where cmj2.chat_id = c.ROWID and m2.service = ?
                )
            )"""
        )
        params.extend([service, service])
    where = "where " + " and ".join(clauses) if clauses else ""
    params.append(limit)
    result = rows(
        conn,
        f"""
        select
            c.ROWID as rowid,
            c.guid,
            c.display_name,
            c.chat_identifier,
            c.service_name,
            group_concat(distinct h.id) as participant_csv,
            group_concat(distinct h.service) as participant_service_csv,
            group_concat(distinct m.service) as message_service_csv,
            count(distinct m.ROWID) as message_count,
            max(m.date) as latest_date,
            sum(case when m.is_from_me = 0 and m.is_read = 0 then 1 else 0 end) as unread_estimate
        from chat c
        left join chat_handle_join chj on chj.chat_id = c.ROWID
        left join handle h on h.ROWID = chj.handle_id
        left join chat_message_join cmj on cmj.chat_id = c.ROWID
        left join message m on m.ROWID = cmj.message_id
        {where}
        group by c.ROWID
        order by latest_date desc
        limit ?
        """,
        tuple(params),
    )
    output = []
    for row in result:
        participants = participant_rows(conn, int(row["rowid"]))
        output.append(
            {
                "chat_id": row["rowid"],
                "chat_guid": text(row["guid"], ""),
                "label": chat_label(row),
                "chat_identifier": text(row["chat_identifier"], ""),
                "chat_service": text(row["service_name"], ""),
                "message_services": sorted({item for item in text(row["message_service_csv"], "").split(",") if item}),
                "participant_services": sorted({item for item in text(row["participant_service_csv"], "").split(",") if item}),
                "participants": participants,
                "message_count": int(row["message_count"] or 0),
                "unread_estimate": int(row["unread_estimate"] or 0),
                "latest": apple_time_to_datetime(row["latest_date"]),
            }
        )
    return output


def chat_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    participants = participant_rows(conn, int(row["rowid"]))
    return {
        "chat_id": row["rowid"],
        "chat_guid": text(row["guid"], ""),
        "label": chat_label(row),
        "chat_identifier": text(row["chat_identifier"], ""),
        "chat_service": text(row["service_name"], ""),
        "participants": participants,
    }


def resolve_chat_by_id(conn: sqlite3.Connection, chat_id: int) -> dict[str, Any]:
    result = rows(
        conn,
        """
        select c.ROWID as rowid, c.guid, c.display_name, c.chat_identifier, c.service_name
        from chat c
        where c.ROWID = ?
        """,
        (chat_id,),
    )
    if not result:
        raise AppleMessagesReadError("No matching chat found.")
    return chat_from_row(conn, result[0])


def resolve_chat(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    selectors = [
        bool(getattr(args, "chat_id", None)),
        bool(getattr(args, "chat_guid", None)),
        bool(getattr(args, "chat_identifier", None)),
        bool(getattr(args, "handle", None)),
    ]
    if sum(selectors) != 1:
        raise AppleMessagesReadError("Select exactly one chat selector.")
    if getattr(args, "chat_id", None):
        where = "c.ROWID = ?"
        params: tuple[Any, ...] = (args.chat_id,)
    elif getattr(args, "chat_guid", None):
        where = "c.guid = ?"
        params = (args.chat_guid,)
    elif getattr(args, "chat_identifier", None):
        where = "c.chat_identifier = ?"
        params = (args.chat_identifier,)
    else:
        where = """exists (
            select 1
            from chat_handle_join chj2
            join handle h2 on h2.ROWID = chj2.handle_id
            where chj2.chat_id = c.ROWID and h2.id = ?
        )"""
        params = (args.handle,)
    result = rows(
        conn,
        f"""
        select c.ROWID as rowid, c.guid, c.display_name, c.chat_identifier, c.service_name
        from chat c
        where {where}
        order by c.ROWID desc
        limit 2
        """,
        params,
    )
    if not result:
        raise AppleMessagesReadError("No matching chat found.")
    if len(result) > 1 and getattr(args, "handle", None):
        raise AppleMessagesReadError("Handle matched multiple chats. Use --chat-id from the chats command.")
    return chat_from_row(conn, result[0])


def fetch_messages(conn: sqlite3.Connection, chat_id: int, limit: int, days: int | None = None) -> list[dict[str, Any]]:
    message_cols = table_columns(conn, "message")
    params: list[Any] = [chat_id]
    clauses = ["cmj.chat_id = ?"]
    cutoff = cutoff_for_days(days)
    if cutoff:
        clauses.append("m.date >= ?")
        params.append(cutoff)
    params.append(limit)
    result = rows(
        conn,
        f"""
        select
            m.ROWID as message_id,
            {column_expr(message_cols, "m", "guid", "message_guid")},
            {column_expr(message_cols, "m", "date", "date", "0")},
            {column_expr(message_cols, "m", "is_from_me", "is_from_me", "0")},
            {column_expr(message_cols, "m", "is_read", "is_read", "0")},
            {column_expr(message_cols, "m", "is_sent", "is_sent", "0")},
            {column_expr(message_cols, "m", "is_delivered", "is_delivered", "0")},
            {column_expr(message_cols, "m", "is_empty", "is_empty", "0")},
            {column_expr(message_cols, "m", "service", "service")},
            {column_expr(message_cols, "m", "text", "body")},
            {column_expr(message_cols, "m", "attributedBody", "attributed_body")},
            {column_expr(message_cols, "m", "reply_to_guid", "reply_to_guid")},
            h.id as sender,
            count(a.ROWID) as attachment_count
        from chat_message_join cmj
        join message m on m.ROWID = cmj.message_id
        left join handle h on h.ROWID = m.handle_id
        left join message_attachment_join maj on maj.message_id = m.ROWID
        left join attachment a on a.ROWID = maj.attachment_id
        where {" and ".join(clauses)}
        group by m.ROWID
        order by m.date desc
        limit ?
        """,
        tuple(params),
    )
    output = []
    for row in reversed(result):
        output.append(
            {
                "message_id": row["message_id"],
                "message_guid": text(row["message_guid"], ""),
                "at": apple_time_to_datetime(row["date"]),
                "from": sender_label(row),
                "from_handle": text(row["sender"], ""),
                "from_me": bool(row["is_from_me"]),
                "service": text(row["service"], ""),
                "sent": bool(row["is_sent"]),
                "delivered": bool(row["is_delivered"]),
                "read": bool(row["is_read"]),
                "attachments": int(row["attachment_count"] or 0),
                "reply_to_guid": text(row["reply_to_guid"], ""),
                "text": message_body(row),
            }
        )
    return output


def search_messages(conn: sqlite3.Connection, term: str, limit: int, days: int | None = None, service: str | None = None) -> list[dict[str, Any]]:
    message_cols = table_columns(conn, "message")
    params: list[Any] = []
    clauses: list[str] = []
    cutoff = cutoff_for_days(days)
    if cutoff:
        clauses.append("m.date >= ?")
        params.append(cutoff)
    service_clause, service_params = service_filter_clause(service)
    if service_clause:
        clauses.append(service_clause)
        params.extend(service_params)
    where = "where " + " and ".join(clauses) if clauses else ""
    candidate_limit = min(max(limit * 20, 100), 5000)
    params.append(candidate_limit)
    result = rows(
        conn,
        f"""
        select
            c.ROWID as chat_id,
            c.guid as chat_guid,
            c.display_name,
            c.chat_identifier,
            c.service_name,
            m.ROWID as message_id,
            {column_expr(message_cols, "m", "guid", "message_guid")},
            {column_expr(message_cols, "m", "date", "date", "0")},
            {column_expr(message_cols, "m", "is_from_me", "is_from_me", "0")},
            {column_expr(message_cols, "m", "is_empty", "is_empty", "0")},
            {column_expr(message_cols, "m", "service", "service")},
            {column_expr(message_cols, "m", "text", "body")},
            {column_expr(message_cols, "m", "attributedBody", "attributed_body")},
            h.id as sender,
            count(a.ROWID) as attachment_count
        from message m
        join chat_message_join cmj on cmj.message_id = m.ROWID
        join chat c on c.ROWID = cmj.chat_id
        left join handle h on h.ROWID = m.handle_id
        left join message_attachment_join maj on maj.message_id = m.ROWID
        left join attachment a on a.ROWID = maj.attachment_id
        {where}
        group by m.ROWID, c.ROWID
        order by m.date desc
        limit ?
        """,
        tuple(params),
    )
    matches = []
    term_lower = term.lower()
    for row in result:
        body = message_body(row)
        if term_lower not in body.lower():
            continue
        matches.append(
            {
                "chat_id": row["chat_id"],
                "chat_guid": text(row["chat_guid"], ""),
                "chat": chat_label(row),
                "chat_service": text(row["service_name"], ""),
                "message_id": row["message_id"],
                "message_guid": text(row["message_guid"], ""),
                "at": apple_time_to_datetime(row["date"]),
                "from": sender_label(row),
                "from_me": bool(row["is_from_me"]),
                "service": text(row["service"], ""),
                "attachments": int(row["attachment_count"] or 0),
                "text": body,
            }
        )
        if len(matches) >= limit:
            break
    return matches


def list_unread_chats(conn: sqlite3.Connection, limit: int, query: str | None = None, service: str | None = None) -> list[dict[str, Any]]:
    clauses = ["m.is_from_me = 0", "m.is_read = 0"]
    params: list[Any] = []
    if query:
        like = f"%{query}%"
        clauses.append(
            """(
                c.display_name like ?
                or c.chat_identifier like ?
                or exists (
                    select 1
                    from chat_handle_join chj2
                    join handle h2 on h2.ROWID = chj2.handle_id
                    where chj2.chat_id = c.ROWID and h2.id like ?
                )
            )"""
        )
        params.extend([like, like, like])
    service_clause, service_params = service_filter_clause(service)
    if service_clause:
        clauses.append(service_clause)
        params.extend(service_params)
    params.append(limit)
    result = rows(
        conn,
        f"""
        select
            c.ROWID as rowid,
            c.guid,
            c.display_name,
            c.chat_identifier,
            c.service_name,
            group_concat(distinct m.service) as message_service_csv,
            count(distinct m.ROWID) as unread_count,
            max(m.date) as latest_unread_date
        from chat_message_join cmj
        join message m on m.ROWID = cmj.message_id
        join chat c on c.ROWID = cmj.chat_id
        where {" and ".join(clauses)}
        group by c.ROWID
        order by latest_unread_date desc
        limit ?
        """,
        tuple(params),
    )
    output = []
    for row in result:
        output.append(
            {
                "chat_id": row["rowid"],
                "chat_guid": text(row["guid"], ""),
                "label": chat_label(row),
                "chat_identifier": text(row["chat_identifier"], ""),
                "chat_service": text(row["service_name"], ""),
                "message_services": sorted({item for item in text(row["message_service_csv"], "").split(",") if item}),
                "participants": participant_rows(conn, int(row["rowid"])),
                "unread_count": int(row["unread_count"] or 0),
                "latest_unread": apple_time_to_datetime(row["latest_unread_date"]),
            }
        )
    return output


def list_needs_reply_chats(
    conn: sqlite3.Connection,
    limit: int,
    days: int | None = None,
    query: str | None = None,
    service: str | None = None,
) -> list[dict[str, Any]]:
    message_cols = table_columns(conn, "message")
    clauses = ["m.is_from_me in (0, 1)", "m.is_empty = 0"]
    params: list[Any] = []
    cutoff = cutoff_for_days(days)
    if cutoff:
        clauses.append("m.date >= ?")
        params.append(cutoff)
    if query:
        like = f"%{query}%"
        clauses.append(
            """(
                c.display_name like ?
                or c.chat_identifier like ?
                or exists (
                    select 1
                    from chat_handle_join chj2
                    join handle h2 on h2.ROWID = chj2.handle_id
                    where chj2.chat_id = c.ROWID and h2.id like ?
                )
            )"""
        )
        params.extend([like, like, like])
    service_clause, service_params = service_filter_clause(service)
    if service_clause:
        clauses.append(service_clause)
        params.extend(service_params)
    result = rows(
        conn,
        f"""
        select
            c.ROWID as chat_id,
            c.guid as chat_guid,
            c.display_name,
            c.chat_identifier,
            c.service_name,
            m.ROWID as message_id,
            {column_expr(message_cols, "m", "guid", "message_guid")},
            {column_expr(message_cols, "m", "date", "date", "0")},
            {column_expr(message_cols, "m", "is_from_me", "is_from_me", "0")},
            {column_expr(message_cols, "m", "is_read", "is_read", "0")},
            {column_expr(message_cols, "m", "is_empty", "is_empty", "0")},
            {column_expr(message_cols, "m", "service", "service")},
            {column_expr(message_cols, "m", "text", "body")},
            {column_expr(message_cols, "m", "attributedBody", "attributed_body")},
            h.id as sender,
            count(a.ROWID) as attachment_count
        from chat_message_join cmj
        join message m on m.ROWID = cmj.message_id
        join chat c on c.ROWID = cmj.chat_id
        left join handle h on h.ROWID = m.handle_id
        left join message_attachment_join maj on maj.message_id = m.ROWID
        left join attachment a on a.ROWID = maj.attachment_id
        where {" and ".join(clauses)}
        group by m.ROWID, c.ROWID
        order by c.ROWID, m.date desc
        """,
        tuple(params),
    )
    latest_by_chat: dict[int, sqlite3.Row] = {}
    for row in result:
        chat_id = int(row["chat_id"])
        if chat_id not in latest_by_chat:
            latest_by_chat[chat_id] = row
    output = []
    for row in latest_by_chat.values():
        if row["is_from_me"]:
            continue
        output.append(
            {
                "chat_id": row["chat_id"],
                "chat_guid": text(row["chat_guid"], ""),
                "label": chat_label(row),
                "chat_identifier": text(row["chat_identifier"], ""),
                "chat_service": text(row["service_name"], ""),
                "latest_incoming": apple_time_to_datetime(row["date"]),
                "message_id": row["message_id"],
                "message_guid": text(row["message_guid"], ""),
                "from": sender_label(row),
                "unread": not bool(row["is_read"]),
                "service": text(row["service"], ""),
                "attachments": int(row["attachment_count"] or 0),
                "text": message_body(row),
            }
        )
    output.sort(key=lambda item: item["latest_incoming"], reverse=True)
    return output[:limit]


def attachment_file_exists(filename: str) -> bool:
    if not filename:
        return False
    try:
        return Path(filename).expanduser().is_file()
    except OSError:
        return False


def is_trusted_attachment_path(filename: str) -> bool:
    if not filename:
        return False
    try:
        path = Path(filename).expanduser().resolve(strict=False)
        root = DEFAULT_ATTACHMENT_ROOT.resolve(strict=False)
        return path.is_relative_to(root)
    except OSError:
        return False


def shell_path(value: str | Path) -> str:
    return shlex.quote(str(Path(value).expanduser()))


def attachment_lookup_command(db_path: str | Path, message_id: int) -> str:
    return (
        "apple-messages read attachments "
        f"--db {shell_path(db_path)} --message-id {message_id}"
    )


def with_attachment_commands(items: list[dict[str, Any]], db_path: str | Path) -> list[dict[str, Any]]:
    output = []
    for item in items:
        enriched = dict(item)
        if enriched.get("attachments") and enriched.get("message_id"):
            enriched["attachment_command"] = attachment_lookup_command(db_path, int(enriched["message_id"]))
        output.append(enriched)
    return output


def local_attachment_path(filename: str) -> str:
    if not filename:
        return ""
    try:
        return str(Path(filename).expanduser())
    except OSError:
        return filename


def attachment_metadata(conn: sqlite3.Connection, message_id: int) -> list[dict[str, Any]]:
    attachment_cols = table_columns(conn, "attachment")
    result = rows(
        conn,
        f"""
        select
            a.ROWID as attachment_id,
            {column_expr(attachment_cols, "a", "guid", "guid")},
            {column_expr(attachment_cols, "a", "filename", "filename")},
            {column_expr(attachment_cols, "a", "transfer_name", "transfer_name")},
            {column_expr(attachment_cols, "a", "uti", "uti")},
            {column_expr(attachment_cols, "a", "mime_type", "mime_type")},
            {column_expr(attachment_cols, "a", "total_bytes", "total_bytes", "0")},
            {column_expr(attachment_cols, "a", "transfer_state", "transfer_state", "0")},
            {column_expr(attachment_cols, "a", "is_outgoing", "is_outgoing", "0")},
            {column_expr(attachment_cols, "a", "is_sticker", "is_sticker", "0")}
        from message_attachment_join maj
        join attachment a on a.ROWID = maj.attachment_id
        where maj.message_id = ?
        order by a.ROWID
        """,
        (message_id,),
    )
    output = []
    for row in result:
        filename = text(row["filename"], "")
        local_path = local_attachment_path(filename)
        exists = attachment_file_exists(filename)
        trusted = exists and is_trusted_attachment_path(filename)
        output.append(
            {
                "attachment_id": row["attachment_id"],
                "guid": text(row["guid"], ""),
                "filename": filename,
                "local_path": local_path,
                "transfer_name": text(row["transfer_name"], ""),
                "uti": text(row["uti"], ""),
                "mime_type": text(row["mime_type"], ""),
                "total_bytes": int(row["total_bytes"] or 0),
                "transfer_state": int(row["transfer_state"] or 0),
                "is_outgoing": bool(row["is_outgoing"]),
                "is_sticker": bool(row["is_sticker"]),
                "file_exists": exists,
                "trusted_messages_attachment": trusted,
                "access": "read-local-file" if trusted else ("untrusted-local-file" if exists else "missing-local-file"),
            }
        )
    return output


def schema_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    output = []
    for table in sorted(table_names(conn)):
        count = single_value(conn, f"select count(*) from {table}")
        columns = sorted(table_columns(conn, table))
        output.append({"table": table, "rows": int(count or 0), "columns": columns})
    return output


def export_payload(conn: sqlite3.Connection, db_path: str, days: int | None, all_history: bool) -> dict[str, Any]:
    cutoff = cutoff_for_days(days)
    clauses: list[str] = []
    params: list[Any] = []
    if cutoff:
        clauses.append("m.date >= ?")
        params.append(cutoff)
    where = "where " + " and ".join(clauses) if clauses else ""
    chat_ids = [
        int(row["chat_id"])
        for row in rows(
            conn,
            f"""
            select distinct cmj.chat_id
            from chat_message_join cmj
            join message m on m.ROWID = cmj.message_id
            {where}
            order by cmj.chat_id
            """,
            tuple(params),
        )
    ]
    chats = [resolve_chat_by_id(conn, chat_id) for chat_id in chat_ids]
    messages: list[dict[str, Any]] = []
    for chat_id in chat_ids:
        messages.extend(fetch_messages(conn, chat_id, 1000000, days))
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "generated_at": generated_at(),
        "source": {
            "type": "Messages SQLite",
            "db_path": str(Path(db_path).expanduser()),
            "days": days,
            "all": all_history,
        },
        "counts": {
            "chats": len(chats),
            "messages": len(messages),
            "attachments": sum(message["attachments"] for message in messages),
        },
        "chats": chats,
        "messages": messages,
    }


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


def print_status(payload: dict[str, Any]) -> None:
    print(
        "Apple Messages access ok | "
        f"database={payload['database']} | "
        f"messages={payload['messages']} | "
        f"chats={payload['chats']} | "
        f"handles={payload['handles']} | "
        f"attachments={payload['attachments']} | "
        f"latest_message={payload['latest_message']}"
    )


def print_chats(chats: list[dict[str, Any]], label: str = "Apple Messages chats") -> None:
    print(f"{label} | count={len(chats)}")
    for chat in chats:
        print(
            " | ".join(
                [
                    f"chat_id={chat['chat_id']}",
                    f"guid={chat['chat_guid'] or '-'}",
                    f"label={chat['label']}",
                    f"chat_service={chat['chat_service'] or '-'}",
                    f"message_services={join_values(chat['message_services'])}",
                    f"latest={chat.get('latest', chat.get('latest_unread', '-'))}",
                    f"messages={chat.get('message_count', '-')}",
                    f"unread={chat.get('unread_estimate', chat.get('unread_count', '-'))}",
                    f"participants={participant_summary(chat['participants'])}",
                ]
            )
        )


def print_messages(label: str, messages: list[dict[str, Any]]) -> None:
    print(f"{label} | count={len(messages)}")
    for message in messages:
        parts = [
            f"message_id={message['message_id']}",
            f"guid={message['message_guid'] or '-'}",
            f"at={message['at']}",
            f"from={message['from']}",
            f"from_me={str(message['from_me']).lower()}",
            f"service={message['service'] or '-'}",
            f"attachments={message['attachments']}",
        ]
        if message.get("attachment_command"):
            parts.append(f"attachment_command={message['attachment_command']}")
        parts.append(f"text={message['text']}")
        print(" | ".join(parts))


def print_needs_reply(chats: list[dict[str, Any]]) -> None:
    print(f"Apple Messages needs-reply | chats={len(chats)}")
    for chat in chats:
        print(
            " | ".join(
                [
                    f"chat_id={chat['chat_id']}",
                    f"guid={chat['chat_guid'] or '-'}",
                    f"label={chat['label']}",
                    f"chat_service={chat['chat_service'] or '-'}",
                    f"service={chat['service'] or '-'}",
                    f"latest_incoming={chat['latest_incoming']}",
                    f"from={chat['from']}",
                    f"unread={str(chat['unread']).lower()}",
                    f"attachments={chat['attachments']}",
                    *([f"attachment_command={chat['attachment_command']}"] if chat.get("attachment_command") else []),
                    f"text={chat['text']}",
                ]
            )
        )


def print_attachments(attachments: list[dict[str, Any]]) -> None:
    print(f"Apple Messages attachments | count={len(attachments)}")
    for item in attachments:
        print(
            " | ".join(
                [
                    f"attachment_id={item['attachment_id']}",
                    f"guid={item['guid'] or '-'}",
                    f"transfer_name={item['transfer_name'] or '-'}",
                    f"mime_type={item['mime_type'] or '-'}",
                    f"uti={item['uti'] or '-'}",
                    f"bytes={item['total_bytes']}",
                    f"file_exists={str(item['file_exists']).lower()}",
                    f"trusted_messages_attachment={str(item['trusted_messages_attachment']).lower()}",
                    f"access={item['access']}",
                    f"local_path={item['local_path'] or '-'}",
                ]
            )
        )


def print_schema(schema: list[dict[str, Any]]) -> None:
    print("table\trows\tcolumns")
    for item in schema:
        print(f"{item['table']}\t{item['rows']}\t{','.join(item['columns'])}")


def command_status(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "database": str(Path(args.db).expanduser()),
        "messages": single_value(conn, "select count(*) from message"),
        "chats": single_value(conn, "select count(*) from chat"),
        "handles": single_value(conn, "select count(*) from handle"),
        "attachments": single_value(conn, "select count(*) from attachment") if "attachment" in table_names(conn) else 0,
        "latest_message": apple_time_to_datetime(single_value(conn, "select max(date) from message")),
    }
    print_json(payload) if args.json else print_status(payload)
    return 0


def command_chats(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    chats = list_chats(conn, args.limit, args.query, args.service)
    print_json(chats) if args.json else print_chats(chats)
    return 0


def command_show(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    chat = resolve_chat(conn, args)
    messages = with_attachment_commands(fetch_messages(conn, int(chat["chat_id"]), args.limit, args.days), args.db)
    payload = {"schema_version": SCHEMA_VERSION, "chat": chat, "messages": messages}
    if args.json:
        print_json(payload)
        return 0
    print(
        "Apple Messages show | "
        f"chat_id={chat['chat_id']} | guid={chat['chat_guid'] or '-'} | "
        f"label={chat['label']} | chat_service={chat['chat_service'] or '-'} | "
        f"participants={participant_summary(chat['participants'])}"
    )
    print_messages("messages", messages)
    return 0


def command_search(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    messages = with_attachment_commands(search_messages(conn, args.term, args.limit, args.days, args.service), args.db)
    print_json(messages) if args.json else print_messages(f"Apple Messages search | term={args.term}", messages)
    return 0


def command_unread(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    chats = list_unread_chats(conn, args.limit, args.query, args.service)
    print_json(chats) if args.json else print_chats(chats, "Apple Messages unread")
    return 0


def command_needs_reply(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    chats = with_attachment_commands(list_needs_reply_chats(conn, args.limit, args.days, args.query, args.service), args.db)
    print_json(chats) if args.json else print_needs_reply(chats)
    return 0


def command_attachments(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    attachments = attachment_metadata(conn, args.message_id)
    print_json(attachments) if args.json else print_attachments(attachments)
    return 0


def command_schema(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    schema = schema_rows(conn)
    print_json(schema) if args.json else print_schema(schema)
    return 0


def command_export(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    if not args.all and args.days is None:
        raise AppleMessagesReadError("export requires --days N or explicit --all")
    payload = export_payload(conn, args.db, args.days, args.all)
    if args.json:
        print_json(payload)
    else:
        counts = payload["counts"]
        print(
            "Apple Messages export | "
            f"days={args.days if args.days is not None else 'all'} | "
            f"chats={counts['chats']} | messages={counts['messages']} | attachments={counts['attachments']}"
        )
    return 0


def add_db_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Messages chat.db path. Defaults to ~/Library/Messages/chat.db.")


def add_service_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--service", choices=SERVICES, help="Filter by observed message/chat service.")


def add_chat_selector_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--chat-id", type=int, help="Messages chat ROWID.")
    parser.add_argument("--chat-guid", help="Exact chat.guid value.")
    parser.add_argument("--chat-identifier", help="Exact chat.chat_identifier value.")
    parser.add_argument("--handle", help="Exact participant phone/email handle.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read local Apple Messages.app data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Examples:
              apple-messages read status
              apple-messages read chats --service RCS
              apple-messages read show --chat-id 123 --limit 20
              apple-messages read export --days 7 --json
            """
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Verify read-only Messages database access.")
    add_db_option(status)
    status.add_argument("--json", action="store_true", help="Print JSON.")
    status.set_defaults(handler=command_status)

    chats = subparsers.add_parser("chats", help="List recent Messages chats.")
    add_db_option(chats)
    chats.add_argument("--limit", type=positive_limit, default=50)
    chats.add_argument("--query")
    add_service_option(chats)
    chats.add_argument("--json", action="store_true")
    chats.set_defaults(handler=command_chats)

    show = subparsers.add_parser("show", help="Show one chat and recent messages.")
    add_db_option(show)
    add_chat_selector_options(show)
    show.add_argument("--limit", type=positive_limit, default=DEFAULT_MESSAGE_LIMIT)
    show.add_argument("--days", type=positive_days)
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=command_show)

    search = subparsers.add_parser("search", help="Search message text.")
    add_db_option(search)
    search.add_argument("term")
    search.add_argument("--limit", type=positive_limit, default=DEFAULT_MESSAGE_LIMIT)
    search.add_argument("--days", type=positive_days)
    add_service_option(search)
    search.add_argument("--json", action="store_true")
    search.set_defaults(handler=command_search)

    unread = subparsers.add_parser("unread", help="List chats with unread incoming messages.")
    add_db_option(unread)
    unread.add_argument("--limit", type=positive_limit, default=25)
    unread.add_argument("--query")
    add_service_option(unread)
    unread.add_argument("--json", action="store_true")
    unread.set_defaults(handler=command_unread)

    needs_reply = subparsers.add_parser("needs-reply", help="List chats where the latest scanned message is incoming.")
    add_db_option(needs_reply)
    needs_reply.add_argument("--limit", type=positive_limit, default=25)
    needs_reply.add_argument("--days", type=positive_days, default=14)
    needs_reply.add_argument("--query")
    add_service_option(needs_reply)
    needs_reply.add_argument("--json", action="store_true")
    needs_reply.set_defaults(handler=command_needs_reply)

    attachments = subparsers.add_parser("attachments", help="Show attachment metadata for one message.")
    add_db_option(attachments)
    attachments.add_argument("--message-id", type=int, required=True)
    attachments.add_argument("--json", action="store_true")
    attachments.set_defaults(handler=command_attachments)

    schema = subparsers.add_parser("schema", help="Show Messages database table schema summary.")
    add_db_option(schema)
    schema.add_argument("--json", action="store_true")
    schema.set_defaults(handler=command_schema)

    export = subparsers.add_parser("export", help="Export bounded Messages data.")
    add_db_option(export)
    group = export.add_mutually_exclusive_group(required=False)
    group.add_argument("--days", type=positive_days, help="Only export messages newer than this many days.")
    group.add_argument("--all", action="store_true", help="Explicitly export full history.")
    export.add_argument("--json", action="store_true")
    export.set_defaults(handler=command_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        with connect(args.db) as conn:
            return args.handler(args, conn)
    except AppleMessagesReadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
