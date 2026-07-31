#!/usr/bin/env python3
"""
Safely mutate Apple Contacts through Contacts.framework.

Usage:
  apple-contacts write status [--json]
  apple-contacts write create --payload contact.json [--container-id ID] [--confirm] [--json]
  apple-contacts write update --id CONTACT_ID --payload patch.json [--confirm] [--json]
  apple-contacts write delete --id CONTACT_ID [--confirm] [--json]
  apple-contacts write groups list [--json]
  apple-contacts write groups members --id GROUP_ID [--json]
  apple-contacts write groups create --name NAME [--container-id ID] [--confirm] [--json]
  apple-contacts write groups update --id GROUP_ID --name NAME [--confirm] [--json]
  apple-contacts write groups delete --id GROUP_ID [--confirm] [--json]
  apple-contacts write groups add-contact --contact-id CONTACT_ID --group-id GROUP_ID [--confirm] [--json]
  apple-contacts write groups remove-contact --contact-id CONTACT_ID --group-id GROUP_ID [--confirm] [--json]

Inputs:
  JSON payload files for create/update. The payload may either be the contact
  object directly or an object with a top-level "contact" object.

Outputs:
  Default output is concise text for agent review. Use --json for the full
  Contacts.framework bridge response.

Write/mutation behavior:
  All mutation commands are dry-runs unless --confirm is passed. Writes are
  performed through Contacts.framework, never by writing AddressBook SQLite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
BRIDGE_SOURCE = SCRIPT_DIR / "AppleContactsBridge.swift"


def _cache_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "rundesk-scripts" / "apple-contacts"


CACHE_DIR = _cache_dir()
BRIDGE_BINARY = CACHE_DIR / "apple-contacts-bridge"

AUTHORIZATION_STATES = {
    0: "notDetermined",
    1: "restricted",
    2: "denied",
    3: "authorized",
}


class AppleContactsWriteError(RuntimeError):
    pass


CONTACT_SCALAR_FIELDS = {
    "name_prefix",
    "given_name",
    "middle_name",
    "family_name",
    "previous_family_name",
    "name_suffix",
    "nickname",
    "organization_name",
    "department_name",
    "job_title",
    "note",
    "birthday",
}
CONTACT_ARRAY_FIELDS = {
    "phones",
    "emails",
    "addresses",
    "postal_addresses",
    "urls",
    "social_profiles",
    "instant_messages",
    "relations",
    "dates",
}
CONTACT_FIELDS = CONTACT_SCALAR_FIELDS | CONTACT_ARRAY_FIELDS


def bridge_source_hash() -> str:
    return hashlib.sha256(BRIDGE_SOURCE.read_bytes()).hexdigest()[:12]


def ensure_bridge_binary() -> Path:
    if not BRIDGE_SOURCE.is_file():
        raise AppleContactsWriteError(f"Contacts bridge source not found: {BRIDGE_SOURCE}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = CACHE_DIR / f"{BRIDGE_BINARY.name}.{bridge_source_hash()}.stamp"
    if BRIDGE_BINARY.is_file() and stamp.is_file() and BRIDGE_BINARY.stat().st_mtime >= BRIDGE_SOURCE.stat().st_mtime:
        return BRIDGE_BINARY

    tmp_file = tempfile.NamedTemporaryFile(prefix=f".{BRIDGE_BINARY.name}.", dir=CACHE_DIR, delete=False)
    tmp_binary = Path(tmp_file.name)
    tmp_file.close()
    try:
        result = subprocess.run(
            ["/usr/bin/swiftc", str(BRIDGE_SOURCE), "-o", str(tmp_binary)],
            cwd=SCRIPT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise AppleContactsWriteError(
                f"failed to compile Contacts bridge: {result.stderr.strip() or result.stdout.strip()}"
            )
        tmp_binary.replace(BRIDGE_BINARY)
        for old_stamp in CACHE_DIR.glob(f"{BRIDGE_BINARY.name}.*.stamp"):
            old_stamp.unlink(missing_ok=True)
        stamp.write_text("", encoding="utf-8")
        return BRIDGE_BINARY
    finally:
        tmp_binary.unlink(missing_ok=True)


def run_bridge(request: dict[str, Any]) -> dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    request_file = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=".json",
        prefix="request-",
        dir=CACHE_DIR,
        delete=False,
    )
    request_path = Path(request_file.name)
    try:
        with request_file:
            json.dump(request, request_file, ensure_ascii=True)
        result = subprocess.run(
            [str(ensure_bridge_binary()), str(request_path)],
            cwd=SCRIPT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        request_path.unlink(missing_ok=True)

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise AppleContactsWriteError(f"Contacts bridge failed: {detail}")
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AppleContactsWriteError(f"Contacts bridge returned non-JSON output: {result.stdout[:500]!r}") from exc
    if not isinstance(parsed, dict):
        raise AppleContactsWriteError("Contacts bridge returned a non-object JSON payload")
    return parsed


def load_contact_payload(path: str) -> dict[str, Any]:
    payload_path = Path(path).expanduser()
    try:
        data = json.loads(payload_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AppleContactsWriteError(f"unable to read payload {payload_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AppleContactsWriteError(f"invalid JSON payload {payload_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AppleContactsWriteError("contact payload must be a JSON object")
    if "contact" in data:
        siblings = sorted(set(data) - {"contact"})
        if siblings:
            raise AppleContactsWriteError(f"wrapped contact payload cannot include sibling field(s): {', '.join(siblings)}")
    contact = data.get("contact", data)
    if not isinstance(contact, dict):
        raise AppleContactsWriteError("contact payload must be an object or contain a contact object")
    validate_contact_payload(contact)
    return contact


def require_array(contact: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = contact.get(key)
    if not isinstance(value, list):
        raise AppleContactsWriteError(f"{key} must be an array")
    output: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise AppleContactsWriteError(f"{key}[{index}] must be an object")
        output.append(item)
    return output


def has_text(item: dict[str, Any], key: str) -> bool:
    return str(item.get(key, "")).strip() != ""


def require_one(item: dict[str, Any], keys: list[str], field: str, index: int) -> None:
    if not any(has_text(item, key) for key in keys):
        raise AppleContactsWriteError(f"{field}[{index}] requires one of: {', '.join(keys)}")


def validate_date_value(value: Any, field: str) -> None:
    if value is None:
        return
    if isinstance(value, str):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return
        raise AppleContactsWriteError(f"{field} must use YYYY-MM-DD or numeric year/month/day")
    if isinstance(value, dict):
        if not any(key in value for key in ("year", "month", "day")):
            raise AppleContactsWriteError(f"{field} requires year, month, or day")
        for key in ("year", "month", "day"):
            if key in value and value[key] is not None and not isinstance(value[key], int):
                raise AppleContactsWriteError(f"{field}.{key} must be a number")
        return
    raise AppleContactsWriteError(f"{field} must use YYYY-MM-DD or numeric year/month/day")


def validate_contact_payload(contact: dict[str, Any]) -> None:
    unknown = sorted(set(contact) - CONTACT_FIELDS)
    if unknown:
        raise AppleContactsWriteError(f"unknown contact field(s): {', '.join(unknown)}")
    if "addresses" in contact and "postal_addresses" in contact:
        raise AppleContactsWriteError("use addresses or postal_addresses, not both")
    if "birthday" in contact:
        validate_date_value(contact["birthday"], "birthday")

    for key in CONTACT_ARRAY_FIELDS:
        if key not in contact:
            continue
        items = require_array(contact, key)
        for index, item in enumerate(items):
            if key in {"phones", "emails"}:
                require_one(item, ["value"], key, index)
            elif key in {"addresses", "postal_addresses"}:
                require_one(
                    item,
                    ["street", "city", "state", "postal_code", "country", "iso_country_code", "sub_locality"],
                    key,
                    index,
                )
            elif key == "urls":
                require_one(item, ["value", "url"], key, index)
            elif key == "social_profiles":
                require_one(item, ["username", "url", "user_identifier"], key, index)
            elif key == "instant_messages":
                require_one(item, ["username", "value"], key, index)
            elif key == "relations":
                require_one(item, ["name"], key, index)
            elif key == "dates":
                require_one(item, ["date", "value", "year", "month", "day"], key, index)
                validate_date_value(item.get("date", item.get("value", item)), f"{key}[{index}]")


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


def summarize_values(items: Any, keys: tuple[str, ...] = ("value", "username", "name")) -> str:
    if not isinstance(items, list):
        return str(items)
    values: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in keys:
            if item.get(key):
                values.append(str(item[key]))
                break
    return ";".join(values) if values else "-"


def summarize_birthday(contact: dict[str, Any]) -> str:
    birthday = contact.get("birthday")
    if not isinstance(birthday, dict) or not birthday:
        return "-"
    return "-".join(str(birthday.get(key, "")) for key in ("year", "month", "day")).strip("-") or "-"


def summarize_note(contact: dict[str, Any]) -> str:
    if "note" in contact:
        note = str(contact.get("note") or "").replace("\r", " ").replace("\n", " ").strip()
        return note if note else "-"
    if contact.get("note_available") is False:
        return "unavailable"
    return "-"


def print_response(payload: dict[str, Any]) -> None:
    operation = payload.get("operation", "-")
    dry_run = "true" if payload.get("dry_run") else "false"
    parts = [f"operation={operation}", f"dry_run={dry_run}", f"status={payload.get('status', '-')}"]
    if payload.get("authorization_state"):
        parts.append(f"authorization_state={payload['authorization_state']}")
    if payload.get("contact_id"):
        parts.append(f"contact_id={payload['contact_id']}")
    if payload.get("group_id"):
        parts.append(f"group_id={payload['group_id']}")
    before = payload.get("before")
    after = payload.get("after")
    contact = payload.get("contact") if isinstance(payload.get("contact"), dict) else after
    if isinstance(before, dict) and isinstance(after, dict):
        parts.append(f"before_name={before.get('display_name') or '-'}")
        parts.append(f"after_name={after.get('display_name') or '-'}")
        parts.append(f"before_phones={summarize_values(before.get('phones'))}")
        parts.append(f"after_phones={summarize_values(after.get('phones'))}")
        parts.append(f"before_emails={summarize_values(before.get('emails'))}")
        parts.append(f"after_emails={summarize_values(after.get('emails'))}")
        parts.append(f"before_birthday={summarize_birthday(before)}")
        parts.append(f"after_birthday={summarize_birthday(after)}")
        parts.append(f"before_note={summarize_note(before)}")
        parts.append(f"after_note={summarize_note(after)}")
    elif isinstance(contact, dict):
        parts.append(f"name={contact.get('display_name') or contact.get('name') or '-'}")
        if "phones" in contact:
            parts.append(f"phones={summarize_values(contact.get('phones'))}")
        if "emails" in contact:
            parts.append(f"emails={summarize_values(contact.get('emails'))}")
        if "birthday" in contact:
            parts.append(f"birthday={summarize_birthday(contact)}")
        if "note" in contact or contact.get("note_available") is False:
            parts.append(f"note={summarize_note(contact)}")
    group = payload.get("group")
    if isinstance(group, dict):
        parts.append(f"group={group.get('name') or '-'}")
    print(" | ".join(parts))


def print_groups(payload: dict[str, Any]) -> None:
    print("group_id\tname\tcontainer_id\tcontainer_name")
    for group in payload.get("groups", []):
        print(
            "\t".join(
                [
                    str(group.get("id", "")),
                    str(group.get("name", "")),
                    str(group.get("container_id", "")),
                    str(group.get("container_name", "")),
                ]
            )
        )


def print_members(payload: dict[str, Any]) -> None:
    print("contact_id\tdisplay_name\tphones\temails")
    for contact in payload.get("members", []):
        print(
            "\t".join(
                [
                    str(contact.get("id", "")),
                    str(contact.get("display_name", "")),
                    summarize_values(contact.get("phones")),
                    summarize_values(contact.get("emails")),
                ]
            )
        )


def finish(args: argparse.Namespace, payload: dict[str, Any], groups: bool = False) -> int:
    if args.json:
        print_json(payload)
    elif groups:
        print_groups(payload)
    elif getattr(args, "members", False):
        print_members(payload)
    else:
        print_response(payload)
    return 0


def command_status(args: argparse.Namespace) -> int:
    payload = run_bridge({"operation": "status"})
    raw_status = payload.get("authorization_status")
    authorization_state = AUTHORIZATION_STATES.get(raw_status, f"unknown({raw_status})")
    authorized = authorization_state == "authorized"
    payload["authorization_state"] = authorization_state
    payload["status"] = "ok" if authorized else "not_authorized"
    finish(args, payload)
    return 0 if authorized else 1


def command_create(args: argparse.Namespace) -> int:
    request = {
        "operation": "contact.create",
        "confirm": args.confirm,
        "container_id": args.container_id,
        "contact": load_contact_payload(args.payload),
    }
    return finish(args, run_bridge(request))


def command_update(args: argparse.Namespace) -> int:
    request = {
        "operation": "contact.update",
        "confirm": args.confirm,
        "id": args.id,
        "contact": load_contact_payload(args.payload),
    }
    return finish(args, run_bridge(request))


def command_delete(args: argparse.Namespace) -> int:
    request = {
        "operation": "contact.delete",
        "confirm": args.confirm,
        "id": args.id,
    }
    return finish(args, run_bridge(request))


def command_groups_list(args: argparse.Namespace) -> int:
    payload = run_bridge({"operation": "groups.list"})
    return finish(args, payload, groups=True)


def command_groups_members(args: argparse.Namespace) -> int:
    payload = run_bridge({"operation": "group.members", "id": args.id})
    return finish(args, payload)


def command_groups_create(args: argparse.Namespace) -> int:
    request = {
        "operation": "group.create",
        "confirm": args.confirm,
        "name": args.name,
        "container_id": args.container_id,
    }
    return finish(args, run_bridge(request))


def command_groups_update(args: argparse.Namespace) -> int:
    request = {
        "operation": "group.update",
        "confirm": args.confirm,
        "id": args.id,
        "name": args.name,
    }
    return finish(args, run_bridge(request))


def command_groups_delete(args: argparse.Namespace) -> int:
    request = {
        "operation": "group.delete",
        "confirm": args.confirm,
        "id": args.id,
    }
    return finish(args, run_bridge(request))


def command_group_member(args: argparse.Namespace) -> int:
    operation = "group.addContact" if args.member_action == "add-contact" else "group.removeContact"
    request = {
        "operation": operation,
        "confirm": args.confirm,
        "contact_id": args.contact_id,
        "group_id": args.group_id,
    }
    return finish(args, run_bridge(request))


def add_common_write_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--confirm", action="store_true", help="Actually save the Contacts.framework change.")
    parser.add_argument("--json", action="store_true", help="Print structured JSON output.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mutate Apple Contacts safely through Contacts.framework.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Show Contacts authorization status.")
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=command_status)

    create = subparsers.add_parser("create", help="Create a contact. Dry-run unless --confirm is passed.")
    create.add_argument("--payload", required=True, help="Contact JSON payload file.")
    create.add_argument("--container-id", help="Exact Contacts.framework container ID.")
    add_common_write_args(create)
    create.set_defaults(handler=command_create)

    update = subparsers.add_parser("update", help="Update a contact by exact Contacts.framework ID.")
    update.add_argument("--id", required=True, help="Exact apple_contact_id from the read tool.")
    update.add_argument("--payload", required=True, help="Patch JSON payload file.")
    add_common_write_args(update)
    update.set_defaults(handler=command_update)

    delete = subparsers.add_parser("delete", help="Delete a contact by exact Contacts.framework ID.")
    delete.add_argument("--id", required=True, help="Exact apple_contact_id from the read tool.")
    add_common_write_args(delete)
    delete.set_defaults(handler=command_delete)

    groups = subparsers.add_parser("groups", help="Manage contact groups.")
    group_subparsers = groups.add_subparsers(dest="group_command", required=True)

    groups_list = group_subparsers.add_parser("list", help="List contact groups.")
    groups_list.add_argument("--json", action="store_true")
    groups_list.set_defaults(handler=command_groups_list)

    groups_members = group_subparsers.add_parser("members", help="List group members by exact group ID.")
    groups_members.add_argument("--id", required=True)
    groups_members.add_argument("--json", action="store_true")
    groups_members.set_defaults(handler=command_groups_members, members=True)

    groups_create = group_subparsers.add_parser("create", help="Create a group. Dry-run unless --confirm is passed.")
    groups_create.add_argument("--name", required=True)
    groups_create.add_argument("--container-id", help="Exact Contacts.framework container ID.")
    add_common_write_args(groups_create)
    groups_create.set_defaults(handler=command_groups_create)

    groups_update = group_subparsers.add_parser("update", help="Rename a group. Dry-run unless --confirm is passed.")
    groups_update.add_argument("--id", required=True)
    groups_update.add_argument("--name", required=True)
    add_common_write_args(groups_update)
    groups_update.set_defaults(handler=command_groups_update)

    groups_delete = group_subparsers.add_parser("delete", help="Delete a group. Dry-run unless --confirm is passed.")
    groups_delete.add_argument("--id", required=True)
    add_common_write_args(groups_delete)
    groups_delete.set_defaults(handler=command_groups_delete)

    add_contact = group_subparsers.add_parser("add-contact", help="Add a contact to a group.")
    add_contact.add_argument("--contact-id", required=True)
    add_contact.add_argument("--group-id", required=True)
    add_common_write_args(add_contact)
    add_contact.set_defaults(handler=command_group_member, member_action="add-contact")

    remove_contact = group_subparsers.add_parser("remove-contact", help="Remove a contact from a group.")
    remove_contact.add_argument("--contact-id", required=True)
    remove_contact.add_argument("--group-id", required=True)
    add_common_write_args(remove_contact)
    remove_contact.set_defaults(handler=command_group_member, member_action="remove-contact")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except AppleContactsWriteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
