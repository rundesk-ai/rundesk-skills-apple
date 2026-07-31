#!/usr/bin/env python3
"""Read bounded Apple Mail metadata and message content from allowed accounts."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from apple_mail_lib import (
    DEFAULT_CONFIG,
    AppleMailError,
    generated_at,
    live_accounts,
    print_json,
    run_bridge,
    select_allowed_accounts,
    text,
    truncate,
)


def bounded_int(value: str, minimum: int, maximum: int, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise argparse.ArgumentTypeError(f"{label} must be between {minimum} and {maximum}")
    return parsed


def positive_limit(value: str) -> int:
    return bounded_int(value, 1, 500, "limit")


def positive_scan_limit(value: str) -> int:
    return bounded_int(value, 1, 2000, "scan limit")


def positive_days(value: str) -> int:
    return bounded_int(value, 1, 36500, "days")


def preview_chars(value: str) -> int:
    return bounded_int(value, 0, 500, "preview characters")


def body_chars(value: str) -> int:
    return bounded_int(value, 200, 20000, "body characters")


def selected(args):
    return select_allowed_accounts(args.config, live_accounts(), args.account_id)


def command_status(args):
    accounts = live_accounts()
    allowed = select_allowed_accounts(args.config, accounts, args.account_id)
    payload = {"status": "ok", "accounts": len(accounts), "allowed_accounts": len(allowed)}
    if args.json:
        print_json(payload)
    else:
        print(f"Apple Mail read access ok | accounts={len(accounts)} | allowed_accounts={len(allowed)}")
    return 0


def command_accounts(args):
    accounts = selected(args)
    if args.json:
        print_json(accounts)
    else:
        for account in accounts:
            print(f"account_id={account['id']} | name={account.get('name') or '-'} | enabled=true")
    return 0


def command_mailboxes(args):
    output = []
    for account in selected(args):
        for mailbox in run_bridge("mailboxes", [account["id"]]):
            output.append(dict(mailbox, account_id=account["id"], account_name=account.get("name", "")))
    if args.json:
        print_json(output)
    else:
        for mailbox in output:
            print(
                f"account={mailbox['account_name']} | account_id={mailbox['account_id']} | "
                f"mailbox={mailbox['path']} | unread={mailbox['unread_count']}"
            )
    return 0


def fetch_messages(args, *, unread_only=False, query="", content_mode="preview", message_id=""):
    since = ""
    if getattr(args, "days", None):
        since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat(timespec="seconds")
    accounts = selected(args)
    output = []
    scanned = 0

    def bridge_fetch(account, mode, result_limit, selected_ids=None):
        return run_bridge(
            "messages",
            [
                account["id"],
                args.mailbox,
                str(args.scan_limit),
                str(result_limit),
                "1" if unread_only else "0",
                query,
                since,
                mode,
                message_id,
                str(args.body_chars if mode == "full" else args.preview_chars if mode == "preview" else 0),
                json.dumps(selected_ids or []),
            ],
        )

    for account in accounts:
        initial_mode = "full" if content_mode == "full" else "none"
        payload = bridge_fetch(account, initial_mode, args.limit)
        scanned += int(payload.get("scanned", 0))
        for message in payload.get("messages", []):
            output.append(
                dict(
                    message,
                    account_id=account["id"],
                    account_name=account.get("name", ""),
                    mailbox=payload.get("mailbox", args.mailbox),
                )
            )
    output.sort(key=lambda item: item.get("date_received", ""), reverse=True)
    output = output[: args.limit]

    if content_mode == "preview" and args.preview_chars:
        account_by_id = {account["id"]: account for account in accounts}
        selected_by_account = {}
        for message in output:
            message_id_value = int(message.get("id", 0) or 0)
            if message_id_value <= 0:
                message["preview_unavailable"] = True
                continue
            selected_by_account.setdefault(message["account_id"], []).append(str(message_id_value))
        previews = {}
        for account_id, selected_ids in selected_by_account.items():
            account = account_by_id[account_id]
            payload = bridge_fetch(account, "preview", len(selected_ids), selected_ids)
            for message in payload.get("messages", []):
                previews[(account_id, str(message.get("id", 0)))] = message
        for message in output:
            message_id_value = int(message.get("id", 0) or 0)
            preview = previews.get((message["account_id"], str(message_id_value)), {}) if message_id_value > 0 else {}
            for key in (
                "to",
                "to_omitted",
                "preview",
                "preview_truncated",
                "preview_unavailable",
                "recipients_unavailable",
            ):
                if key in preview:
                    message[key] = preview[key]

    return output, scanned


def address_text(addresses, *, limit=3, max_chars=240, omitted=0):
    values = [text(item.get("address"), "") for item in addresses]
    values = [value for value in values if value]
    if not values:
        return "-"
    rendered = ",".join(values[:limit])
    remaining = max(0, len(values) - limit) + int(omitted or 0)
    if remaining:
        rendered += f",…(+{remaining})"
    return truncate(rendered, max_chars)


def preview_text(message):
    preview = str(message.get("preview") or "")
    return preview + ("…" if preview and message.get("preview_truncated") else "")


def print_messages(messages):
    for message in messages:
        print(
            f"date={text(message.get('date_received'))} | account={truncate(message.get('account_name'), 80)} | "
            f"account_id={text(message.get('account_id'))} | "
            f"mailbox={text(message.get('mailbox'))} | id={message.get('id', 0)} | "
            f"unread={str(not message.get('read', False)).lower()} | from={truncate(message.get('sender'), 100)} | "
            f"to={address_text(message.get('to', []), omitted=message.get('to_omitted', 0))} | "
            f"subject={truncate(message.get('subject'), 140)} | "
            f"preview={text(preview_text(message))}"
        )


def print_messages_csv(messages):
    fields = ("date_received", "account", "account_id", "mailbox", "id", "unread", "from", "to", "subject", "preview")
    writer = csv.DictWriter(sys.stdout, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for message in messages:
        writer.writerow(
            {
                "date_received": message.get("date_received", ""),
                "account": csv_cell(truncate(message.get("account_name"), 80)),
                "account_id": message.get("account_id", ""),
                "mailbox": message.get("mailbox", ""),
                "id": message.get("id", 0),
                "unread": not message.get("read", False),
                "from": csv_cell(truncate(message.get("sender"), 100)),
                "to": csv_cell(
                    address_text(message.get("to", []), omitted=message.get("to_omitted", 0))
                ),
                "subject": csv_cell(truncate(message.get("subject"), 140)),
                "preview": csv_cell(preview_text(message)),
            }
        )


def csv_cell(value):
    value = str(value or "")
    candidate = value.lstrip()
    return "'" + value if candidate.startswith(("=", "+", "-", "@")) else value


def output_format(args):
    return "json" if args.json else args.format


def command_messages(args, *, unread_only=False, query=""):
    messages, scanned = fetch_messages(args, unread_only=unread_only, query=query)
    payload = {"generated_at": generated_at(), "scanned": scanned, "messages": messages}
    selected_format = output_format(args)
    if selected_format == "json":
        print_json(payload)
    elif selected_format == "csv":
        print_messages_csv(messages)
    else:
        print_messages(messages)
    return 0


def command_show(args):
    if not args.account_id or len(set(args.account_id)) != 1:
        raise AppleMailError("show requires exactly one --account-id before message content can be read.")
    messages, scanned = fetch_messages(args, content_mode="full", message_id=args.message_id)
    if not messages:
        raise AppleMailError("No matching message found in the selected allowed account and mailbox.")
    if len(messages) > 1:
        raise AppleMailError("Message identifier was not unique; specify one exact --account-id.")
    payload = {"generated_at": generated_at(), "scanned": scanned, "message": messages[0]}
    if output_format(args) == "json":
        print_json(payload)
    else:
        message = messages[0]
        print(f"date: {text(message.get('date_received'))}")
        print(f"account: {text(message.get('account_name'))}")
        print(f"account_id: {text(message.get('account_id'))}")
        print(f"mailbox: {text(message.get('mailbox'))}")
        print(f"id: {message.get('id', 0)}")
        print(f"from: {text(message.get('sender'))}")
        print(
            f"to: {address_text(message.get('to', []), limit=20, max_chars=1000, omitted=message.get('to_omitted', 0))}"
        )
        print(
            f"cc: {address_text(message.get('cc', []), limit=20, max_chars=1000, omitted=message.get('cc_omitted', 0))}"
        )
        print(f"subject: {text(message.get('subject'))}")
        print(f"unread: {str(not message.get('read', False)).lower()}")
        print(f"body_truncated: {str(bool(message.get('content_truncated'))).lower()}")
        for attachment in message.get("attachments", []):
            print(
                f"attachment_id={text(attachment.get('id'))} | name={text(attachment.get('name'))} | "
                f"mime_type={text(attachment.get('mime_type'))} | size={attachment.get('file_size', 0)} | "
                f"downloaded={str(bool(attachment.get('downloaded'))).lower()}"
            )
        if message.get("attachments_omitted", 0):
            print(f"attachments_omitted: {message['attachments_omitted']}")
        print("body:")
        print(str(message.get("content") or "").strip())
    return 0


def add_selection_options(parser):
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Local allowlist config path.")
    parser.add_argument("--account-id", action="append", help="Allowed account ID. Repeat to select multiple.")


def add_message_options(parser, *, default_limit=25, show=False):
    add_selection_options(parser)
    parser.add_argument("--mailbox", default="INBOX", help="Exact mailbox path. Defaults to INBOX.")
    parser.add_argument("--limit", type=positive_limit, default=default_limit)
    parser.add_argument("--scan-limit", type=positive_scan_limit, default=250)
    parser.add_argument("--days", type=positive_days)
    if show:
        parser.add_argument("--body-chars", type=body_chars, default=4000)
        parser.set_defaults(preview_chars=0)
    else:
        parser.add_argument("--preview-chars", type=preview_chars, default=160)
        parser.set_defaults(body_chars=0)
    parser.set_defaults(format="text")
    if show:
        parser.add_argument("--json", action="store_true")
    else:
        formats = parser.add_mutually_exclusive_group()
        formats.add_argument("--format", choices=("text", "csv"), default="text")
        formats.add_argument("--json", action="store_true")


def build_parser():
    parser = argparse.ArgumentParser(description="Read Apple Mail from locally allowed accounts without changing message state.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "accounts", "mailboxes"):
        child = subparsers.add_parser(name)
        add_selection_options(child)
        child.add_argument("--json", action="store_true")
    for name in ("inbox", "unread"):
        add_message_options(subparsers.add_parser(name))
    search = subparsers.add_parser("search")
    search.add_argument("query")
    add_message_options(search, default_limit=50)
    show = subparsers.add_parser("show")
    show.add_argument("--message-id", required=True)
    add_message_options(show, default_limit=2, show=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            return command_status(args)
        if args.command == "accounts":
            return command_accounts(args)
        if args.command == "mailboxes":
            return command_mailboxes(args)
        if args.command == "unread":
            return command_messages(args, unread_only=True)
        if args.command == "search":
            return command_messages(args, query=args.query)
        if args.command == "show":
            return command_show(args)
        return command_messages(args)
    except AppleMailError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
