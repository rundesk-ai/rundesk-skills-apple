#!/usr/bin/env python3
"""Tests for apple-contacts."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


TOOL_DIR = Path(__file__).resolve().parent
READ_SCRIPT = TOOL_DIR / "apple-contacts-read.py"
WRITE_SCRIPT = TOOL_DIR / "apple-contacts-write.py"
BRIDGE_SOURCE = TOOL_DIR / "AppleContactsBridge.swift"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def create_fixture_db(root: Path) -> Path:
    source = root / "Sources" / "SYNTHETIC-SOURCE"
    source.mkdir(parents=True)
    db = source / "AddressBook-v22.abcddb"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            create table Z_PRIMARYKEY (Z_ENT integer, Z_NAME text, Z_SUPER integer, Z_MAX integer);
            insert into Z_PRIMARYKEY values (19, 'ABCDGroup', 18, 1);
            insert into Z_PRIMARYKEY values (22, 'ABCDContact', 17, 1);

            create table ZABCDRECORD (
              Z_PK integer primary key,
              Z_ENT integer,
              ZUNIQUEID text,
              ZNAME text,
              ZTITLE text,
              ZFIRSTNAME text,
              ZMIDDLENAME text,
              ZLASTNAME text,
              ZSUFFIX text,
              ZMAIDENNAME text,
              ZNICKNAME text,
              ZORGANIZATION text,
              ZDEPARTMENT text,
              ZJOBTITLE text,
              ZPHONETICFIRSTNAME text,
              ZPHONETICMIDDLENAME text,
              ZPHONETICLASTNAME text,
              ZPHONETICORGANIZATION text,
              ZCREATIONDATE real,
              ZMODIFICATIONDATE real,
              ZLASTSYNCDATE real,
              ZBIRTHDAY real,
              ZBIRTHDAYYEAR integer,
              ZIMAGEDATA blob,
              ZTHUMBNAILIMAGEDATA blob
            );
            insert into ZABCDRECORD values
              (1, 22, 'apple-contact-1', '', 'Dr.', 'Ada', 'Byron', 'Lovelace', 'PhD', '', 'Enchantress', 'Analytical Engines', 'Research', 'Mathematician', '', '', '', '', 1000, 2000, 3000, 4000, 1815, X'0102', X'03'),
              (2, 19, 'apple-group-1', 'Research Group', '', '', '', '', '', '', '', '', '', '', '', '', '', '', null, null, null, null, null, null, null);

            create table ZABCDPHONENUMBER (
              Z_PK integer primary key,
              Z22_OWNER integer,
              ZFULLNUMBER text,
              ZLABEL text,
              ZCOUNTRYCODE text,
              ZORDERINGINDEX integer,
              ZISPRIMARY integer,
              ZISPRIVATE integer,
              ZUNIQUEID text
            );
            insert into ZABCDPHONENUMBER values (10, 1, '+15550101000', '_$!<Mobile>!$_', 'us', 0, 1, 0, 'phone-1');

            create table ZABCDEMAILADDRESS (
              Z_PK integer primary key,
              ZOWNER integer,
              Z22_OWNER integer,
              ZADDRESS text,
              ZADDRESSNORMALIZED text,
              ZLABEL text,
              ZORDERINGINDEX integer,
              ZISPRIMARY integer,
              ZISPRIVATE integer,
              ZUNIQUEID text
            );
            insert into ZABCDEMAILADDRESS values
              (11, 1, null, 'ada@example.test', 'ada@example.test', '_$!<Work>!$_', 0, 1, 0, 'email-1'),
              (21, null, 1, 'ada.secondary@example.test', 'ada.secondary@example.test', 'other', 1, 0, 0, 'email-2');

            create table ZABCDPOSTALADDRESS (
              Z_PK integer primary key,
              ZOWNER integer,
              ZLABEL text,
              ZSTREET text,
              ZCITY text,
              ZSTATE text,
              ZZIPCODE text,
              ZCOUNTRYNAME text,
              ZCOUNTRYCODE text,
              ZSUBLOCALITY text,
              ZORDERINGINDEX integer,
              ZUNIQUEID text
            );
            insert into ZABCDPOSTALADDRESS values (12, 1, 'home', '1 Example St
Suite 2', 'London', 'LDN', 'N1', 'United Kingdom', 'GB', 'North', 0, 'address-1');

            create table ZABCDURLADDRESS (
              Z_PK integer primary key,
              ZOWNER integer,
              ZLABEL text,
              ZURL text,
              ZORDERINGINDEX integer,
              ZUNIQUEID text
            );
            insert into ZABCDURLADDRESS values (13, 1, 'homepage', 'https://example.test/ada', 0, 'url-1');

            create table ZABCDSOCIALPROFILE (
              Z_PK integer primary key,
              ZOWNER integer,
              ZLABEL text,
              ZSERVICENAME text,
              ZUSERNAME text,
              ZUSERIDENTIFIER text,
              ZURLSTRING text,
              ZDISPLAYNAME text,
              ZORDERINGINDEX integer,
              ZUNIQUEID text
            );
            insert into ZABCDSOCIALPROFILE values (14, 1, 'work', 'Mastodon', 'ada', 'u-1', 'https://social.example.test/@ada', 'Ada L.', 0, 'social-1');

            create table ZABCDSERVICE (Z_PK integer primary key, ZSERVICENAME text);
            insert into ZABCDSERVICE values (20, 'Signal');
            create table ZABCDMESSAGINGADDRESS (
              Z_PK integer primary key,
              ZOWNER integer,
              ZSERVICE integer,
              ZADDRESS text,
              ZLABEL text,
              ZUSERIDENTIFIER text,
              ZORDERINGINDEX integer,
              ZUNIQUEID text
            );
            insert into ZABCDMESSAGINGADDRESS values (15, 1, 20, 'ada-signal', 'mobile', 'signal-user', 0, 'im-1');

            create table ZABCDRELATEDNAME (
              Z_PK integer primary key,
              ZOWNER integer,
              ZLABEL text,
              ZNAME text,
              ZORDERINGINDEX integer,
              ZUNIQUEID text
            );
            insert into ZABCDRELATEDNAME values (16, 1, 'assistant', 'Charles Babbage', 0, 'relation-1');

            create table ZABCDCONTACTDATE (
              Z_PK integer primary key,
              ZOWNER integer,
              ZLABEL text,
              ZDATE real,
              ZDATEYEAR integer,
              ZDATEYEARLESS real,
              ZORDERINGINDEX integer,
              ZUNIQUEID text
            );
            insert into ZABCDCONTACTDATE values (17, 1, 'anniversary', 5000, 1843, null, 0, 'date-1');

            create table ZABCDNOTE (
              Z_PK integer primary key,
              ZCONTACT integer,
              ZTEXT text,
              ZRICHTEXTDATA blob
            );
            insert into ZABCDNOTE values (18, 1, 'Synthetic note
for testing.', X'0405');

            create table ZABCDCALENDARURI (
              Z_PK integer primary key,
              Z17_OWNER integer,
              ZLABEL text,
              ZURL text,
              ZORDERINGINDEX integer,
              ZUNIQUEID text
            );
            insert into ZABCDCALENDARURI values (19, 1, 'work', 'caldav://example.test/ada', 0, 'calendar-1');

            create table Z_22PARENTGROUPS (
              Z_22CONTACTS integer,
              Z_19PARENTGROUPS1 integer
            );
            insert into Z_22PARENTGROUPS values (1, 2);
            """
        )
        conn.commit()
    finally:
        conn.close()
    return db


class AppleContactsReadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.read_module = load_module("apple_contacts_read", READ_SCRIPT)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "AddressBook"
        create_fixture_db(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_read_normalizes_common_contact_details(self) -> None:
        payload = self.read_module.read_addressbook(self.root)

        self.assertEqual(payload["counts"]["contacts"], 1)
        self.assertEqual(payload["counts"]["groups"], 1)
        contact = payload["contacts"][0]
        self.assertEqual(contact["db_ref"], "SYNTHETIC-SOURCE:1")
        self.assertEqual(contact["apple_contact_id"], "apple-contact-1")
        self.assertEqual(contact["display_name"], "Dr. Ada Byron Lovelace PhD")
        self.assertEqual(contact["phones"][0]["value"], "+15550101000")
        self.assertEqual(contact["emails"][0]["value"], "ada@example.test")
        self.assertEqual(contact["emails"][1]["value"], "ada.secondary@example.test")
        self.assertEqual(contact["postal_addresses"][0]["street"], "1 Example St\nSuite 2")
        self.assertEqual(contact["postal_addresses"][0]["city"], "London")
        self.assertEqual(contact["urls"][0]["value"], "https://example.test/ada")
        self.assertEqual(contact["calendar_uris"][0]["url"], "caldav://example.test/ada")
        self.assertEqual(contact["social_profiles"][0]["service"], "Mastodon")
        self.assertEqual(contact["instant_messages"][0]["service"], "Signal")
        self.assertEqual(contact["relations"][0]["name"], "Charles Babbage")
        self.assertEqual(contact["note"], "Synthetic note\nfor testing.")
        self.assertEqual(contact["groups"][0]["name"], "Research Group")
        self.assertEqual(contact["images"]["image"]["bytes"], 2)
        self.assertNotIn("base64", contact["images"]["image"])

    def test_include_blobs_adds_base64_payloads(self) -> None:
        payload = self.read_module.read_addressbook(self.root, include_blobs=True)

        self.assertEqual(payload["contacts"][0]["images"]["image"]["base64"], "AQI=")
        self.assertEqual(payload["contacts"][0]["notes"][0]["rich_text"]["base64"], "BAU=")

    def test_include_blobs_works_after_subcommand(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            rc = self.read_module.main(
                ["--addressbook-root", str(self.root), "show", "--id", "apple-contact-1", "--json", "--include-blobs"]
            )

        self.assertEqual(rc, 0)
        contact = json.loads(output.getvalue())
        self.assertEqual(contact["images"]["image"]["base64"], "AQI=")

    def test_search_and_show_commands_support_json_and_text(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            rc = self.read_module.main(["--addressbook-root", str(self.root), "search", "Ada Engines", "--json"])
        self.assertEqual(rc, 0)
        matches = json.loads(output.getvalue())
        self.assertEqual(matches[0]["apple_contact_id"], "apple-contact-1")

        detail = io.StringIO()
        with redirect_stdout(detail):
            rc = self.read_module.main(["--addressbook-root", str(self.root), "show", "--id", "apple-contact-1"])
        self.assertEqual(rc, 0)
        self.assertIn("Contact: Dr. Ada Byron Lovelace PhD", detail.getvalue())
        self.assertIn("Synthetic note", detail.getvalue())
        self.assertIn("for testing.", detail.getvalue())
        self.assertIn("Postal addresses:", detail.getvalue())
        self.assertIn("home | 1 Example St", detail.getvalue())
        self.assertIn("Groups:", detail.getvalue())
        self.assertIn("Research Group | apple-group-1", detail.getvalue())

    def test_contact_detail_text_omits_empty_sections(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.read_module.print_contact_detail(
                {
                    "display_name": "Sparse Example",
                    "db_ref": "source:1",
                    "apple_contact_id": "contact-1",
                    "source_id": "source",
                    "organization_name": "",
                    "department_name": "",
                    "job_title": "",
                    "nickname": "",
                    "created_at": "",
                    "modified_at": "2026-06-24T12:00:00+00:00",
                    "phones": [{"label": "mobile", "value": "+15550101000"}],
                    "emails": [],
                    "urls": [],
                    "instant_messages": [],
                    "social_profiles": [{"label": "", "url": "https://social.example.test/sparse"}],
                    "relations": [],
                    "dates": [],
                    "postal_addresses": [],
                    "groups": [],
                    "note": "Useful note",
                }
            )

        text = output.getvalue()
        self.assertIn("Contact: Sparse Example", text)
        self.assertIn("  modified_at: 2026-06-24T12:00:00+00:00", text)
        self.assertIn("  Phones:\n    mobile | +15550101000", text)
        self.assertIn("  Social profiles:\n    - | https://social.example.test/sparse", text)
        self.assertIn("  Notes:\n    Useful note", text)
        self.assertNotIn("organization:", text)
        self.assertNotIn("department:", text)
        self.assertNotIn("job_title:", text)
        self.assertNotIn("nickname:", text)
        self.assertNotIn("created_at:", text)
        self.assertNotIn("Emails:", text)
        self.assertNotIn("URLs:", text)
        self.assertNotIn("Instant messages:", text)
        self.assertNotIn("Relations:", text)
        self.assertNotIn("Dates:", text)
        self.assertNotIn("Postal addresses:", text)
        self.assertNotIn("Groups:", text)
        self.assertNotIn("\n    -\n", text)

    def test_contact_detail_classifies_every_normalized_field(self) -> None:
        # Guards against silent gaps: every key the normalizer emits must be
        # either rendered by print_contact_detail or explicitly hidden. A new
        # normalized field with no decision fails here instead of vanishing.
        contact = self.read_module.read_addressbook(self.root)["contacts"][0]
        classified = self.read_module.DETAIL_RENDERED_KEYS | self.read_module.DETAIL_HIDDEN_KEYS
        unclassified = set(contact) - classified
        stale = classified - set(contact)
        self.assertEqual(unclassified, set(), f"unclassified normalized fields: {sorted(unclassified)}")
        self.assertEqual(stale, set(), f"classified keys not produced by normalizer: {sorted(stale)}")
        self.assertEqual(
            self.read_module.DETAIL_RENDERED_KEYS & self.read_module.DETAIL_HIDDEN_KEYS,
            set(),
            "a field cannot be both rendered and hidden",
        )

    def test_contact_detail_renders_birthday_and_sync_fields(self) -> None:
        detail = io.StringIO()
        with redirect_stdout(detail):
            rc = self.read_module.main(["--addressbook-root", str(self.root), "show", "--id", "apple-contact-1"])
        self.assertEqual(rc, 0)
        text = detail.getvalue()
        self.assertIn("  birthday: 2001-01-01", text)
        self.assertIn("  last_sync_at:", text)

    def test_sources_list_and_schema_have_stable_text_headers(self) -> None:
        for command, header in [
            (["sources"], "source_id\tdb_path\tcontacts"),
            (["list"], "db_ref\tapple_contact_id\tdisplay_name"),
            (["schema"], "source_id\ttable\trows\tcolumns"),
        ]:
            output = io.StringIO()
            with redirect_stdout(output):
                rc = self.read_module.main(["--addressbook-root", str(self.root), *command])
            self.assertEqual(rc, 0)
            self.assertTrue(output.getvalue().startswith(header), output.getvalue())


class AppleContactsWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.write_module = load_module("apple_contacts_write", WRITE_SCRIPT)
        self.tmp = tempfile.TemporaryDirectory()
        self.payload = Path(self.tmp.name) / "contact.json"
        self.payload.write_text(
            json.dumps(
                {
                    "contact": {
                        "given_name": "Synthetic",
                        "family_name": "Agent",
                        "organization_name": "Example Org",
                        "phones": [{"label": "mobile", "value": "+15550101111"}],
                        "emails": [{"label": "work", "value": "agent@example.test"}],
                        "note": "dry-run note",
                    }
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_status_names_authorization_state_and_fails_closed_unless_authorized(self) -> None:
        cases = [
            (0, "notDetermined", "not_authorized", 1),
            (1, "restricted", "not_authorized", 1),
            (2, "denied", "not_authorized", 1),
            (3, "authorized", "ok", 0),
            (99, "unknown(99)", "not_authorized", 1),
            (None, "unknown(None)", "not_authorized", 1),
        ]

        for raw_status, state, status, expected_rc in cases:
            with self.subTest(state=state):
                output = io.StringIO()
                bridge_payload = {
                    "operation": "status",
                    "status": "ok",
                    "authorization_status": raw_status,
                }
                with (
                    patch.object(self.write_module, "run_bridge", return_value=bridge_payload),
                    redirect_stdout(output),
                ):
                    rc = self.write_module.main(["status", "--json"])

                payload = json.loads(output.getvalue())
                self.assertEqual(rc, expected_rc)
                self.assertEqual(payload["authorization_status"], raw_status)
                self.assertEqual(payload["authorization_state"], state)
                self.assertEqual(payload["status"], status)

    def test_status_text_renders_named_authorization_state(self) -> None:
        output = io.StringIO()
        bridge_payload = {
            "operation": "status",
            "status": "ok",
            "authorization_status": 2,
        }
        with patch.object(self.write_module, "run_bridge", return_value=bridge_payload), redirect_stdout(output):
            rc = self.write_module.main(["status"])

        self.assertEqual(rc, 1)
        self.assertIn("status=not_authorized", output.getvalue())
        self.assertIn("authorization_state=denied", output.getvalue())

    def test_every_mutation_is_dry_run_by_default_and_forwards_only_explicit_confirm(self) -> None:
        cases = [
            ("contact create", ["create", "--payload", str(self.payload)], "contact.create"),
            (
                "contact update",
                ["update", "--id", "contact-1", "--payload", str(self.payload)],
                "contact.update",
            ),
            ("contact delete", ["delete", "--id", "contact-1"], "contact.delete"),
            ("group create", ["groups", "create", "--name", "Synthetic Group"], "group.create"),
            (
                "group update",
                ["groups", "update", "--id", "group-1", "--name", "Renamed Group"],
                "group.update",
            ),
            ("group delete", ["groups", "delete", "--id", "group-1"], "group.delete"),
            (
                "group add-contact",
                ["groups", "add-contact", "--contact-id", "contact-1", "--group-id", "group-1"],
                "group.addContact",
            ),
            (
                "group remove-contact",
                ["groups", "remove-contact", "--contact-id", "contact-1", "--group-id", "group-1"],
                "group.removeContact",
            ),
        ]

        for label, command, operation in cases:
            for confirmed in (False, True):
                with self.subTest(command=label, confirmed=confirmed):
                    requests: list[dict[str, object]] = []

                    def fake_bridge(request):
                        requests.append(request)
                        return {
                            "operation": request["operation"],
                            "dry_run": not request["confirm"],
                            "status": "ok",
                        }

                    argv = [*command, *(["--confirm"] if confirmed else [])]
                    output = io.StringIO()
                    with patch.object(self.write_module, "run_bridge", side_effect=fake_bridge), redirect_stdout(output):
                        rc = self.write_module.main(argv)

                    self.assertEqual(rc, 0)
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(requests[0]["operation"], operation)
                    self.assertIs(requests[0]["confirm"], confirmed)
                    self.assertIn(f"dry_run={'false' if confirmed else 'true'}", output.getvalue())

    def test_create_is_dry_run_by_default_and_sends_payload(self) -> None:
        requests: list[dict[str, object]] = []

        def fake_bridge(request, fixture=None):
            requests.append(request)
            return {
                "operation": request["operation"],
                "dry_run": not request["confirm"],
                "status": "ok",
                "contact_id": "contact-1",
                "contact": {"display_name": "Synthetic Agent", "phones": 1, "emails": 1},
            }

        output = io.StringIO()
        with patch.object(self.write_module, "run_bridge", side_effect=fake_bridge), redirect_stdout(output):
            rc = self.write_module.main(["create", "--payload", str(self.payload)])

        self.assertEqual(rc, 0)
        self.assertFalse(requests[0]["confirm"])
        self.assertEqual(requests[0]["operation"], "contact.create")
        self.assertEqual(requests[0]["contact"]["emails"][0]["value"], "agent@example.test")
        self.assertIn("dry_run=true", output.getvalue())

    def test_update_confirm_sets_confirm_true(self) -> None:
        requests: list[dict[str, object]] = []

        def fake_bridge(request, fixture=None):
            requests.append(request)
            return {
                "operation": request["operation"],
                "dry_run": not request["confirm"],
                "status": "ok",
                "contact_id": request["id"],
                "after": {"display_name": "Synthetic Agent", "phones": 1, "emails": 1},
            }

        with patch.object(self.write_module, "run_bridge", side_effect=fake_bridge):
            rc = self.write_module.main(["update", "--id", "contact-1", "--payload", str(self.payload), "--confirm"])

        self.assertEqual(rc, 0)
        self.assertTrue(requests[0]["confirm"])
        self.assertEqual(requests[0]["id"], "contact-1")

    def test_groups_add_contact_uses_exact_ids(self) -> None:
        requests: list[dict[str, object]] = []

        def fake_bridge(request, fixture=None):
            requests.append(request)
            return {
                "operation": request["operation"],
                "dry_run": not request["confirm"],
                "status": "ok",
                "contact": {"display_name": "Synthetic Agent"},
                "group": {"id": request["group_id"], "name": "Synthetic Group"},
            }

        with patch.object(self.write_module, "run_bridge", side_effect=fake_bridge):
            rc = self.write_module.main(
                ["groups", "add-contact", "--contact-id", "contact-1", "--group-id", "group-1"]
            )

        self.assertEqual(rc, 0)
        self.assertEqual(requests[0]["operation"], "group.addContact")
        self.assertEqual(requests[0]["contact_id"], "contact-1")
        self.assertEqual(requests[0]["group_id"], "group-1")

    def test_groups_members_lists_contacts(self) -> None:
        requests: list[dict[str, object]] = []

        def fake_bridge(request):
            requests.append(request)
            return {
                "operation": request["operation"],
                "dry_run": True,
                "status": "ok",
                "members": [
                    {
                        "id": "contact-1",
                        "display_name": "Synthetic Agent",
                        "phones": [{"value": "+15550101111"}],
                        "emails": [{"value": "agent@example.test"}],
                    }
                ],
            }

        output = io.StringIO()
        with patch.object(self.write_module, "run_bridge", side_effect=fake_bridge), redirect_stdout(output):
            rc = self.write_module.main(["groups", "members", "--id", "group-1"])

        self.assertEqual(rc, 0)
        self.assertEqual(requests[0]["operation"], "group.members")
        self.assertEqual(requests[0]["id"], "group-1")
        self.assertIn("Synthetic Agent", output.getvalue())

    def test_payload_loader_accepts_direct_contact_object(self) -> None:
        direct_payload = Path(self.tmp.name) / "direct.json"
        direct_payload.write_text(
            json.dumps({"given_name": "Direct", "emails": [{"label": "work", "value": "direct@example.test"}]}),
            encoding="utf-8",
        )

        payload = self.write_module.load_contact_payload(str(direct_payload))

        self.assertEqual(payload["given_name"], "Direct")
        self.assertEqual(payload["emails"][0]["value"], "direct@example.test")

    def test_payload_loader_rejects_unknown_contact_fields(self) -> None:
        bad_payload = Path(self.tmp.name) / "bad-field.json"
        bad_payload.write_text(json.dumps({"display_name": "Wrong Field"}), encoding="utf-8")

        with self.assertRaisesRegex(self.write_module.AppleContactsWriteError, "unknown contact field"):
            self.write_module.load_contact_payload(str(bad_payload))

    def test_payload_loader_rejects_malformed_array_fields(self) -> None:
        object_payload = Path(self.tmp.name) / "object-array.json"
        object_payload.write_text(json.dumps({"phones": {"value": "+15550101111"}}), encoding="utf-8")
        missing_value_payload = Path(self.tmp.name) / "missing-value.json"
        missing_value_payload.write_text(json.dumps({"emails": [{"label": "work"}]}), encoding="utf-8")
        bad_birthday_payload = Path(self.tmp.name) / "bad-birthday.json"
        bad_birthday_payload.write_text(json.dumps({"birthday": "06/24/2026"}), encoding="utf-8")
        both_address_payload = Path(self.tmp.name) / "both-addresses.json"
        both_address_payload.write_text(
            json.dumps({"addresses": [{"city": "A"}], "postal_addresses": [{"city": "B"}]}),
            encoding="utf-8",
        )
        sibling_payload = Path(self.tmp.name) / "sibling.json"
        sibling_payload.write_text(
            json.dumps({"contact": {"given_name": "Wrapped"}, "emails": [{"value": "dropped@example.test"}]}),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(self.write_module.AppleContactsWriteError, "phones must be an array"):
            self.write_module.load_contact_payload(str(object_payload))
        with self.assertRaisesRegex(self.write_module.AppleContactsWriteError, "emails\\[0\\] requires"):
            self.write_module.load_contact_payload(str(missing_value_payload))
        with self.assertRaisesRegex(self.write_module.AppleContactsWriteError, "birthday must use"):
            self.write_module.load_contact_payload(str(bad_birthday_payload))
        with self.assertRaisesRegex(self.write_module.AppleContactsWriteError, "addresses or postal_addresses"):
            self.write_module.load_contact_payload(str(both_address_payload))
        with self.assertRaisesRegex(self.write_module.AppleContactsWriteError, "sibling field"):
            self.write_module.load_contact_payload(str(sibling_payload))

    def test_swift_bridge_compiles(self) -> None:
        binary = Path(self.tmp.name) / "apple-contacts-bridge-check"
        result = subprocess.run(
            ["/usr/bin/swiftc", str(BRIDGE_SOURCE), "-o", str(binary)],
            cwd=TOOL_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_swift_bridge_rejects_missing_operation(self) -> None:
        binary = Path(self.tmp.name) / "apple-contacts-bridge-check"
        request = Path(self.tmp.name) / "request.json"
        request.write_text("{}", encoding="utf-8")
        compile_result = subprocess.run(
            ["/usr/bin/swiftc", str(BRIDGE_SOURCE), "-o", str(binary)],
            cwd=TOOL_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(compile_result.returncode, 0, compile_result.stderr or compile_result.stdout)

        result = subprocess.run(
            [str(binary), str(request)],
            cwd=TOOL_DIR,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing operation", result.stderr)


@unittest.skipUnless(os.environ.get("APPLE_CONTACTS_LIVE_TESTS") == "1", "set APPLE_CONTACTS_LIVE_TESTS=1")
class AppleContactsLiveTest(unittest.TestCase):
    def run_json(self, args: list[str]) -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, *args, "--json"],
            cwd=TOOL_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def read_search(self, query: str) -> list[dict[str, object]]:
        result = subprocess.run(
            [sys.executable, str(READ_SCRIPT), "search", query, "--json"],
            cwd=TOOL_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def wait_for_contact(self, query: str, predicate=None, attempts: int = 10) -> dict[str, object]:
        for _ in range(attempts):
            matches = self.read_search(query)
            if matches and (predicate is None or predicate(matches[0])):
                return matches[0]
            time.sleep(1)
        self.fail(f"synthetic contact {query} did not reach expected state")

    def wait_for_no_contact(self, query: str) -> None:
        for _ in range(10):
            if not self.read_search(query):
                return
            time.sleep(1)
        self.fail(f"synthetic contact {query} was still visible")

    def groups_payload(self) -> dict[str, object]:
        return self.run_json([str(WRITE_SCRIPT), "groups", "list"])

    def group_names(self) -> set[str]:
        return {str(group.get("name")) for group in self.groups_payload().get("groups", [])}

    def wait_for_group_absent(self, name: str) -> None:
        for _ in range(10):
            if name not in self.group_names():
                return
            time.sleep(1)
        self.fail(f"synthetic group {name} was still visible")

    def assert_contact_has_group(self, marker: str, group_name: str, present: bool) -> None:
        def predicate(contact: dict[str, object]) -> bool:
            names = {str(group.get("name")) for group in contact.get("groups", [])}
            return (group_name in names) is present

        self.wait_for_contact(marker, predicate, attempts=30)

    def assert_group_has_member(self, group_id: str, contact_id: str, present: bool) -> None:
        for _ in range(10):
            members = self.run_json([str(WRITE_SCRIPT), "groups", "members", "--id", group_id]).get("members", [])
            ids = {str(member.get("id")) for member in members}
            if (contact_id in ids) is present:
                return
            time.sleep(1)
        self.fail(f"group {group_id} membership for {contact_id} did not reach expected state")

    def test_live_synthetic_contact_crud(self) -> None:
        marker = f"CodexSynthetic-{uuid.uuid4().hex[:10]}"
        dry_marker = f"{marker}-DryRun"
        group_name = f"{marker} Group"
        dry_group_name = f"{marker} Dry Group"
        contact_id = ""
        group_id = ""
        with tempfile.TemporaryDirectory() as tmp:
            create_payload = Path(tmp) / "create.json"
            update_payload = Path(tmp) / "update.json"
            dry_payload = Path(tmp) / "dry.json"
            note_payload = Path(tmp) / "note.json"
            bad_payload = Path(tmp) / "bad.json"
            create_payload.write_text(
                json.dumps(
                    {
                        "given_name": marker,
                        "family_name": "Contact",
                        "organization_name": "Codex Synthetic Tests",
                        "phones": [{"label": "mobile", "value": "+15550109999"}],
                        "emails": [{"label": "work", "value": f"{marker.lower()}@example.test"}],
                        "addresses": [{"label": "home", "street": "1 Test Way", "city": "Testville", "state": "TS"}],
                        "urls": [{"label": "homepage", "value": "https://example.test/contact"}],
                        "social_profiles": [{"label": "work", "service": "Mastodon", "username": marker}],
                        "instant_messages": [{"label": "work", "service": "Signal", "username": marker}],
                        "relations": [{"label": "assistant", "name": "Synthetic Helper"}],
                        "dates": [{"label": "anniversary", "date": "2026-06-24"}],
                        "birthday": "2000-01-02",
                    }
                ),
                encoding="utf-8",
            )
            dry_payload.write_text(json.dumps({"given_name": dry_marker, "family_name": "Contact"}), encoding="utf-8")
            note_payload.write_text(
                json.dumps({"given_name": f"{marker}-Note", "family_name": "Contact", "note": "note should fail closed"}),
                encoding="utf-8",
            )
            bad_payload.write_text(json.dumps({"given_name": f"{marker}-Bad", "phones": {"value": "+15550107777"}}), encoding="utf-8")
            update_payload.write_text(
                json.dumps(
                    {
                        "job_title": "Updated Synthetic Contact",
                        "phones": [{"label": "mobile", "value": "+15550108888"}],
                    }
                ),
                encoding="utf-8",
            )

            try:
                self.run_json([str(WRITE_SCRIPT), "create", "--payload", str(dry_payload)])
                self.assertFalse(self.read_search(dry_marker))
                bad_result = subprocess.run(
                    [sys.executable, str(WRITE_SCRIPT), "create", "--payload", str(bad_payload), "--confirm", "--json"],
                    cwd=TOOL_DIR,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(bad_result.returncode, 0)
                self.assertIn("phones must be an array", bad_result.stderr)
                self.assertFalse(self.read_search(f"{marker}-Bad"))

                note_result = subprocess.run(
                    [sys.executable, str(WRITE_SCRIPT), "create", "--payload", str(note_payload), "--confirm", "--json"],
                    cwd=TOOL_DIR,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(note_result.returncode, 0)
                self.assertIn("direct AddressBook DB fallback is forbidden", note_result.stderr)
                self.assertFalse(self.read_search(f"{marker}-Note"))

                self.run_json([str(WRITE_SCRIPT), "groups", "create", "--name", dry_group_name])
                self.assertNotIn(dry_group_name, self.group_names())

                created = self.run_json(
                    [str(WRITE_SCRIPT), "create", "--payload", str(create_payload), "--confirm"]
                )
                contact_id = str(created["contact_id"])
                self.assertTrue(contact_id)

                group = self.run_json(
                    [str(WRITE_SCRIPT), "groups", "create", "--name", group_name, "--confirm"]
                )
                group_id = str(group["group_id"])
                self.assertTrue(group_id)
                self.assertIn(group_name, self.group_names())
                renamed_group_name = f"{group_name} Renamed"
                self.run_json([str(WRITE_SCRIPT), "groups", "update", "--id", group_id, "--name", renamed_group_name])
                self.assertIn(group_name, self.group_names())
                self.assertNotIn(renamed_group_name, self.group_names())
                self.run_json([str(WRITE_SCRIPT), "groups", "delete", "--id", group_id])
                self.assertIn(group_name, self.group_names())

                created_read = self.wait_for_contact(marker)
                self.assertEqual(created_read["apple_contact_id"], contact_id)
                self.assertEqual(created_read["organization_name"], "Codex Synthetic Tests")
                self.assertEqual(created_read["phones"][0]["value"], "+15550109999")
                self.assertEqual(created_read["emails"][0]["value"], f"{marker.lower()}@example.test")
                self.assertEqual(created_read["postal_addresses"][0]["street"], "1 Test Way")
                self.assertEqual(created_read["urls"][0]["value"], "https://example.test/contact")
                self.assertEqual(created_read["social_profiles"][0]["username"], marker)
                self.assertEqual(created_read["instant_messages"][0]["value"], marker)
                self.assertEqual(created_read["relations"][0]["name"], "Synthetic Helper")
                self.assertTrue(created_read["dates"])
                self.assertEqual(created_read["birthday"]["year"], 2000)

                self.run_json([str(WRITE_SCRIPT), "update", "--id", contact_id, "--payload", str(update_payload)])
                still_original = self.wait_for_contact(marker, lambda contact: contact["phones"][0]["value"] == "+15550109999")
                self.assertNotEqual(still_original["job_title"], "Updated Synthetic Contact")

                self.run_json([str(WRITE_SCRIPT), "update", "--id", contact_id, "--payload", str(update_payload), "--confirm"])
                updated_read = self.wait_for_contact(
                    marker,
                    lambda contact: contact["job_title"] == "Updated Synthetic Contact"
                    and contact["phones"][0]["value"] == "+15550108888",
                )
                self.assertEqual(len(updated_read["phones"]), 1)
                self.assertEqual(updated_read["emails"][0]["value"], f"{marker.lower()}@example.test")

                self.run_json([str(WRITE_SCRIPT), "groups", "add-contact", "--contact-id", contact_id, "--group-id", group_id])
                self.assert_group_has_member(group_id, contact_id, present=False)
                self.run_json([str(WRITE_SCRIPT), "groups", "add-contact", "--contact-id", contact_id, "--group-id", group_id, "--confirm"])
                self.assert_group_has_member(group_id, contact_id, present=True)
                self.assert_contact_has_group(marker, group_name, present=True)
                self.run_json([str(WRITE_SCRIPT), "groups", "remove-contact", "--contact-id", contact_id, "--group-id", group_id])
                self.assert_group_has_member(group_id, contact_id, present=True)
                self.run_json([str(WRITE_SCRIPT), "groups", "remove-contact", "--contact-id", contact_id, "--group-id", group_id, "--confirm"])
                self.assert_group_has_member(group_id, contact_id, present=False)
                self.assert_contact_has_group(marker, group_name, present=False)

                self.run_json([str(WRITE_SCRIPT), "delete", "--id", contact_id])
                self.assertTrue(self.read_search(marker))
                self.run_json([str(WRITE_SCRIPT), "delete", "--id", contact_id, "--confirm"])
                contact_id = ""
                self.wait_for_no_contact(marker)

                self.run_json([str(WRITE_SCRIPT), "groups", "delete", "--id", group_id, "--confirm"])
                group_id = ""
                self.wait_for_group_absent(group_name)
            finally:
                if contact_id:
                    subprocess.run(
                        [sys.executable, str(WRITE_SCRIPT), "delete", "--id", contact_id, "--confirm"],
                        cwd=TOOL_DIR,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                if group_id:
                    subprocess.run(
                        [sys.executable, str(WRITE_SCRIPT), "groups", "delete", "--id", group_id, "--confirm"],
                        cwd=TOOL_DIR,
                        text=True,
                        capture_output=True,
                        check=False,
                    )


if __name__ == "__main__":
    unittest.main()
