#!/usr/bin/env python3
"""Create Apple Mail drafts, send mail, or queue a later send with confirmation guards."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import mimetypes
import os
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr
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
DEFAULT_SCHEDULE_STORE = Path(
    os.environ.get(
        "APPLE_MAIL_SCHEDULE_STORE",
        str(DEFAULT_CONFIG.with_name("scheduled.json")),
    )
).expanduser()
AUTOMATION_TIMEOUT_SECONDS = 60
APPROVAL_TTL_SECONDS = 15 * 60
MAX_BODY_LENGTH = 100_000
MAX_RECIPIENTS = 100
MAX_SUBJECT_LENGTH = 500
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_ATTACHMENT_TOTAL_BYTES = 25 * 1024 * 1024
ATTACHMENT_READ_CHUNK = 1024 * 1024

PENDING = "pending"
SENDING = "sending"
SENT = "sent"
FAILED = "failed"
EXPIRED = "expired"
CANCELLED = "cancelled"
ACTIVE_STATES = (PENDING, SENDING)
MAX_SCHEDULED_ACTIVE = 200
MAX_SCHEDULED_HISTORY = 100
MAX_SCHEDULE_HORIZON_SECONDS = 365 * 24 * 60 * 60
DEFAULT_EXPIRE_AFTER_MINUTES = 24 * 60
MAX_EXPIRE_AFTER_MINUTES = 30 * 24 * 60
HISTORY_RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_RUN_DUE_BATCH = 25


def now_epoch() -> int:
    return int(time.time())


def iso_utc(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()


def iso_local(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).astimezone().isoformat()


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
        recovery = bridge_recovery(operation)
        raise AppleMailError(f"Mail.app {operation} failed or is indeterminate: {detail} {recovery}") from exc
    except subprocess.TimeoutExpired as exc:
        recovery = bridge_recovery(operation)
        raise AppleMailError(
            f"Mail.app {operation} timed out after {AUTOMATION_TIMEOUT_SECONDS} seconds. {recovery}"
        ) from exc
    except OSError as exc:
        raise AppleMailError(f"Unable to start Mail.app {operation} automation: {exc}") from exc
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        recovery = bridge_recovery(operation)
        raise AppleMailError(f"Mail.app {operation} returned an indeterminate response. {recovery}") from exc


def bridge_recovery(operation: str) -> str:
    if operation == "send":
        return "Delivery may already have been initiated; check Sent and Outbox before approving a retry."
    if operation in ("draft", "draft-mime"):
        return "A partial draft may exist; check Drafts before approving a retry."
    return "Grant Accessibility to the invoking terminal or agent process in System Settings."


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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(ATTACHMENT_READ_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def attachment_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("attachments", [])
    if values is None:
        return []
    if not isinstance(values, list):
        raise AppleMailError("attachments must be a list of local file paths.")
    if len(values) > MAX_ATTACHMENTS:
        raise AppleMailError(f"Email payload supports at most {MAX_ATTACHMENTS} attachments.")
    resolved: list[dict[str, Any]] = []
    total = 0
    for value in values:
        candidate = value.strip() if isinstance(value, str) else ""
        if not candidate:
            raise AppleMailError("Each attachment must be a nonempty local file path string.")
        if any(ord(char) < 32 or ord(char) == 127 for char in candidate):
            raise AppleMailError("Attachment paths must not contain control characters.")
        try:
            path = Path(candidate).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise AppleMailError(f"Attachment file does not exist: {candidate}: {exc}") from exc
        if not path.is_file():
            raise AppleMailError(f"Attachment is not a regular file: {path}")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise AppleMailError(f"Unable to read attachment: {path}: {exc}") from exc
        if size > MAX_ATTACHMENT_BYTES:
            raise AppleMailError(
                f"Attachment exceeds the {MAX_ATTACHMENT_BYTES}-byte limit: {path} is {size} bytes."
            )
        total += size
        if total > MAX_ATTACHMENT_TOTAL_BYTES:
            raise AppleMailError(
                f"Attachments exceed the {MAX_ATTACHMENT_TOTAL_BYTES}-byte total limit."
            )
        try:
            digest = file_sha256(path)
        except OSError as exc:
            raise AppleMailError(f"Unable to read attachment: {path}: {exc}") from exc
        resolved.append({"path": str(path), "name": path.name, "bytes": size, "sha256": digest})
    return resolved


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
        "attachments": attachment_list(raw),
    }


def action_sha256(operation: str, message: dict[str, Any], schedule: dict[str, Any] | None = None) -> str:
    exact = {
        "operation": operation,
        "account_id": message["account_id"],
        "from": message["from"],
        "to": message["to"],
        "cc": message["cc"],
        "bcc": message["bcc"],
        "subject": message["subject"],
        "body": message["body"],
        "attachments": [
            {"path": item["path"], "bytes": item["bytes"], "sha256": item["sha256"]}
            for item in message.get("attachments", [])
        ],
    }
    if schedule is not None:
        exact["send_at"] = int(schedule["send_at"])
        exact["expire_after_minutes"] = int(schedule["expire_after_minutes"])
    canonical = json.dumps(exact, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def approval_store_for(config_path: str, explicit_store: str | None = None) -> Path:
    if explicit_store:
        return Path(explicit_store).expanduser()
    config = Path(config_path).expanduser()
    if config == DEFAULT_CONFIG:
        return DEFAULT_APPROVAL_STORE
    return config.with_name("apple-mail-approvals.json")


def schedule_store_for(config_path: str, explicit_store: str | None = None) -> Path:
    if explicit_store:
        return Path(explicit_store).expanduser()
    config = Path(config_path).expanduser()
    if config == DEFAULT_CONFIG:
        return DEFAULT_SCHEDULE_STORE
    return config.with_name("apple-mail-scheduled.json")


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def approval_expiry(item: dict[str, Any]) -> int:
    try:
        return int(item.get("expires_at", 0))
    except (TypeError, ValueError):
        return 0


def read_store(path: Path, key: str, label: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppleMailError(f"Unable to read Apple Mail {label} store: {path}: {exc}") from exc
    items = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise AppleMailError(f"Apple Mail {label} store has an invalid shape.")
    return [item for item in items if isinstance(item, dict)]


def write_store(path: Path, key: str, label: str, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps({"schema_version": SCHEMA_VERSION, key: items}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise AppleMailError(f"Unable to write Apple Mail {label} store: {path}: {exc}") from exc


def with_store_lock(path: Path, label: str, callback):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    try:
        with lock_path.open("a+", encoding="utf-8") as lock:
            lock_path.chmod(0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            return callback()
    except OSError as exc:
        raise AppleMailError(f"Unable to lock Apple Mail {label} store: {path}: {exc}") from exc


def read_approvals(path: Path) -> list[dict[str, Any]]:
    return read_store(path, "approvals", "approval")


def write_approvals(path: Path, approvals: list[dict[str, Any]]) -> None:
    write_store(path, "approvals", "approval", approvals)


def with_approval_lock(path: Path, callback):
    return with_store_lock(path, "approval", callback)


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


def parse_send_at(value: Any) -> int:
    candidate = str(value or "").strip()
    if not candidate:
        raise AppleMailError("A scheduled send requires --at with an ISO 8601 date and time.")
    normalized = candidate[:-1] + "+00:00" if candidate.endswith(("Z", "z")) else candidate
    try:
        moment = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise AppleMailError(
            f"--at must be an ISO 8601 date and time such as 2026-08-05T09:00:00-04:00: {candidate}"
        ) from exc
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return int(moment.timestamp())


def validate_schedule(send_at: int, expire_after_minutes: int, now: int) -> dict[str, int]:
    if send_at <= now:
        raise AppleMailError("The scheduled send time must be in the future.")
    if send_at - now > MAX_SCHEDULE_HORIZON_SECONDS:
        raise AppleMailError("The scheduled send time must be within 365 days.")
    if not 1 <= expire_after_minutes <= MAX_EXPIRE_AFTER_MINUTES:
        raise AppleMailError(
            f"--expire-after-minutes must be between 1 and {MAX_EXPIRE_AFTER_MINUTES}."
        )
    return {"send_at": send_at, "expire_after_minutes": expire_after_minutes}


def read_scheduled(path: Path) -> list[dict[str, Any]]:
    return read_store(path, "scheduled", "scheduled send")


def write_scheduled(path: Path, items: list[dict[str, Any]]) -> None:
    write_store(path, "scheduled", "scheduled send", items)


def with_schedule_lock(path: Path, callback):
    return with_store_lock(path, "scheduled send", callback)


def item_int(item: dict[str, Any], key: str) -> int:
    try:
        return int(item.get(key, 0))
    except (TypeError, ValueError):
        return 0


def send_deadline(item: dict[str, Any]) -> int:
    return item_int(item, "send_at") + item_int(item, "expire_after_minutes") * 60


def prune_scheduled(items: list[dict[str, Any]], now: int) -> list[dict[str, Any]]:
    kept = [
        item
        for item in items
        if item.get("status") in ACTIVE_STATES
        or now - item_int(item, "finished_at") <= HISTORY_RETENTION_SECONDS
    ]
    active = [item for item in kept if item.get("status") in ACTIVE_STATES]
    history = [item for item in kept if item.get("status") not in ACTIVE_STATES]
    return active + history[-MAX_SCHEDULED_HISTORY:]


def schedule_id() -> str:
    return "sch_" + secrets.token_hex(6)


def enqueue_scheduled(store_path: Path, item: dict[str, Any]) -> None:
    now = item_int(item, "created_at")

    def update():
        items = prune_scheduled(read_scheduled(store_path), now)
        if sum(1 for entry in items if entry.get("status") in ACTIVE_STATES) >= MAX_SCHEDULED_ACTIVE:
            raise AppleMailError(
                f"At most {MAX_SCHEDULED_ACTIVE} Apple Mail sends can be scheduled at once."
            )
        if any(
            entry.get("status") == PENDING and entry.get("action_sha256") == item["action_sha256"]
            for entry in items
        ):
            raise AppleMailError("That exact Apple Mail send is already scheduled for that exact time.")
        items.append(item)
        write_scheduled(store_path, items)

    with_schedule_lock(store_path, update)


def scheduled_item(message: dict[str, Any], schedule: dict[str, int], action_hash: str, now: int) -> dict[str, Any]:
    return {
        "id": schedule_id(),
        "status": PENDING,
        "created_at": now,
        "send_at": schedule["send_at"],
        "expire_after_minutes": schedule["expire_after_minutes"],
        "action_sha256": action_hash,
        "message": {key: message[key] for key in
                    ("account_id", "account_name", "from", "to", "cc", "bcc", "subject", "body", "attachments")},
        "attempt_started_at": 0,
        "finished_at": 0,
        "error": "",
    }


def cancel_sha256(item: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"operation": "cancel", "id": item["id"], "action_sha256": item["action_sha256"]},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def find_scheduled(store_path: Path, identifier: str) -> dict[str, Any]:
    wanted = str(identifier or "").strip()
    if not wanted:
        raise AppleMailError("A scheduled send id is required.")
    for item in read_scheduled(store_path):
        if item.get("id") == wanted:
            return item
    raise AppleMailError(f"No scheduled Apple Mail send has id {wanted}.")


def require_cancellable(item: dict[str, Any]) -> dict[str, Any]:
    status = item.get("status")
    if status == SENDING:
        raise AppleMailError(
            f"Scheduled send {item.get('id')} is already being sent and cannot be cancelled; "
            "check Sent and Outbox before scheduling a replacement."
        )
    if status != PENDING:
        raise AppleMailError(f"Scheduled send {item.get('id')} is {status} and is no longer pending.")
    return item


def cancel_scheduled(store_path: Path, identifier: str, now: int) -> None:
    def update():
        items = read_scheduled(store_path)
        match = next((item for item in items if item.get("id") == identifier), None)
        if match is None:
            raise AppleMailError(f"No scheduled Apple Mail send has id {identifier}.")
        require_cancellable(match)
        match["status"] = CANCELLED
        match["finished_at"] = now
        write_scheduled(store_path, prune_scheduled(items, now))

    with_schedule_lock(store_path, update)


def claim_due(store_path: Path, now: int) -> dict[str, list[dict[str, Any]]]:
    claimed: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    indeterminate: list[dict[str, Any]] = []

    def update():
        del claimed[:], expired[:], indeterminate[:]
        stored = read_scheduled(store_path)
        items = prune_scheduled(stored, now)
        for item in items:
            status = item.get("status")
            if status == SENDING:
                indeterminate.append(dict(item))
                continue
            if status != PENDING or item_int(item, "send_at") > now:
                continue
            if now > send_deadline(item):
                item["status"] = EXPIRED
                item["finished_at"] = now
                item["error"] = (
                    f"Not sent within {item_int(item, 'expire_after_minutes')} minutes "
                    "of the scheduled time."
                )
                expired.append(dict(item))
                continue
            if len(claimed) >= MAX_RUN_DUE_BATCH:
                continue
            item["status"] = SENDING
            item["attempt_started_at"] = now
            claimed.append(dict(item))
        if claimed or expired or items != stored:
            write_scheduled(store_path, items)

    with_schedule_lock(store_path, update)
    return {"claimed": claimed, "expired": expired, "indeterminate": indeterminate}


def finish_scheduled(store_path: Path, identifier: str, status: str, error: str, now: int) -> None:
    def update():
        items = read_scheduled(store_path)
        for item in items:
            if item.get("id") == identifier and item.get("status") == SENDING:
                item["status"] = status
                item["finished_at"] = now
                item["error"] = error
        write_scheduled(store_path, items)

    with_schedule_lock(store_path, update)


def bridge_payload_for(message: dict[str, Any]) -> dict[str, Any]:
    payload = {key: message[key] for key in ("account_id", "from", "to", "cc", "bcc", "subject", "body")}
    payload["attachments"] = [item["path"] for item in message.get("attachments", [])]
    payload["attachment_metadata"] = [
        {"name": item["name"], "bytes": item["bytes"]}
        for item in message.get("attachments", [])
    ]
    return payload


def require_supported_attachment_operation(operation: str, message: dict[str, Any]) -> None:
    if message.get("attachments") and operation != "draft":
        raise AppleMailError(
            "Attachment-bearing Apple Mail sends and schedules are temporarily unavailable. "
            "Create a draft, verify it in Mail, and send it there."
        )
    if message.get("attachments") and (message.get("cc") or message.get("bcc")):
        raise AppleMailError(
            "Attachment-bearing native drafts temporarily support To recipients only; "
            "add Cc or Bcc in Mail after the draft is saved."
        )


def send_now(message: dict[str, Any], operation: str) -> None:
    require_supported_attachment_operation(operation, message)
    if operation == "draft" and message.get("attachments"):
        result = create_standard_attachment_draft(message)
    else:
        result = run_write_bridge(operation, bridge_payload_for(message))
    if (
        not isinstance(result, dict)
        or result.get("status") != "ok"
        or result.get("operation") != operation
        or result.get("attachments") != len(message.get("attachments", []))
    ):
        raise AppleMailError("Mail.app did not return a valid success confirmation for this action.")
    if operation == "draft" and message.get("attachments"):
        verify_saved_attachment_source(message, result, require_standard_attachment=True)


def standard_attachment_message(message: dict[str, Any]) -> EmailMessage:
    draft = EmailMessage(policy=policy.SMTP)
    draft["From"] = message["from"]
    draft["To"] = ", ".join(message.get("to", []))
    if message.get("cc"):
        draft["Cc"] = ", ".join(message["cc"])
    if message.get("bcc"):
        draft["Bcc"] = ", ".join(message["bcc"])
    draft["Subject"] = message["subject"]
    draft.set_content(message["body"], charset="utf-8")
    for item in message.get("attachments", []):
        mime_type, _ = mimetypes.guess_type(item["name"])
        maintype, subtype = (mime_type or "application/octet-stream").split("/", 1)
        try:
            content = Path(item["path"]).read_bytes()
        except OSError as exc:
            raise AppleMailError(f"Unable to read attachment: {item['path']}: {exc}") from exc
        draft.add_attachment(
            content,
            maintype=maintype,
            subtype=subtype,
            filename=item["name"],
            disposition="attachment",
        )
    return draft


def create_standard_attachment_draft(message: dict[str, Any]) -> dict[str, Any]:
    source = standard_attachment_message(message).as_bytes()
    with tempfile.TemporaryDirectory(prefix="rundesk-apple-mail-") as directory:
        path = Path(directory) / "approved-draft.eml"
        path.write_bytes(source)
        path.chmod(0o600)
        payload = bridge_payload_for(message)
        payload["account_name"] = message["account_name"]
        payload["eml_path"] = str(path)
        return run_write_bridge("draft-mime", payload)


def normalized_source_body(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def verify_saved_attachment_source(
    message: dict[str, Any], result: dict[str, Any], *, require_standard_attachment: bool = False
) -> None:
    source = result.get("saved_draft_source")
    if not isinstance(source, str) or not source:
        raise AppleMailError("Mail.app did not return the saved draft source for attachment verification.")
    try:
        parsed = BytesParser(policy=policy.default).parsebytes(source.encode("utf-8"))
        actual = []
        body_parts = []
        for part in parsed.walk():
            name = part.get_filename()
            if name is None:
                if not part.is_multipart() and part.get_content_type() == "text/plain":
                    body_parts.append(normalized_source_body(part.get_content()))
                continue
            content = part.get_payload(decode=True)
            if content is None:
                raise ValueError(f"attachment {name!r} has no decodable payload")
            actual.append(
                {
                    "name": name,
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "disposition": part.get_content_disposition(),
                    "content_id": part.get("Content-ID"),
                }
            )
    except (LookupError, TypeError, ValueError) as exc:
        raise AppleMailError(f"Mail.app saved draft attachment verification failed: {exc}") from exc
    expected = [
        {
            "name": item["name"],
            "bytes": item["bytes"],
            "sha256": item["sha256"],
            "disposition": "attachment" if require_standard_attachment else actual[index]["disposition"],
            "content_id": None if require_standard_attachment else actual[index]["content_id"],
        }
        for index, item in enumerate(message.get("attachments", []))
        if index < len(actual)
    ]
    if len(expected) != len(message.get("attachments", [])) or actual != expected:
        raise AppleMailError("Mail.app saved draft attachments do not exactly match the approved files.")
    if require_standard_attachment:
        if parsed.get_content_type() != "multipart/mixed":
            raise AppleMailError("Mail.app did not preserve a standard multipart attachment draft.")
        expected_from = [message["from"].lower()]
        actual_from = [address.lower() for _, address in getaddresses(parsed.get_all("From", []))]
        expected_to = [address.lower() for address in message.get("to", [])]
        actual_to = [address.lower() for _, address in getaddresses(parsed.get_all("To", []))]
        if actual_from != expected_from or actual_to != expected_to or parsed.get("Subject") != message["subject"]:
            raise AppleMailError("Mail.app saved draft envelope does not exactly match the approved action.")
        if len(body_parts) != 1:
            raise AppleMailError("Mail.app saved draft did not preserve one exact plain-text body.")
        expected_body = normalized_source_body(message["body"])
        if not expected_body.endswith("\n"):
            expected_body += "\n"
        if body_parts[0] != expected_body:
            raise AppleMailError("Mail.app saved draft body does not exactly match the approved action.")


def revalidate_scheduled(item: dict[str, Any], config_path: str) -> dict[str, Any]:
    stored = item.get("message")
    if not isinstance(stored, dict):
        raise AppleMailError("The scheduled message record is unreadable.")
    raw = dict(stored, attachments=[entry.get("path") for entry in stored.get("attachments", [])])
    message = normalize_payload(raw, config_path)
    schedule = {"send_at": item_int(item, "send_at"), "expire_after_minutes": item_int(item, "expire_after_minutes")}
    if action_sha256("schedule", message, schedule) != item.get("action_sha256"):
        raise AppleMailError(
            "The scheduled message no longer matches the approved action and was not sent."
        )
    return message


def action_payload(operation: str, message: dict[str, Any], confirmed: bool) -> dict[str, Any]:
    body = message["body"]
    attachments = message.get("attachments", [])
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
        "attachments": [dict(item) for item in attachments],
        "attachment_count": len(attachments),
        "attachment_bytes": sum(item["bytes"] for item in attachments),
        "action_sha256": action_sha256(operation, message),
    }


ACTION_WORDING = {
    "draft": ("create Apple Mail draft", "created Apple Mail draft"),
    "send": ("send Apple Mail", "sent Apple Mail"),
    "schedule": ("schedule Apple Mail send", "scheduled Apple Mail send"),
}


def print_action(payload: dict[str, Any]) -> None:
    operation = payload["operation"]
    intent, done = ACTION_WORDING[operation]
    prefix = f"dry-run: would {intent}" if payload["dry_run"] else done
    print(
        f"{prefix} | account={text(payload['account_name'])} | account_id={payload['account_id']} | "
        f"from={payload['from']} | to={','.join(payload['to']) or '-'} | cc={','.join(payload['cc']) or '-'} | "
        f"bcc={','.join(payload['bcc']) or '-'} | subject={payload['subject']} | "
        f"body_length={payload['body_length']} | body_sha256={payload['body_sha256']} | "
        f"attachments={payload['attachment_count']} | attachment_bytes={payload['attachment_bytes']}"
    )
    for index, item in enumerate(payload["attachments"]):
        print(
            f"attachment[{index}]={text(item['name'])} | bytes={item['bytes']} | "
            f"sha256={item['sha256']} | path={text(item['path'])}"
        )
    if operation == "schedule":
        print(
            f"send_at={payload['send_at']} | send_at_local={payload['send_at_local']} | "
            f"expire_after_minutes={payload['expire_after_minutes']}"
        )
        if not payload["dry_run"]:
            print(f"schedule_id={payload['schedule_id']}")
            print(
                "Nothing is sent until a run-due invocation fires at or after that time. "
                "Cancel it with `write cancel --id " + payload["schedule_id"] + "`."
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
    accessibility = run_write_bridge("accessibility-status", {})
    if (
        not isinstance(accessibility, dict)
        or accessibility.get("status") != "ok"
        or accessibility.get("accessibility") is not True
    ):
        raise AppleMailError("Mail Accessibility status did not return a valid confirmation.")
    payload = {
        "status": "ok",
        "accounts": len(accounts),
        "allowed_sender_accounts": len(allowed),
        "accessibility": True,
    }
    print_json(payload) if args.json else print(
        f"Apple Mail write access ok | accounts={len(accounts)} | "
        f"allowed_sender_accounts={len(allowed)} | accessibility=ok"
    )
    return 0


def command_action(args):
    message = normalize_payload(load_payload(args.payload), args.config)
    require_supported_attachment_operation(args.command, message)
    action_hash = action_sha256(args.command, message)
    approval_store = approval_store_for(args.config, args.approval_store)
    if args.confirm:
        consume_confirmation(args.confirm, action_hash, approval_store)
        send_now(message, args.command)
    payload = action_payload(args.command, message, bool(args.confirm))
    if not args.confirm:
        token, expires_at = issue_confirmation(action_hash, approval_store)
        payload["confirmation_token"] = token
        payload["confirmation_expires_at"] = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()
    print_json(payload) if args.json else print_action(payload)
    return 0


def command_schedule(args):
    now = now_epoch()
    message = normalize_payload(load_payload(args.payload), args.config)
    require_supported_attachment_operation("schedule", message)
    schedule = validate_schedule(parse_send_at(args.at), args.expire_after_minutes, now)
    action_hash = action_sha256("schedule", message, schedule)
    approval_store = approval_store_for(args.config, args.approval_store)
    payload = action_payload("schedule", message, bool(args.confirm))
    payload["send_at"] = iso_utc(schedule["send_at"])
    payload["send_at_local"] = iso_local(schedule["send_at"])
    payload["send_at_epoch"] = schedule["send_at"]
    payload["expire_after_minutes"] = schedule["expire_after_minutes"]
    if args.confirm:
        consume_confirmation(args.confirm, action_hash, approval_store)
        item = scheduled_item(message, schedule, action_hash, now)
        enqueue_scheduled(schedule_store_for(args.config, args.schedule_store), item)
        payload["schedule_id"] = item["id"]
    else:
        token, expires_at = issue_confirmation(action_hash, approval_store)
        payload["confirmation_token"] = token
        payload["confirmation_expires_at"] = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()
    print_json(payload) if args.json else print_action(payload)
    return 0


def scheduled_summary(item: dict[str, Any]) -> dict[str, Any]:
    message = item.get("message", {}) if isinstance(item.get("message"), dict) else {}
    return {
        "id": item.get("id", ""),
        "status": item.get("status", ""),
        "send_at": iso_utc(item_int(item, "send_at")),
        "send_at_local": iso_local(item_int(item, "send_at")),
        "expires_at": iso_utc(send_deadline(item)),
        "expire_after_minutes": item_int(item, "expire_after_minutes"),
        "account_id": message.get("account_id", ""),
        "from": message.get("from", ""),
        "to": list(message.get("to", [])),
        "cc": list(message.get("cc", [])),
        "bcc": list(message.get("bcc", [])),
        "subject": message.get("subject", ""),
        "attachment_count": len(message.get("attachments", [])),
        "action_sha256": item.get("action_sha256", ""),
        "error": item.get("error", ""),
    }


def print_scheduled(row: dict[str, Any]) -> None:
    print(
        f"scheduled | id={row['id']} | status={row['status']} | send_at={row['send_at']} | "
        f"send_at_local={row['send_at_local']} | expires_at={row['expires_at']} | "
        f"from={row['from']} | to={','.join(row['to']) or '-'} | cc={','.join(row['cc']) or '-'} | "
        f"bcc={','.join(row['bcc']) or '-'} | subject={text(row['subject'])} | "
        f"attachments={row['attachment_count']} | error={text(row['error'])}"
    )


def command_scheduled(args):
    store_path = schedule_store_for(args.config, args.schedule_store)
    items = prune_scheduled(read_scheduled(store_path), now_epoch())
    if args.pending:
        items = [item for item in items if item.get("status") in ACTIVE_STATES]
    rows = [scheduled_summary(item) for item in sorted(items, key=lambda item: item_int(item, "send_at"))]
    if args.json:
        print_json({"schema_version": SCHEMA_VERSION, "status": "ok", "scheduled": rows, "count": len(rows)})
        return 0
    for row in rows:
        print_scheduled(row)
    print(f"scheduled sends: {len(rows)}")
    return 0


def command_cancel(args):
    now = now_epoch()
    store_path = schedule_store_for(args.config, args.schedule_store)
    item = require_cancellable(find_scheduled(store_path, args.id))
    cancel_hash = cancel_sha256(item)
    approval_store = approval_store_for(args.config, args.approval_store)
    row = scheduled_summary(item)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "operation": "cancel",
        "dry_run": not args.confirm,
        "status": "ok",
        "scheduled": row,
        "action_sha256": cancel_hash,
    }
    if args.confirm:
        consume_confirmation(args.confirm, cancel_hash, approval_store)
        cancel_scheduled(store_path, item["id"], now)
    else:
        token, expires_at = issue_confirmation(cancel_hash, approval_store)
        payload["confirmation_token"] = token
        payload["confirmation_expires_at"] = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()
    if args.json:
        print_json(payload)
        return 0
    prefix = "dry-run: would cancel scheduled Apple Mail send" if payload["dry_run"] else "cancelled scheduled Apple Mail send"
    print(f"{prefix} | id={row['id']} | send_at={row['send_at']} | subject={text(row['subject'])}")
    if payload["dry_run"]:
        print(f"action_sha256={cancel_hash}")
        print(f"confirmation_token={payload['confirmation_token']}")
        print(f"confirmation_expires_at={payload['confirmation_expires_at']}")
        print(
            f"After the owner approves this exact cancellation, pass --confirm "
            f"{payload['confirmation_token']} before it expires. The token works once."
        )
    return 0


def command_run_due(args):
    now = now_epoch()
    store_path = schedule_store_for(args.config, args.schedule_store)
    if args.dry_run:
        due = [
            item
            for item in prune_scheduled(read_scheduled(store_path), now)
            if item.get("status") == PENDING and item_int(item, "send_at") <= now
        ]
        rows = [dict(scheduled_summary(item), outcome="due" if now <= send_deadline(item) else "expired")
                for item in due]
        payload = {"schema_version": SCHEMA_VERSION, "operation": "run-due", "dry_run": True,
                   "status": "ok", "results": rows, "due": len(rows)}
        if args.json:
            print_json(payload)
        else:
            for row in rows:
                print_scheduled(row)
            print(f"dry-run: would process {len(rows)} due Apple Mail send(s)")
        return 0

    batch = claim_due(store_path, now)
    results = [dict(scheduled_summary(item), outcome=EXPIRED) for item in batch["expired"]]
    results += [dict(scheduled_summary(item), outcome="indeterminate") for item in batch["indeterminate"]]
    for item in batch["claimed"]:
        try:
            message = revalidate_scheduled(item, args.config)
            send_now(message, "send")
        except AppleMailError as exc:
            finish_scheduled(store_path, item["id"], FAILED, str(exc), now_epoch())
            results.append(dict(scheduled_summary(item), status=FAILED, outcome=FAILED, error=str(exc)))
            continue
        finish_scheduled(store_path, item["id"], SENT, "", now_epoch())
        results.append(dict(scheduled_summary(item), status=SENT, outcome=SENT))
    counts = {state: sum(1 for row in results if row["outcome"] == state)
              for state in (SENT, FAILED, EXPIRED, "indeterminate")}
    if args.json:
        print_json({"schema_version": SCHEMA_VERSION, "operation": "run-due", "dry_run": False,
                    "status": "ok", "results": results, **counts})
        return 0
    for row in results:
        print(
            f"{row['outcome']} | id={row['id']} | send_at={row['send_at']} | from={row['from']} | "
            f"to={','.join(row['to']) or '-'} | subject={text(row['subject'])} | error={text(row['error'])}"
        )
    print(
        f"apple-mail run-due | sent={counts[SENT]} | failed={counts[FAILED]} | "
        f"expired={counts[EXPIRED]} | indeterminate={counts['indeterminate']}"
    )
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Create Apple Mail drafts, send mail, or queue a later send with confirmation guards."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Local account allowlist config path.")
    parser.add_argument("--approval-store", help="Local one-time confirmation store path. Defaults beside --config.")
    parser.add_argument("--schedule-store", help="Local scheduled-send queue path. Defaults beside --config.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status")
    status.add_argument("--json", action="store_true")
    for name in ("draft", "send"):
        child = subparsers.add_parser(name)
        child.add_argument("--payload", required=True, help="JSON email payload path.")
        child.add_argument("--confirm", metavar="ONE_TIME_TOKEN", help="One-time token printed by a fresh dry-run.")
        child.add_argument("--json", action="store_true")
    schedule = subparsers.add_parser("schedule")
    schedule.add_argument("--payload", required=True, help="JSON email payload path.")
    schedule.add_argument("--at", required=True, help="ISO 8601 send time. A time without an offset is local.")
    schedule.add_argument(
        "--expire-after-minutes",
        type=int,
        default=DEFAULT_EXPIRE_AFTER_MINUTES,
        help="Skip the send if it is this many minutes overdue. Defaults to 1440.",
    )
    schedule.add_argument("--confirm", metavar="ONE_TIME_TOKEN", help="One-time token printed by a fresh dry-run.")
    schedule.add_argument("--json", action="store_true")
    listing = subparsers.add_parser("scheduled")
    listing.add_argument("--pending", action="store_true", help="Show only pending and in-flight sends.")
    listing.add_argument("--json", action="store_true")
    cancel = subparsers.add_parser("cancel")
    cancel.add_argument("--id", required=True, help="Scheduled send id from `write scheduled`.")
    cancel.add_argument("--confirm", metavar="ONE_TIME_TOKEN", help="One-time token printed by a fresh dry-run.")
    cancel.add_argument("--json", action="store_true")
    run_due = subparsers.add_parser("run-due")
    run_due.add_argument("--dry-run", action="store_true", help="Report due sends without sending them.")
    run_due.add_argument("--json", action="store_true")
    return parser


COMMANDS = {
    "status": command_status,
    "draft": command_action,
    "send": command_action,
    "schedule": command_schedule,
    "scheduled": command_scheduled,
    "cancel": command_cancel,
    "run-due": command_run_due,
}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except AppleMailError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
