#!/usr/bin/env python3
"""Create Apple Mail drafts or send mail with exact-account and confirmation guards."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from apple_mail_lib import (
    DEFAULT_CONFIG,
    AppleMailError,
    live_accounts,
    print_json,
    select_allowed_accounts,
    text,
    truncate,
)


SCHEMA_VERSION = "1.0"
WRITE_BRIDGE = Path(__file__).resolve().parent / "AppleMailWriteBridge.js"
DEFAULT_APPROVAL_STORE = Path(
    os.environ.get(
        "APPLE_MAIL_APPROVAL_STORE",
        str(DEFAULT_CONFIG.with_name("approvals.json")),
    )
).expanduser()
AUTOMATION_TIMEOUT_SECONDS = 60
APPROVAL_TTL_SECONDS = 15 * 60
MAX_BODY_LENGTH = 100_000
MAX_RECIPIENTS = 100
MAX_SUBJECT_LENGTH = 500


def run_write_bridge(operation: str, payload: dict[str, Any]) -> Any:
    invocation = [
        "/usr/bin/osascript",
        "-l",
        "JavaScript",
        str(WRITE_BRIDGE),
        operation,
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
    ]
    try:
        result = subprocess.run(
            invocation,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=AUTOMATION_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        recovery = (
            "Delivery may already have been initiated; check Sent and Outbox before approving a retry."
            if operation == "send"
            else "A partial draft may exist; check Drafts before approving a retry."
        )
        raise AppleMailError(f"Mail.app {operation} failed or is indeterminate: {detail} {recovery}") from exc
    except subprocess.TimeoutExpired as exc:
        recovery = (
            "Delivery may already have been initiated; check Sent and Outbox before approving a retry."
            if operation == "send"
            else "A partial draft may exist; check Drafts before approving a retry."
        )
        raise AppleMailError(
            f"Mail.app {operation} timed out after {AUTOMATION_TIMEOUT_SECONDS} seconds. {recovery}"
        ) from exc
    except OSError as exc:
        raise AppleMailError(f"Unable to start Mail.app {operation} automation: {exc}") from exc
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        recovery = (
            "Delivery may already have been initiated; check Sent and Outbox before approving a retry."
            if operation == "send"
            else "A partial draft may exist; check Drafts before approving a retry."
        )
        raise AppleMailError(f"Mail.app {operation} returned an indeterminate response. {recovery}") from exc


def load_payload(path: str | Path) -> dict[str, Any]:
    payload_path = Path(path).expanduser()
    try:
        raw = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppleMailError(f"Unable to read email payload: {payload_path}: {exc}") from exc
    payload = raw.get("email", raw) if isinstance(raw, dict) else raw
    if not isinstance(payload, dict):
        raise AppleMailError("Email payload must be a JSON object or contain an email object.")
    return payload


def email_address(value: Any, label: str) -> str:
    candidate = str(value or "").strip()
    _, address = parseaddr(candidate)
    if (
        not candidate
        or not address
        or "@" not in address
        or candidate.lower() != address.lower()
        or any(ord(char) < 32 or ord(char) == 127 for char in candidate)
    ):
        raise AppleMailError(f"{label} contains an invalid email address.")
    return address


def address_list(payload: dict[str, Any], key: str) -> list[str]:
    values = payload.get(key, [])
    if values is None:
        return []
    if not isinstance(values, list):
        raise AppleMailError(f"{key} must be a list of email addresses.")
    return [email_address(value, key) for value in values]


def normalize_payload(raw: dict[str, Any], config_path: str) -> dict[str, Any]:
    account_id = str(raw.get("account_id") or "").strip()
    if not account_id:
        raise AppleMailError("Email payload requires account_id.")
    accounts = live_accounts()
    selected = select_allowed_accounts(config_path, accounts, [account_id])
    account = selected[0]
    allowed_senders = {
        str(value).strip().lower()
        for value in account.get("email_addresses", [])
        if str(value).strip()
    }
    sender = email_address(raw.get("from"), "from")
    _, sender_address = parseaddr(sender)
    if sender_address.lower() not in allowed_senders:
        raise AppleMailError("The from address is not configured on the selected allowed Apple Mail account.")
    matching_account_ids = {
        str(candidate.get("id"))
        for candidate in accounts
        if sender_address.lower()
        in {
            str(value).strip().lower()
            for value in candidate.get("email_addresses", [])
            if str(value).strip()
        }
    }
    if matching_account_ids != {account_id}:
        raise AppleMailError("The from address must map uniquely to the selected Apple Mail account.")
    to = address_list(raw, "to")
    cc = address_list(raw, "cc")
    bcc = address_list(raw, "bcc")
    if not to and not cc and not bcc:
        raise AppleMailError("Email payload requires at least one to, cc, or bcc recipient.")
    if len(to) + len(cc) + len(bcc) > MAX_RECIPIENTS:
        raise AppleMailError(f"Email payload supports at most {MAX_RECIPIENTS} recipients.")
    subject = str(raw.get("subject") or "").strip()
    body = str(raw.get("body") or "")
    if not subject:
        raise AppleMailError("Email payload requires a nonempty subject.")
    if len(subject) > MAX_SUBJECT_LENGTH:
        raise AppleMailError(f"Email subject exceeds the {MAX_SUBJECT_LENGTH}-character limit.")
    if any(ord(char) < 32 or ord(char) == 127 for char in subject):
        raise AppleMailError("Email subject must not contain control characters.")
    if not body.strip():
        raise AppleMailError("Email payload requires a nonempty body.")
    if len(body) > MAX_BODY_LENGTH:
        raise AppleMailError(f"Email body exceeds the {MAX_BODY_LENGTH}-character limit.")
    return {
        "account_id": account_id,
        "account_name": account.get("name", ""),
        "from": sender_address,
        "to": to,
        "cc": cc,
        "bcc": bcc,
        "subject": subject,
        "body": body,
    }


def action_sha256(operation: str, message: dict[str, Any]) -> str:
    exact = {
        "operation": operation,
        "account_id": message["account_id"],
        "from": message["from"],
        "to": message["to"],
        "cc": message["cc"],
        "bcc": message["bcc"],
        "subject": message["subject"],
        "body": message["body"],
    }
    canonical = json.dumps(exact, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def approval_store_for(config_path: str, explicit_store: str | None = None) -> Path:
    if explicit_store:
        return Path(explicit_store).expanduser()
    config = Path(config_path).expanduser()
    if config == DEFAULT_CONFIG:
        return DEFAULT_APPROVAL_STORE
    return config.with_name("apple-mail-approvals.json")


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def approval_expiry(item: dict[str, Any]) -> int:
    try:
        return int(item.get("expires_at", 0))
    except (TypeError, ValueError):
        return 0


def read_approvals(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppleMailError(f"Unable to read Apple Mail approval store: {path}: {exc}") from exc
    approvals = payload.get("approvals") if isinstance(payload, dict) else None
    if not isinstance(approvals, list):
        raise AppleMailError("Apple Mail approval store has an invalid shape.")
    return [item for item in approvals if isinstance(item, dict)]


def write_approvals(path: Path, approvals: list[dict[str, Any]]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps({"schema_version": SCHEMA_VERSION, "approvals": approvals}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise AppleMailError(f"Unable to write Apple Mail approval store: {path}: {exc}") from exc


def with_approval_lock(path: Path, callback):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    try:
        with lock_path.open("a+", encoding="utf-8") as lock:
            lock_path.chmod(0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            return callback()
    except OSError as exc:
        raise AppleMailError(f"Unable to lock Apple Mail approval store: {path}: {exc}") from exc


def issue_confirmation(action_hash: str, store_path: Path) -> tuple[str, int]:
    token = "mail_" + secrets.token_urlsafe(24)
    now = int(time.time())
    expires_at = now + APPROVAL_TTL_SECONDS

    def update():
        approvals = [
            item
            for item in read_approvals(store_path)
            if approval_expiry(item) > now and item.get("action_sha256") != action_hash
        ]
        approvals.append(
            {
                "token_sha256": token_digest(token),
                "action_sha256": action_hash,
                "expires_at": expires_at,
            }
        )
        write_approvals(store_path, approvals[-100:])

    with_approval_lock(store_path, update)
    return token, expires_at


def consume_confirmation(token: str, action_hash: str, store_path: Path) -> None:
    now = int(time.time())
    digest = token_digest(token)

    def update():
        approvals = read_approvals(store_path)
        matched = any(
            secrets.compare_digest(str(item.get("token_sha256", "")), digest)
            and secrets.compare_digest(str(item.get("action_sha256", "")), action_hash)
            and approval_expiry(item) > now
            for item in approvals
        )
        remaining = [
            item
            for item in approvals
            if approval_expiry(item) > now and item.get("action_sha256") != action_hash
        ]
        write_approvals(store_path, remaining)
        if not matched:
            raise AppleMailError("Confirmation token is invalid, expired, already used, or belongs to another action.")

    with_approval_lock(store_path, update)


def action_payload(operation: str, message: dict[str, Any], confirmed: bool) -> dict[str, Any]:
    body = message["body"]
    return {
        "schema_version": SCHEMA_VERSION,
        "operation": operation,
        "dry_run": not confirmed,
        "status": "ok",
        "account_id": message["account_id"],
        "account_name": message["account_name"],
        "from": message["from"],
        "to": message["to"],
        "cc": message["cc"],
        "bcc": message["bcc"],
        "subject": message["subject"],
        "body_preview": truncate(body, 240),
        "body_length": len(body),
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "action_sha256": action_sha256(operation, message),
    }


def print_action(payload: dict[str, Any]) -> None:
    operation = payload["operation"]
    if payload["dry_run"]:
        prefix = f"dry-run: would {'create Apple Mail draft' if operation == 'draft' else 'send Apple Mail'}"
    else:
        prefix = "created Apple Mail draft" if operation == "draft" else "sent Apple Mail"
    print(
        f"{prefix} | account={text(payload['account_name'])} | account_id={payload['account_id']} | "
        f"from={payload['from']} | to={','.join(payload['to']) or '-'} | cc={','.join(payload['cc']) or '-'} | "
        f"bcc={','.join(payload['bcc']) or '-'} | subject={payload['subject']} | "
        f"body_length={payload['body_length']} | body_sha256={payload['body_sha256']}"
    )
    if payload["dry_run"]:
        print(f"body_preview={payload['body_preview']}")
        print(f"action_sha256={payload['action_sha256']}")
        print(f"confirmation_token={payload['confirmation_token']}")
        print(f"confirmation_expires_at={payload['confirmation_expires_at']}")
        print(
            f"After the owner approves this exact email {operation} action, pass "
            f"--confirm {payload['confirmation_token']} before it expires. The token works once."
        )


def command_status(args):
    accounts = live_accounts()
    allowed = select_allowed_accounts(args.config, accounts, None)
    payload = {"status": "ok", "accounts": len(accounts), "allowed_sender_accounts": len(allowed)}
    print_json(payload) if args.json else print(
        f"Apple Mail write access ok | accounts={len(accounts)} | allowed_sender_accounts={len(allowed)}"
    )
    return 0


def command_action(args):
    message = normalize_payload(load_payload(args.payload), args.config)
    action_hash = action_sha256(args.command, message)
    approval_store = approval_store_for(args.config, args.approval_store)
    if args.confirm:
        consume_confirmation(args.confirm, action_hash, approval_store)
        bridge_payload = {
            key: message[key]
            for key in ("account_id", "from", "to", "cc", "bcc", "subject", "body")
        }
        result = run_write_bridge(args.command, bridge_payload)
        if not isinstance(result, dict) or result.get("status") != "ok" or result.get("operation") != args.command:
            raise AppleMailError("Mail.app did not return a valid success confirmation for this action.")
    payload = action_payload(args.command, message, bool(args.confirm))
    if not args.confirm:
        token, expires_at = issue_confirmation(action_hash, approval_store)
        payload["confirmation_token"] = token
        payload["confirmation_expires_at"] = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()
    print_json(payload) if args.json else print_action(payload)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Create Apple Mail drafts or send mail with confirmation guards.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Local account allowlist config path.")
    parser.add_argument("--approval-store", help="Local one-time confirmation store path. Defaults beside --config.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status")
    status.add_argument("--json", action="store_true")
    for name in ("draft", "send"):
        child = subparsers.add_parser(name)
        child.add_argument("--payload", required=True, help="JSON email payload path.")
        child.add_argument("--confirm", metavar="ONE_TIME_TOKEN", help="One-time token printed by a fresh dry-run.")
        child.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return command_status(args) if args.command == "status" else command_action(args)
    except AppleMailError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
