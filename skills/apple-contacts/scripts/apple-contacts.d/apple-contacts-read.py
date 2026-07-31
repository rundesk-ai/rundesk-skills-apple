#!/usr/bin/env python3
"""
Read local Apple Contacts / AddressBook data directly from SQLite.

Usage:
  apple-contacts read sources [--json]
  apple-contacts read list [--limit 200] [--json]
  apple-contacts read search "Alex Example" [--limit 20] [--json]
  apple-contacts read show --id CONTACT_ID [--json]
  apple-contacts read export --json [--include-blobs]
  apple-contacts read schema [--json]

Inputs:
  Opens ~/Library/Application Support/AddressBook/**/AddressBook-v*.abcddb
  in SQLite read-only mode. It does not use Contacts.app, Messages.app, or
  iCloud credentials.

Outputs:
  Default output is compact text/TSV for agent use. Use --json for structured
  full output. Blob/image payloads are summarized by default and are only
  base64 encoded when --include-blobs is passed.

Write/mutation behavior:
  Read-only. This script never writes to the AddressBook databases.
"""

from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ADDRESSBOOK_ROOT = Path.home() / "Library" / "Application Support" / "AddressBook"
APPLE_EPOCH_OFFSET = 978_307_200
SCHEMA_VERSION = 1


class AppleContactsReadError(RuntimeError):
    pass


def clean(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    output = str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ").strip()
    return output if output else fallback


def source_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    return str(value)


def tsv(value: Any) -> str:
    if isinstance(value, list):
        return ";".join(clean(item) for item in value if clean(item))
    return clean(value)


def boolish(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes"}


def apple_timestamp(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        seconds = float(value) + APPLE_EPOCH_OFFSET
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=0).isoformat()


def generated_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def discover_dbs(root: Path) -> list[Path]:
    root = root.expanduser()
    candidates = [
        *sorted((root / "Sources").glob("*/AddressBook-v*.abcddb")),
        *sorted(root.glob("AddressBook-v*.abcddb")),
    ]
    seen: set[Path] = set()
    output: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if path.is_file() and resolved not in seen:
            seen.add(resolved)
            output.append(path)
    return output


def source_id_for(path: Path, root: Path) -> str:
    try:
        relative = path.expanduser().resolve().relative_to(root.expanduser().resolve())
    except ValueError:
        return path.parent.name or "unknown"
    parts = relative.parts
    if len(parts) >= 3 and parts[0] == "Sources":
        return parts[1]
    return "root"


def connect_readonly(path: Path) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(f"file:{path.expanduser()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise AppleContactsReadError(f"Unable to open AddressBook database read-only: {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    try:
        return conn.execute(query, params).fetchall()
    except sqlite3.Error as exc:
        raise AppleContactsReadError(str(exc)) from exc


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in rows(conn, "select name from sqlite_master where type='table'")}


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in rows(conn, f"pragma table_info({table})")}


def all_table_columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in rows(conn, f"pragma table_info({table})")]


def select_existing(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    if table not in table_names(conn):
        return []
    return rows(conn, f"select * from {table}")


def owner_expression(columns: set[str]) -> str | None:
    candidates = [col for col in ("ZOWNER", "Z22_OWNER", "Z17_OWNER", "ZCONTACT", "Z22_CONTACT") if col in columns]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return "coalesce(" + ", ".join(candidates) + ")"


def blob_value(value: Any, include_blobs: bool) -> dict[str, Any]:
    if value is None:
        return {"available": False, "bytes": 0}
    data = bytes(value)
    output: dict[str, Any] = {"available": bool(data), "bytes": len(data)}
    if include_blobs and data:
        output["base64"] = base64.b64encode(data).decode("ascii")
    return output


def normalize_row(row: sqlite3.Row, include_blobs: bool = False) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if isinstance(value, bytes):
            output[key] = blob_value(value, include_blobs)
        else:
            output[key] = value
    return output


def child_rows(
    conn: sqlite3.Connection,
    table: str,
    source_id: str,
    field_map: dict[str, str],
    include_blobs: bool,
) -> dict[int, list[dict[str, Any]]]:
    tables = table_names(conn)
    if table not in tables:
        return {}
    columns = table_columns(conn, table)
    owner = owner_expression(columns)
    if not owner:
        return {}

    select_parts = [f"{owner} as owner"]
    for alias, column in field_map.items():
        if column in columns:
            select_parts.append(f"{column} as {alias}")
    if "Z_PK" in columns:
        select_parts.append("Z_PK as db_pk")

    order = "owner"
    if "ZORDERINGINDEX" in columns:
        order += ", ZORDERINGINDEX"

    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows(conn, f"select {', '.join(select_parts)} from {table} where {owner} is not null order by {order}"):
        owner_pk = row["owner"]
        if owner_pk is None:
            continue
        item: dict[str, Any] = {"db_ref": f"{source_id}:{row['db_pk']}"} if "db_pk" in row.keys() else {}
        for key in row.keys():
            if key in {"owner", "db_pk"}:
                continue
            value = row[key]
            if isinstance(value, bytes):
                item[key] = blob_value(value, include_blobs)
            elif key.startswith("is_"):
                item[key] = boolish(value)
            elif key.endswith("_at"):
                item[key] = apple_timestamp(value)
            else:
                item[key] = source_text(value) if isinstance(value, str) else value
        result[int(owner_pk)].append(item)
    return result


def entity_map(conn: sqlite3.Connection) -> dict[int, str]:
    if "Z_PRIMARYKEY" not in table_names(conn):
        return {}
    output: dict[int, str] = {}
    columns = table_columns(conn, "Z_PRIMARYKEY")
    if not {"Z_ENT", "Z_NAME"}.issubset(columns):
        return output
    for row in rows(conn, "select Z_ENT, Z_NAME from Z_PRIMARYKEY"):
        output[int(row["Z_ENT"])] = clean(row["Z_NAME"])
    return output


def entity_name(row: sqlite3.Row, entities: dict[int, str]) -> str:
    return entities.get(int(row["Z_ENT"])) if row["Z_ENT"] is not None else ""


def is_contact_record(row: sqlite3.Row, entities: dict[int, str]) -> bool:
    name = entity_name(row, entities)
    if name:
        return "Contact" in name and "Group" not in name
    values = [clean(row[col]) for col in ("ZFIRSTNAME", "ZLASTNAME", "ZNAME", "ZORGANIZATION") if col in row.keys()]
    return any(values)


def is_group_record(row: sqlite3.Row, entities: dict[int, str]) -> bool:
    return "Group" in entity_name(row, entities)


def display_name(record: dict[str, Any]) -> str:
    explicit = clean(record.get("name"))
    if explicit:
        return explicit
    parts = [
        clean(record.get("name_prefix")),
        clean(record.get("given_name")),
        clean(record.get("middle_name")),
        clean(record.get("family_name")),
        clean(record.get("name_suffix")),
    ]
    joined = " ".join(part for part in parts if part).strip()
    return joined or clean(record.get("organization_name"))


def record_base(row: sqlite3.Row, source_id: str, include_blobs: bool) -> dict[str, Any]:
    raw = normalize_row(row, include_blobs)
    fields = {
        "db_ref": f"{source_id}:{row['Z_PK']}",
        "source_id": source_id,
        "db_pk": int(row["Z_PK"]),
        "entity": clean(raw.get("entity")),
        "apple_contact_id": source_text(row["ZUNIQUEID"]) if "ZUNIQUEID" in row.keys() else "",
        "unique_id": source_text(row["ZUNIQUEID"]) if "ZUNIQUEID" in row.keys() else "",
        "name": source_text(row["ZNAME"]) if "ZNAME" in row.keys() else "",
        "name_prefix": source_text(row["ZTITLE"]) if "ZTITLE" in row.keys() else "",
        "given_name": source_text(row["ZFIRSTNAME"]) if "ZFIRSTNAME" in row.keys() else "",
        "middle_name": source_text(row["ZMIDDLENAME"]) if "ZMIDDLENAME" in row.keys() else "",
        "family_name": source_text(row["ZLASTNAME"]) if "ZLASTNAME" in row.keys() else "",
        "previous_family_name": source_text(row["ZMAIDENNAME"]) if "ZMAIDENNAME" in row.keys() else "",
        "name_suffix": source_text(row["ZSUFFIX"]) if "ZSUFFIX" in row.keys() else "",
        "nickname": source_text(row["ZNICKNAME"]) if "ZNICKNAME" in row.keys() else "",
        "organization_name": source_text(row["ZORGANIZATION"]) if "ZORGANIZATION" in row.keys() else "",
        "department_name": source_text(row["ZDEPARTMENT"]) if "ZDEPARTMENT" in row.keys() else "",
        "job_title": source_text(row["ZJOBTITLE"]) if "ZJOBTITLE" in row.keys() else "",
        "phonetic_given_name": source_text(row["ZPHONETICFIRSTNAME"]) if "ZPHONETICFIRSTNAME" in row.keys() else "",
        "phonetic_middle_name": source_text(row["ZPHONETICMIDDLENAME"]) if "ZPHONETICMIDDLENAME" in row.keys() else "",
        "phonetic_family_name": source_text(row["ZPHONETICLASTNAME"]) if "ZPHONETICLASTNAME" in row.keys() else "",
        "phonetic_organization_name": source_text(row["ZPHONETICORGANIZATION"]) if "ZPHONETICORGANIZATION" in row.keys() else "",
        "created_at": apple_timestamp(row["ZCREATIONDATE"]) if "ZCREATIONDATE" in row.keys() else "",
        "modified_at": apple_timestamp(row["ZMODIFICATIONDATE"]) if "ZMODIFICATIONDATE" in row.keys() else "",
        "last_sync_at": apple_timestamp(row["ZLASTSYNCDATE"]) if "ZLASTSYNCDATE" in row.keys() else "",
        "birthday": {
            "raw": row["ZBIRTHDAY"] if "ZBIRTHDAY" in row.keys() else None,
            "year": row["ZBIRTHDAYYEAR"] if "ZBIRTHDAYYEAR" in row.keys() else None,
            "date_utc": apple_timestamp(row["ZBIRTHDAY"]) if "ZBIRTHDAY" in row.keys() else "",
        },
        "raw_record": raw,
    }
    return fields


def record_images(row: sqlite3.Row, include_blobs: bool) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, column in {
        "image": "ZIMAGEDATA",
        "thumbnail": "ZTHUMBNAILIMAGEDATA",
        "memoji_metadata": "ZMEMOJIMETADATA",
        "wallpaper": "ZWALLPAPER",
        "external_representation": "ZEXTERNALREPRESENTATION",
    }.items():
        if column in row.keys():
            output[key] = blob_value(row[column], include_blobs)
    for key, column in {
        "image_reference": "ZIMAGEREFERENCE",
        "image_type": "ZIMAGETYPE",
        "external_image_uri": "ZEXTERNALIMAGEURI",
        "wallpaper_uri": "ZWALLPAPERURI",
    }.items():
        if column in row.keys():
            output[key] = clean(row[column])
    return output


def service_map(conn: sqlite3.Connection) -> dict[int, str]:
    if "ZABCDSERVICE" not in table_names(conn):
        return {}
    columns = table_columns(conn, "ZABCDSERVICE")
    if not {"Z_PK", "ZSERVICENAME"}.issubset(columns):
        return {}
    return {int(row["Z_PK"]): source_text(row["ZSERVICENAME"]) for row in rows(conn, "select Z_PK, ZSERVICENAME from ZABCDSERVICE")}


def group_memberships(conn: sqlite3.Connection, source_id: str, groups_by_pk: dict[int, dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    tables = table_names(conn)
    if "Z_22PARENTGROUPS" not in tables:
        return {}
    columns = table_columns(conn, "Z_22PARENTGROUPS")
    contact_col = next((col for col in columns if "CONTACT" in col), None)
    group_col = next((col for col in columns if "PARENTGROUP" in col or "GROUP" in col and col != contact_col), None)
    if not contact_col or not group_col:
        return {}

    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows(conn, f"select {contact_col} as contact_pk, {group_col} as group_pk from Z_22PARENTGROUPS"):
        group = groups_by_pk.get(int(row["group_pk"]))
        if group:
            result[int(row["contact_pk"])].append(
                {
                    "db_ref": group["db_ref"],
                    "_group_pk": int(row["group_pk"]),
                    "apple_group_id": group["apple_group_id"],
                    "name": group["name"],
                    "source_id": source_id,
                }
            )
    return result


def read_source(path: Path, root: Path, include_blobs: bool) -> dict[str, Any]:
    source_id = source_id_for(path, root)
    with connect_readonly(path) as conn:
        tables = table_names(conn)
        entities = entity_map(conn)
        record_rows = select_existing(conn, "ZABCDRECORD")

        groups_by_pk: dict[int, dict[str, Any]] = {}
        for row in record_rows:
            if not is_group_record(row, entities):
                continue
            group = record_base(row, source_id, include_blobs)
            group["entity"] = entity_name(row, entities)
            group["apple_group_id"] = group.pop("apple_contact_id")
            group["name"] = source_text(row["ZNAME"]) if "ZNAME" in row.keys() else display_name(group)
            group["contacts"] = []
            groups_by_pk[int(row["Z_PK"])] = group

        services = service_map(conn)
        memberships = group_memberships(conn, source_id, groups_by_pk)

        phones = child_rows(
            conn,
            "ZABCDPHONENUMBER",
            source_id,
            {
                "label": "ZLABEL",
                "value": "ZFULLNUMBER",
                "country_code": "ZCOUNTRYCODE",
                "area_code": "ZAREACODE",
                "local_number": "ZLOCALNUMBER",
                "extension": "ZEXTENSION",
                "last_four_digits": "ZLASTFOURDIGITS",
                "unique_id": "ZUNIQUEID",
                "is_primary": "ZISPRIMARY",
                "is_private": "ZISPRIVATE",
                "ordering_index": "ZORDERINGINDEX",
            },
            include_blobs,
        )
        emails = child_rows(
            conn,
            "ZABCDEMAILADDRESS",
            source_id,
            {
                "label": "ZLABEL",
                "value": "ZADDRESS",
                "normalized": "ZADDRESSNORMALIZED",
                "unique_id": "ZUNIQUEID",
                "is_primary": "ZISPRIMARY",
                "is_private": "ZISPRIVATE",
                "ordering_index": "ZORDERINGINDEX",
            },
            include_blobs,
        )
        addresses = child_rows(
            conn,
            "ZABCDPOSTALADDRESS",
            source_id,
            {
                "label": "ZLABEL",
                "street": "ZSTREET",
                "city": "ZCITY",
                "state": "ZSTATE",
                "region": "ZREGION",
                "postal_code": "ZZIPCODE",
                "country": "ZCOUNTRYNAME",
                "iso_country_code": "ZCOUNTRYCODE",
                "sub_locality": "ZSUBLOCALITY",
                "unique_id": "ZUNIQUEID",
                "is_primary": "ZISPRIMARY",
                "is_private": "ZISPRIVATE",
                "ordering_index": "ZORDERINGINDEX",
            },
            include_blobs,
        )
        urls = child_rows(
            conn,
            "ZABCDURLADDRESS",
            source_id,
            {
                "label": "ZLABEL",
                "value": "ZURL",
                "unique_id": "ZUNIQUEID",
                "is_primary": "ZISPRIMARY",
                "is_private": "ZISPRIVATE",
                "ordering_index": "ZORDERINGINDEX",
            },
            include_blobs,
        )
        socials = child_rows(
            conn,
            "ZABCDSOCIALPROFILE",
            source_id,
            {
                "label": "ZLABEL",
                "service": "ZSERVICENAME",
                "username": "ZUSERNAME",
                "user_identifier": "ZUSERIDENTIFIER",
                "url": "ZURLSTRING",
                "display_name": "ZDISPLAYNAME",
                "bundle_identifiers": "ZBUNDLEIDENTIFIERSSTRING",
                "team_identifier": "ZTEAMIDENTIFIER",
                "unique_id": "ZUNIQUEID",
                "is_primary": "ZISPRIMARY",
                "is_private": "ZISPRIVATE",
                "ordering_index": "ZORDERINGINDEX",
            },
            include_blobs,
        )

        messages = child_rows(
            conn,
            "ZABCDMESSAGINGADDRESS",
            source_id,
            {
                "label": "ZLABEL",
                "value": "ZADDRESS",
                "service_id": "ZSERVICE",
                "user_identifier": "ZUSERIDENTIFIER",
                "bundle_identifiers": "ZBUNDLEIDENTIFIERSSTRING",
                "team_identifier": "ZTEAMIDENTIFIER",
                "unique_id": "ZUNIQUEID",
                "is_primary": "ZISPRIMARY",
                "is_private": "ZISPRIVATE",
                "ordering_index": "ZORDERINGINDEX",
            },
            include_blobs,
        )
        for items in messages.values():
            for item in items:
                if item.get("service_id") is not None:
                    item["service"] = services.get(int(item["service_id"]), "")
        relations = child_rows(
            conn,
            "ZABCDRELATEDNAME",
            source_id,
            {
                "label": "ZLABEL",
                "name": "ZNAME",
                "unique_id": "ZUNIQUEID",
                "is_primary": "ZISPRIMARY",
                "is_private": "ZISPRIVATE",
                "ordering_index": "ZORDERINGINDEX",
            },
            include_blobs,
        )
        dates = child_rows(
            conn,
            "ZABCDCONTACTDATE",
            source_id,
            {
                "label": "ZLABEL",
                "date_at": "ZDATE",
                "date_year": "ZDATEYEAR",
                "date_yearless": "ZDATEYEARLESS",
                "unique_id": "ZUNIQUEID",
                "is_primary": "ZISPRIMARY",
                "is_private": "ZISPRIVATE",
                "ordering_index": "ZORDERINGINDEX",
            },
            include_blobs,
        )
        notes = child_rows(
            conn,
            "ZABCDNOTE",
            source_id,
            {
                "text": "ZTEXT",
                "rich_text": "ZRICHTEXTDATA",
            },
            include_blobs,
        )
        addressing_grammar = child_rows(
            conn,
            "ZABCDADDRESSINGGRAMMAR",
            source_id,
            {
                "label": "ZLABEL",
                "value": "ZADDRESSINGGRAMMAR",
                "unique_id": "ZUNIQUEID",
                "is_primary": "ZISPRIMARY",
                "is_private": "ZISPRIVATE",
                "ordering_index": "ZORDERINGINDEX",
            },
            include_blobs,
        )
        calendar_uris = child_rows(
            conn,
            "ZABCDCALENDARURI",
            source_id,
            {
                "label": "ZLABEL",
                "url": "ZURL",
                "unique_id": "ZUNIQUEID",
                "is_primary": "ZISPRIMARY",
                "is_private": "ZISPRIVATE",
                "ordering_index": "ZORDERINGINDEX",
            },
            include_blobs,
        )
        alert_tones = child_rows(
            conn,
            "ZABCDALERTTONE",
            source_id,
            {
                "type": "ZTYPE",
                "tone_data": "ZTONEDATA",
                "unique_id": "ZUNIQUEID",
            },
            include_blobs,
        )
        likenesses = child_rows(
            conn,
            "ZABCDLIKENESS",
            source_id,
            {
                "label": "ZLABEL",
                "kind": "ZKIND",
                "version": "ZVERSION",
                "data": "ZDATA",
                "unique_id": "ZUNIQUEID",
                "is_primary": "ZISPRIMARY",
                "is_private": "ZISPRIVATE",
                "ordering_index": "ZORDERINGINDEX",
            },
            include_blobs,
        )
        unknown_properties = child_rows(
            conn,
            "ZABCDUNKNOWNPROPERTY",
            source_id,
            {
                "property_name": "ZPROPERTYNAME",
                "original_line": "ZORIGINALLINE",
            },
            include_blobs,
        )

        contacts: list[dict[str, Any]] = []
        for row in record_rows:
            if not is_contact_record(row, entities):
                continue
            pk = int(row["Z_PK"])
            contact = record_base(row, source_id, include_blobs)
            contact["entity"] = entity_name(row, entities)
            contact["display_name"] = display_name(contact)
            contact["phones"] = phones.get(pk, [])
            contact["emails"] = emails.get(pk, [])
            contact["postal_addresses"] = addresses.get(pk, [])
            contact["urls"] = urls.get(pk, [])
            contact["social_profiles"] = socials.get(pk, [])
            contact["instant_messages"] = messages.get(pk, [])
            contact["relations"] = relations.get(pk, [])
            contact["dates"] = dates.get(pk, [])
            contact["notes"] = notes.get(pk, [])
            contact["note"] = "\n\n".join(item.get("text", "") for item in contact["notes"] if item.get("text"))
            contact["addressing_grammar"] = addressing_grammar.get(pk, [])
            contact["calendar_uris"] = calendar_uris.get(pk, [])
            contact["alert_tones"] = alert_tones.get(pk, [])
            contact["likenesses"] = likenesses.get(pk, [])
            contact["unknown_properties"] = unknown_properties.get(pk, [])
            contact["groups"] = memberships.get(pk, [])
            contact["images"] = record_images(row, include_blobs)
            contacts.append(contact)

            for group in contact["groups"]:
                group_pk = int(group["_group_pk"])
                if group_pk in groups_by_pk:
                    groups_by_pk[group_pk]["contacts"].append(
                        {
                            "db_ref": contact["db_ref"],
                            "apple_contact_id": contact["apple_contact_id"],
                            "display_name": contact["display_name"],
                        }
                    )
                group.pop("_group_pk", None)

        table_counts = {
            table: rows(conn, f"select count(*) as count from {table}")[0]["count"]
            for table in sorted(tables)
        }
        latest_modified = max((contact.get("modified_at") or "" for contact in contacts), default="")
        return {
            "source_id": source_id,
            "db_path": str(path.expanduser()),
            "tables": table_counts,
            "counts": {
                "records": len(record_rows),
                "contacts": len(contacts),
                "groups": len(groups_by_pk),
                "phones": sum(len(contact["phones"]) for contact in contacts),
                "emails": sum(len(contact["emails"]) for contact in contacts),
                "notes": sum(len(contact["notes"]) for contact in contacts),
            },
            "latest_modified_at": latest_modified,
            "contacts": contacts,
            "groups": list(groups_by_pk.values()),
            "schema": {
                table: all_table_columns(conn, table)
                for table in sorted(tables)
            },
        }


def read_addressbook(root: Path, include_blobs: bool = False) -> dict[str, Any]:
    dbs = discover_dbs(root)
    if not dbs:
        raise AppleContactsReadError(f"No AddressBook databases found under {root.expanduser()}")

    sources = [read_source(db, root, include_blobs) for db in dbs]
    contacts = [contact for source in sources for contact in source["contacts"]]
    groups = [group for source in sources for group in source["groups"]]
    contacts.sort(key=lambda item: ((item.get("display_name") or item.get("organization_name") or "").lower(), item["db_ref"]))
    groups.sort(key=lambda item: ((item.get("name") or "").lower(), item["db_ref"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "generated_at": generated_at(),
        "source": {
            "type": "AddressBook SQLite",
            "root": str(root.expanduser()),
            "include_blobs": include_blobs,
        },
        "counts": {
            "sources": len(sources),
            "contacts": len(contacts),
            "groups": len(groups),
            "phones": sum(len(contact["phones"]) for contact in contacts),
            "emails": sum(len(contact["emails"]) for contact in contacts),
            "notes": sum(len(contact["notes"]) for contact in contacts),
        },
        "sources": [
            {
                "source_id": source["source_id"],
                "db_path": source["db_path"],
                "counts": source["counts"],
                "latest_modified_at": source["latest_modified_at"],
            }
            for source in sources
        ],
        "contacts": contacts,
        "groups": groups,
        "schema": {
            source["source_id"]: {
                "db_path": source["db_path"],
                "tables": source["schema"],
                "table_counts": source["tables"],
            }
            for source in sources
        },
    }


def find_contact(payload: dict[str, Any], contact_id: str) -> dict[str, Any] | None:
    wanted = clean(contact_id)
    for contact in payload["contacts"]:
        if wanted in {contact["db_ref"], contact.get("apple_contact_id"), contact.get("unique_id")}:
            return contact
    return None


def contact_search_text(contact: dict[str, Any]) -> str:
    values: list[str] = [
        contact.get("db_ref", ""),
        contact.get("apple_contact_id", ""),
        contact.get("display_name", ""),
        contact.get("organization_name", ""),
        contact.get("department_name", ""),
        contact.get("job_title", ""),
        contact.get("nickname", ""),
        contact.get("note", ""),
    ]
    for key in ("phones", "emails", "postal_addresses", "urls", "social_profiles", "instant_messages", "relations", "dates", "groups"):
        for item in contact.get(key) or []:
            values.extend(clean(value) for value in item.values() if not isinstance(value, (list, dict)))
    return " ".join(values).lower()


def search_contacts(payload: dict[str, Any], query: str, limit: int) -> list[dict[str, Any]]:
    terms = [term.lower() for term in query.split() if term.strip()]
    if not terms:
        return []
    matches = [
        contact
        for contact in payload["contacts"]
        if all(term in contact_search_text(contact) for term in terms)
    ]
    return matches[:limit]


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def print_sources(payload: dict[str, Any]) -> None:
    print("source_id\tdb_path\tcontacts\tgroups\tphones\temails\tnotes\tlatest_modified_at")
    for source in payload["sources"]:
        counts = source["counts"]
        print(
            "\t".join(
                [
                    tsv(source["source_id"]),
                    tsv(source["db_path"]),
                    str(counts["contacts"]),
                    str(counts["groups"]),
                    str(counts["phones"]),
                    str(counts["emails"]),
                    str(counts["notes"]),
                    tsv(source["latest_modified_at"]),
                ]
            )
        )


def print_contact_rows(contacts: list[dict[str, Any]]) -> None:
    print("db_ref\tapple_contact_id\tdisplay_name\torganization\tphones\temails\tgroups\tsource_id\tmodified_at")
    for contact in contacts:
        phones = [item.get("value", "") for item in contact.get("phones", [])]
        emails = [item.get("value", "") for item in contact.get("emails", [])]
        groups = [item.get("name", "") for item in contact.get("groups", [])]
        print(
            "\t".join(
                [
                    tsv(contact.get("db_ref")),
                    tsv(contact.get("apple_contact_id")),
                    tsv(contact.get("display_name")),
                    tsv(contact.get("organization_name")),
                    tsv(phones),
                    tsv(emails),
                    tsv(groups),
                    tsv(contact.get("source_id")),
                    tsv(contact.get("modified_at")),
                ]
            )
        )


def has_text(value: Any) -> bool:
    return source_text(value).strip() != ""


def item_has_any_text(item: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return any(has_text(item.get(key)) for key in keys)


def first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = source_text(item.get(key)).strip()
        if value:
            return value
    return ""


# Every top-level key the contact normalizer emits must be classified below so
# the text detail view can never again silently drop a populated field. Keys in
# DETAIL_RENDERED_KEYS are shown by print_contact_detail; keys in
# DETAIL_HIDDEN_KEYS are intentionally excluded (internal ids, the raw mirror,
# name parts already folded into display_name, blob summaries shown elsewhere).
# test-apple-contacts.py asserts that the union of these two sets exactly covers
# the keys produced by a normalized contact, so any new field forces a decision.
DETAIL_RENDERED_KEYS = frozenset(
    {
        "display_name",
        "db_ref",
        "apple_contact_id",
        "source_id",
        "organization_name",
        "department_name",
        "job_title",
        "nickname",
        "previous_family_name",
        "phonetic_given_name",
        "phonetic_middle_name",
        "phonetic_family_name",
        "phonetic_organization_name",
        "birthday",
        "created_at",
        "modified_at",
        "last_sync_at",
        "phones",
        "emails",
        "urls",
        "instant_messages",
        "social_profiles",
        "relations",
        "dates",
        "postal_addresses",
        "groups",
        "note",
    }
)
DETAIL_HIDDEN_KEYS = frozenset(
    {
        "name",
        "name_prefix",
        "given_name",
        "middle_name",
        "family_name",
        "name_suffix",
        "unique_id",
        "db_pk",
        "entity",
        "raw_record",
        "images",
        "notes",
        "addressing_grammar",
        "calendar_uris",
        "alert_tones",
        "likenesses",
        "unknown_properties",
    }
)


def format_birthday(birthday: Any) -> str:
    if not isinstance(birthday, dict):
        return ""
    iso = source_text(birthday.get("date_utc")).strip()
    if not iso:
        return ""
    date_part = iso.split("T", 1)[0]
    # A year-less birthday (no ZBIRTHDAYYEAR) should not imply a real year.
    if not birthday.get("year") and len(date_part) >= 10:
        return date_part[5:]
    return date_part


def print_contact_detail(contact: dict[str, Any]) -> None:
    print(f"Contact: {contact.get('display_name') or '-'}")
    print(f"  db_ref: {contact.get('db_ref')}")
    print(f"  apple_contact_id: {contact.get('apple_contact_id') or '-'}")
    print(f"  source_id: {contact.get('source_id')}")
    for label, key in [
        ("organization", "organization_name"),
        ("department", "department_name"),
        ("job_title", "job_title"),
        ("nickname", "nickname"),
        ("previous_family_name", "previous_family_name"),
        ("phonetic_given_name", "phonetic_given_name"),
        ("phonetic_middle_name", "phonetic_middle_name"),
        ("phonetic_family_name", "phonetic_family_name"),
        ("phonetic_organization_name", "phonetic_organization_name"),
    ]:
        value = contact.get(key)
        if has_text(value):
            print(f"  {label}: {value}")
    birthday = format_birthday(contact.get("birthday"))
    if birthday:
        print(f"  birthday: {birthday}")
    for label, key in [
        ("created_at", "created_at"),
        ("modified_at", "modified_at"),
        ("last_sync_at", "last_sync_at"),
    ]:
        value = contact.get(key)
        if has_text(value):
            print(f"  {label}: {value}")
    for label, key, value_key, extra_keys in [
        ("Phones", "phones", "value", ()),
        ("Emails", "emails", "value", ()),
        ("URLs", "urls", "value", ("url",)),
        ("Instant messages", "instant_messages", "value", ("username",)),
        ("Social profiles", "social_profiles", "username", ("url", "user_identifier", "display_name")),
        ("Relations", "relations", "name", ()),
        ("Dates", "dates", "date_at", ("date", "value")),
    ]:
        value_keys = (value_key, *extra_keys)
        items = [
            item
            for item in contact.get(key) or []
            if item_has_any_text(item, (*value_keys, "service", "label"))
        ]
        if not items:
            continue
        print(f"  {label}:")
        for item in items:
            parts = [item.get("label") or "-", first_text(item, value_keys) or "-"]
            if item.get("service"):
                parts.append(item["service"])
            print("    " + " | ".join(tsv(part) for part in parts))
    addresses = [
        item
        for item in contact.get("postal_addresses") or []
        if item_has_any_text(item, ("street", "city", "state", "region", "postal_code", "country", "label"))
    ]
    if addresses:
        print("  Postal addresses:")
    for item in addresses:
        parts = [
            item.get("label") or "-",
            item.get("street") or "-",
            item.get("city") or "-",
            item.get("state") or item.get("region") or "-",
            item.get("postal_code") or "-",
            item.get("country") or "-",
        ]
        print("    " + " | ".join(tsv(part) for part in parts))
    groups = [item for item in contact.get("groups") or [] if item_has_any_text(item, ("name", "apple_group_id", "db_ref"))]
    if groups:
        print("  Groups:")
    for item in groups:
        print(f"    {item.get('name') or '-'} | {item.get('apple_group_id') or item.get('db_ref')}")
    note = source_text(contact.get("note")).strip()
    if note:
        print("  Notes:")
        for line in note.splitlines():
            print(f"    {line}")


def command_sources(args: argparse.Namespace) -> int:
    payload = read_addressbook(args.addressbook_root, args.include_blobs)
    print_json(payload["sources"]) if args.json else print_sources(payload)
    return 0


def command_list(args: argparse.Namespace) -> int:
    payload = read_addressbook(args.addressbook_root, args.include_blobs)
    contacts = payload["contacts"] if args.all else payload["contacts"][: args.limit]
    print_json(contacts) if args.json else print_contact_rows(contacts)
    return 0


def command_search(args: argparse.Namespace) -> int:
    payload = read_addressbook(args.addressbook_root, args.include_blobs)
    matches = search_contacts(payload, args.query, args.limit)
    print_json(matches) if args.json else print_contact_rows(matches)
    return 0


def command_show(args: argparse.Namespace) -> int:
    payload = read_addressbook(args.addressbook_root, args.include_blobs)
    contact = find_contact(payload, args.id)
    if not contact:
        raise AppleContactsReadError(f"contact not found: {args.id}")
    print_json(contact) if args.json else print_contact_detail(contact)
    return 0


def command_export(args: argparse.Namespace) -> int:
    payload = read_addressbook(args.addressbook_root, args.include_blobs)
    print_json(payload) if args.json else print_contact_rows(payload["contacts"])
    return 0


def command_schema(args: argparse.Namespace) -> int:
    payload = read_addressbook(args.addressbook_root, args.include_blobs)
    if args.json:
        print_json(payload["schema"])
        return 0
    print("source_id\ttable\trows\tcolumns")
    for source_id, source_schema in payload["schema"].items():
        counts = source_schema["table_counts"]
        for table, columns in source_schema["tables"].items():
            print(f"{source_id}\t{table}\t{counts.get(table, 0)}\t{','.join(column['name'] for column in columns)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read local Apple Contacts / AddressBook data.")
    parser.add_argument(
        "--addressbook-root",
        type=Path,
        default=DEFAULT_ADDRESSBOOK_ROOT,
        help="AddressBook support directory.",
    )
    parser.add_argument("--include-blobs", action="store_true", help="Include base64 blob data in JSON output.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_include_blobs(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--include-blobs",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Include base64 blob data in JSON output.",
        )

    sources = subparsers.add_parser("sources", help="List AddressBook source databases.")
    sources.add_argument("--json", action="store_true")
    add_include_blobs(sources)
    sources.set_defaults(handler=command_sources)

    list_cmd = subparsers.add_parser("list", help="List contacts as stable rows.")
    list_cmd.add_argument("--limit", type=int, default=200)
    list_cmd.add_argument("--all", action="store_true", help="List all contacts instead of the first --limit rows.")
    list_cmd.add_argument("--json", action="store_true")
    add_include_blobs(list_cmd)
    list_cmd.set_defaults(handler=command_list)

    search = subparsers.add_parser("search", help="Search contacts by name, org, handle, note, or group.")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--json", action="store_true")
    add_include_blobs(search)
    search.set_defaults(handler=command_search)

    show = subparsers.add_parser("show", help="Show one contact by db_ref or apple_contact_id.")
    show.add_argument("--id", required=True)
    show.add_argument("--json", action="store_true")
    add_include_blobs(show)
    show.set_defaults(handler=command_show)

    export = subparsers.add_parser("export", help="Export all normalized contact data.")
    export.add_argument("--json", action="store_true")
    add_include_blobs(export)
    export.set_defaults(handler=command_export)

    schema = subparsers.add_parser("schema", help="Show AddressBook SQLite table schema and counts.")
    schema.add_argument("--json", action="store_true")
    add_include_blobs(schema)
    schema.set_defaults(handler=command_schema)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except AppleContactsReadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
