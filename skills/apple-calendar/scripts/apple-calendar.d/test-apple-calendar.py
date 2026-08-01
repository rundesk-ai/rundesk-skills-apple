#!/usr/bin/env python3
"""Offline tests for apple-calendar."""

from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parent
READ_SCRIPT = SCRIPT_DIR / "apple-calendar-read.py"
WRITE_SCRIPT = SCRIPT_DIR / "apple-calendar-write.py"


def load_module(name: str, path: Path):
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def synthetic_source() -> dict:
    return {
        "sourceIdentifier": "source-example",
        "title": "Example Account",
        "type": "caldav",
        "calendarCount": 2,
    }


def synthetic_calendar(writable: bool = True) -> dict:
    return {
        "calendarIdentifier": "calendar-example",
        "title": "Example Team",
        "type": "caldav",
        "allowsContentModifications": writable,
        "sourceIdentifier": "source-example",
        "sourceTitle": "Example Account",
        "sourceType": "caldav",
        "source": synthetic_source(),
        "color": "#336699",
    }


def synthetic_event(title: str = "Example Planning") -> dict:
    return {
        "eventIdentifier": "event-example",
        "calendarItemIdentifier": "item-example",
        "calendarItemExternalIdentifier": "external-example",
        "title": title,
        "start": "2026-06-25T14:00:00Z",
        "end": "2026-06-25T14:30:00Z",
        "isAllDay": False,
        "status": "confirmed",
        "availability": "busy",
        "calendar": synthetic_calendar(),
        "hasAlarms": True,
        "hasRecurrenceRules": True,
        "location": "Example Room",
        "timeZone": "America/New_York",
        "creationDate": "2026-06-20T14:00:00Z",
        "lastModifiedDate": "2026-06-21T14:00:00Z",
        "organizer": {"name": "Example Organizer", "url": "mailto:organizer@example.test", "status": "accepted", "role": "chair", "type": "person"},
        "notes": "Synthetic notes only.",
        "url": "https://example.test/calendar",
        "attendees": [
            {"name": "Example Attendee", "url": "mailto:attendee@example.test", "status": "accepted", "role": "required", "type": "person"}
        ],
        "alarms": [{"relativeOffset": -900, "absoluteDate": ""}],
        "recurrenceRules": [{"frequency": "weekly", "interval": 1, "endDate": "", "occurrenceCount": 4, "description": "synthetic"}],
    }


def fixture_payload() -> dict:
    readonly_calendar = {**synthetic_calendar(False), "calendarIdentifier": "calendar-readonly", "title": "Example Readonly"}
    focus_event = {**synthetic_event("Example Focus"), "eventIdentifier": "event-focus", "availability": "free", "calendar": readonly_calendar}
    focus_event["location"] = None
    return {
        "status": "ok",
        "sources": [synthetic_source()],
        "calendars": [synthetic_calendar(), readonly_calendar],
        "events": [
            synthetic_event(),
            focus_event,
        ],
        "event": synthetic_event(),
        "writableCalendars": 1,
    }


class AppleCalendarReadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.read_module = load_module("apple_calendar_read", READ_SCRIPT)
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = Path(self.tmp.name) / "fixture.json"
        self.fixture.write_text(json.dumps(fixture_payload()), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_read(self, *args: str) -> str:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.read_module.main([*args, "--fixture", str(self.fixture)]), 0)
        return output.getvalue()

    def test_status_sources_calendars_text_and_json(self) -> None:
        self.assertIn("Apple Calendar access ok", self.run_read("status"))
        self.assertIn("source_id=source-example", self.run_read("sources", "--source", "Example"))
        self.assertIn("source_id=source-example", self.run_read("sources", "--source-id", "source-example"))
        self.assertIn("source_id=source-example", self.run_read("sources", "--source", "Missing,Example"))
        calendars = self.run_read("calendars", "--writable")
        self.assertIn("calendar_id=calendar-example", calendars)
        self.assertNotIn("calendar-readonly", calendars)
        self.assertIn("calendar_id=calendar-example", self.run_read("calendars", "--type", "caldav"))
        self.assertIn("calendar_id=calendar-example", self.run_read("calendars", "--calendar-id", "calendar-example"))

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.read_module.main(["calendars", "--fixture", str(self.fixture), "--json"]), 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["count"], 2)

    def test_events_search_show_availability_and_export(self) -> None:
        events = self.run_read("events", "--from", "2026-06-25", "--to", "2026-06-26", "--query", "planning")
        self.assertIn("when=2026-06-25", events)
        self.assertIn("title=Example Planning", events)
        self.assertIn("calendar=Example Team", events)
        self.assertIn("location=Example Room", events)
        self.assertNotIn("event_id=", events)
        self.assertNotIn("event-focus", events)

        focus_events = self.run_read("events", "--from", "2026-06-25", "--to", "2026-06-26", "--query", "focus")
        self.assertIn("title=Example Focus", focus_events)
        self.assertNotIn("location=-", focus_events)

        full_events = self.run_read("events", "--from", "2026-06-25", "--to", "2026-06-26", "--query", "planning", "--full")
        self.assertIn("event_id=event-example", full_events)
        self.assertIn("item_id=item-example", full_events)

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                self.read_module.main(
                    ["events", "--fixture", str(self.fixture), "--from", "2026-06-25", "--to", "2026-06-26", "--query", "planning", "--json"]
                ),
                0,
            )
        events_json = json.loads(output.getvalue())
        self.assertEqual(events_json["events"][0]["eventIdentifier"], "event-example")
        self.assertEqual(events_json["events"][0]["calendar"]["calendarIdentifier"], "calendar-example")

        self.assertIn("title=Example Planning", self.run_read("events", "--from", "2026-06-25", "--to", "2026-06-26", "--source", "Example"))
        self.assertIn("title=Example Planning", self.run_read("events", "--from", "2026-06-25", "--to", "2026-06-26", "--calendar", "Missing,Team"))
        self.assertIn("title=Example Planning", self.run_read("events", "--from", "2026-06-25", "--to", "2026-06-26", "--calendar-id", "calendar-example"))
        writable_events = self.run_read("events", "--from", "2026-06-25", "--to", "2026-06-26", "--writable")
        self.assertIn("title=Example Planning", writable_events)
        self.assertNotIn("Example Focus", writable_events)

        search = self.run_read("search", "attendee", "--from", "2026-06-25", "--to", "2026-06-26")
        self.assertIn("title=Example Planning", search)
        self.assertNotIn("event_id=", search)
        search_full = self.run_read("search", "attendee", "--from", "2026-06-25", "--to", "2026-06-26", "--full")
        self.assertIn("event_id=event-example", search_full)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                self.read_module.main(["search", "attendee", "--fixture", str(self.fixture), "--from", "2026-06-25", "--to", "2026-06-26", "--json"]),
                0,
            )
        search_json = json.loads(output.getvalue())
        self.assertEqual(search_json["events"][0]["eventIdentifier"], "event-example")
        self.assertIn("attendees", search_json["events"][0])

        show = self.run_read("show", "--event-id", "event-example")
        self.assertIn("Apple Calendar event", show)
        self.assertIn("attendees:", show)
        self.assertIn("recurrence:", show)

        availability = self.run_read("availability", "--from", "2026-06-25", "--to", "2026-06-26")
        self.assertIn("blocks=1", availability)
        self.assertIn("availability=busy", availability)
        self.assertNotIn("event_id=", availability)
        availability_full = self.run_read("availability", "--from", "2026-06-25", "--to", "2026-06-26", "--full")
        self.assertIn("event_id=event-example", availability_full)
        self.assertNotIn("event-focus", availability)

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                self.read_module.main(["export", "--fixture", str(self.fixture), "--days", "7", "--json"]),
                0,
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["command"], "export")
        self.assertEqual(payload["counts"]["calendars"], 2)
        self.assertEqual(payload["counts"]["events"], 2)

    def test_export_supports_explicit_range_and_all_bounds(self) -> None:
        requests: list[dict] = []

        def fake_bridge(request, fixture=None):
            requests.append(request)
            if request["operation"] == "calendars":
                return {"calendars": [synthetic_calendar()]}
            return {"events": [synthetic_event()]}

        with patch.object(self.read_module, "run_bridge", side_effect=fake_bridge), redirect_stdout(io.StringIO()):
            self.assertEqual(self.read_module.main(["export", "--from", "2026-06-25", "--to", "2026-06-26", "--json"]), 0)
            self.assertEqual(self.read_module.main(["export", "--all", "--json"]), 0)

        explicit_events_request = next(request for request in requests if request["operation"] == "events")
        all_events_request = [request for request in requests if request["operation"] == "events"][1]
        self.assertLess(explicit_events_request["startTimestamp"], explicit_events_request["endTimestamp"])
        self.assertEqual(all_events_request["startTimestamp"], -2208988800.0)
        self.assertEqual(all_events_request["endTimestamp"], 4102444800.0)

    def test_show_rejects_ambiguous_identifier_args(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = self.read_module.main(
                ["show", "--fixture", str(self.fixture), "--event-id", "event-example", "--calendar-item-id", "item-example"]
            )
        self.assertEqual(rc, 1)
        self.assertIn("exactly one", stderr.getvalue())

    def test_export_requires_days_or_all(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = self.read_module.main(["export", "--fixture", str(self.fixture)])
        self.assertEqual(rc, 1)
        self.assertIn("export requires", stderr.getvalue())


class AppleCalendarWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.write_module = load_module("apple_calendar_write", WRITE_SCRIPT)
        self.tmp = tempfile.TemporaryDirectory()
        self.payload = Path(self.tmp.name) / "event.json"
        self.payload.write_text(
            json.dumps(
                {
                    "title": "Example Planning",
                    "calendar_id": "calendar-example",
                    "start": "2026-06-25 10:00",
                    "duration_min": 30,
                    "location": "Example Room",
                    "availability": "busy",
                    "alarms": [{"relative_offset_minutes": -15}],
                    "recurrence": {"frequency": "weekly", "interval": 1, "occurrence_count": 4},
                }
            ),
            encoding="utf-8",
        )
        self.patch_payload = Path(self.tmp.name) / "patch.json"
        self.patch_payload.write_text(json.dumps({"title": "Example Planning Updated", "clear_notes": True}), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_create_dry_run_and_confirm_request_flags(self) -> None:
        requests: list[dict] = []

        def fake_bridge(request, fixture=None):
            requests.append(request)
            return {"operation": "create", "saved": request["confirm"], "event": synthetic_event()}

        with patch.object(self.write_module, "run_bridge", side_effect=fake_bridge), redirect_stdout(io.StringIO()):
            self.assertEqual(self.write_module.main(["create", "--payload", str(self.payload)]), 0)
            self.assertEqual(self.write_module.main(["create", "--payload", str(self.payload), "--confirm"]), 0)

        self.assertEqual(requests[0]["operation"], "create")
        self.assertFalse(requests[0]["confirm"])
        self.assertTrue(requests[1]["confirm"])
        self.assertEqual(requests[0]["calendarIdentifier"], "calendar-example")
        self.assertEqual(requests[0]["recurrence"]["frequency"], "weekly")
        self.assertEqual(requests[0]["recurrence"]["occurrenceCount"], 4)

    def test_update_sets_identifier_span_occurrence_and_patch_fields(self) -> None:
        requests: list[dict] = []

        def fake_bridge(request, fixture=None):
            requests.append(request)
            return {"operation": "update", "saved": request["confirm"], "before": synthetic_event(), "event": synthetic_event("Example Planning Updated")}

        with patch.object(self.write_module, "run_bridge", side_effect=fake_bridge), redirect_stdout(io.StringIO()):
            rc = self.write_module.main(
                [
                    "update",
                    "--event-id",
                    "event-example",
                    "--payload",
                    str(self.patch_payload),
                    "--span",
                    "future",
                    "--occurrence-start",
                    "2026-06-25 10:00",
                    "--confirm",
                ]
            )

        self.assertEqual(rc, 0)
        self.assertTrue(requests[0]["confirm"])
        self.assertEqual(requests[0]["eventIdentifier"], "event-example")
        self.assertEqual(requests[0]["span"], "future")
        self.assertIn("occurrenceStartTimestamp", requests[0])
        self.assertEqual(requests[0]["title"], "Example Planning Updated")
        self.assertTrue(requests[0]["clearNotes"])

    def test_delete_dry_run_and_confirm_request_flags(self) -> None:
        requests: list[dict] = []

        def fake_bridge(request, fixture=None):
            requests.append(request)
            return {"operation": "delete", "saved": request["confirm"], "event": synthetic_event()}

        with patch.object(self.write_module, "run_bridge", side_effect=fake_bridge), redirect_stdout(io.StringIO()):
            self.assertEqual(self.write_module.main(["delete", "--calendar-item-id", "item-example"]), 0)
            self.assertEqual(
                self.write_module.main(
                    [
                        "delete",
                        "--calendar-item-id",
                        "item-example",
                        "--span",
                        "this",
                        "--occurrence-start",
                        "2026-06-25 10:00",
                        "--confirm",
                    ]
                ),
                0,
            )

        self.assertFalse(requests[0]["confirm"])
        self.assertTrue(requests[1]["confirm"])
        self.assertEqual(requests[0]["calendarItemIdentifier"], "item-example")
        self.assertEqual(requests[1]["span"], "this")
        self.assertIn("occurrenceStartTimestamp", requests[1])

    def test_update_and_delete_reject_ambiguous_identifiers(self) -> None:
        for command in ("update", "delete"):
            argv = [command, "--event-id", "event-example", "--calendar-item-id", "item-example"]
            if command == "update":
                argv.extend(["--payload", str(self.patch_payload)])
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = self.write_module.main(argv)
            self.assertEqual(rc, 1)
            self.assertIn("exactly one", stderr.getvalue())

    def test_attendee_mutation_is_rejected(self) -> None:
        payload = Path(self.tmp.name) / "attendees.json"
        payload.write_text(
            json.dumps({"title": "Example Planning", "calendar_id": "calendar-example", "start": "2026-06-25 10:00", "attendees": []}),
            encoding="utf-8",
        )
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = self.write_module.main(["create", "--payload", str(payload)])
        self.assertEqual(rc, 1)
        self.assertIn("attendee/organizer mutation is not supported", stderr.getvalue())

    def test_organizer_mutation_is_rejected(self) -> None:
        payload = Path(self.tmp.name) / "organizer.json"
        payload.write_text(
            json.dumps({"title": "Example Planning", "calendar_id": "calendar-example", "start": "2026-06-25 10:00", "organizer": {}}),
            encoding="utf-8",
        )
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = self.write_module.main(["create", "--payload", str(payload)])
        self.assertEqual(rc, 1)
        self.assertIn("attendee/organizer mutation is not supported", stderr.getvalue())

    def test_invalid_duration_is_a_compact_cli_error(self) -> None:
        payload = Path(self.tmp.name) / "invalid-duration.json"
        payload.write_text(
            json.dumps(
                {
                    "title": "Example Planning",
                    "calendar_id": "calendar-example",
                    "start": "2026-06-25 10:00",
                    "duration_min": "thirty",
                }
            ),
            encoding="utf-8",
        )
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = self.write_module.main(["create", "--payload", str(payload)])

        self.assertEqual(1, rc)
        self.assertIn("duration_min must be an integer", stderr.getvalue())

    def test_unknown_fields_wrapped_siblings_and_noop_updates_are_rejected(self) -> None:
        unknown_payload = Path(self.tmp.name) / "unknown.json"
        unknown_payload.write_text(
            json.dumps({"title": "Example Planning", "calendar_id": "calendar-example", "start": "2026-06-25 10:00", "titel": "Typo"}),
            encoding="utf-8",
        )
        sibling_payload = Path(self.tmp.name) / "sibling.json"
        sibling_payload.write_text(json.dumps({"event": {"title": "Example Planning"}, "extra": True}), encoding="utf-8")
        noop_payload = Path(self.tmp.name) / "noop.json"
        noop_payload.write_text(json.dumps({"duration_min": 30}), encoding="utf-8")

        cases = [
            (["create", "--payload", str(unknown_payload)], "unknown event payload field"),
            (["create", "--payload", str(sibling_payload)], "wrapped payload must not include"),
            (["update", "--event-id", "event-example", "--payload", str(noop_payload)], "at least one supported field change"),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    rc = self.write_module.main(argv)
                self.assertEqual(rc, 1)
                self.assertIn(expected, stderr.getvalue())

    def test_rsvp_response_is_explicitly_unsupported(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = self.write_module.main(["respond", "--event-id", "event-example", "--response", "accept"])

        self.assertEqual(rc, 1)
        self.assertIn("scripted RSVP responses are unsupported", stderr.getvalue())

    def test_bridge_recurring_update_guard_uses_pre_change_state(self) -> None:
        bridge = (SCRIPT_DIR / "AppleCalendarBridge.swift").read_text(encoding="utf-8")
        update_start = bridge.index("func commandUpdate")
        update_end = bridge.index("func commandDelete", update_start)
        update_body = bridge[update_start:update_end]

        was_recurring_pos = update_body.index("let wasRecurring = !(event.recurrenceRules ?? []).isEmpty")
        apply_changes_pos = update_body.index("applyChanges(to: event")
        guard_pos = update_body.index("if confirmed && wasRecurring")

        self.assertLess(was_recurring_pos, apply_changes_pos)
        self.assertLess(apply_changes_pos, guard_pos)


class AppleCalendarBridgePackagingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge_module = load_module(
            "apple_calendar_bridge_packaging",
            SCRIPT_DIR / "apple_calendar_lib.py",
        )
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache_dir = Path(self.temporary.name)
        self.bundle = self.cache_dir / "RundeskAppleCalendar.app"
        self.launcher = self.bundle / "Contents" / "MacOS" / "rundesk-apple-calendar-launcher"
        self.binary = self.cache_dir / "apple-calendar-eventkit"
        self.source = self.cache_dir / "AppleCalendarBridge.swift"
        self.source.write_bytes(self.bridge_module.BRIDGE_SOURCE.read_bytes())

    def signing_details(self, path: Path) -> str:
        completed = subprocess.run(
            ["/usr/bin/codesign", "-dvv", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        return completed.stdout + completed.stderr

    def test_rebuild_has_embedded_privacy_description_and_stable_identity(self) -> None:
        with (
            patch.object(self.bridge_module, "CACHE_DIR", self.cache_dir),
            patch.object(self.bridge_module, "BRIDGE_BUNDLE", self.bundle),
            patch.object(self.bridge_module, "BRIDGE_LAUNCHER_BINARY", self.launcher),
            patch.object(self.bridge_module, "BRIDGE_BINARY", self.binary),
            patch.object(self.bridge_module, "BRIDGE_SOURCE", self.source),
        ):
            self.assertEqual(self.binary, self.bridge_module.ensure_bridge_binary())
            first_launcher_digest = hashlib.sha256(self.launcher.read_bytes()).hexdigest()
            first_launcher_details = self.signing_details(self.launcher)
            first_worker_details = self.signing_details(self.binary)
            self.source.write_text(
                self.source.read_text(encoding="utf-8") + "\n// synthetic catalog update\n",
                encoding="utf-8",
            )
            self.assertEqual(self.binary, self.bridge_module.ensure_bridge_binary())
            second_launcher_digest = hashlib.sha256(self.launcher.read_bytes()).hexdigest()
            second_launcher_details = self.signing_details(self.launcher)
            second_worker_details = self.signing_details(self.binary)

        self.assertEqual(first_launcher_digest, second_launcher_digest)
        for details in (first_launcher_details, second_launcher_details):
            self.assertIn("Identifier=ai.rundesk.apple-calendar.eventkit", details)
            self.assertIn("Info.plist entries=", details)
        for details in (first_worker_details, second_worker_details):
            self.assertIn("Identifier=ai.rundesk.apple-calendar.eventkit.worker", details)
        strings = subprocess.run(
            ["/usr/bin/strings", str(self.launcher)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertIn("NSCalendarsFullAccessUsageDescription", strings)
        self.assertIn("NSCalendarsUsageDescription", strings)
        self.assertEqual(
            0,
            subprocess.run(
                ["/usr/bin/codesign", "--verify", "--strict", str(self.bundle)],
                capture_output=True,
                check=False,
            ).returncode,
        )


class AppleCalendarLiveSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        if os.environ.get("APPLE_CALENDAR_LIVE_TESTS") != "1":
            self.skipTest("set APPLE_CALENDAR_LIVE_TESTS=1 to run live Calendar safety tests")
        self.calendar_id = os.environ.get("APPLE_CALENDAR_TEST_CALENDAR_ID")
        if not self.calendar_id:
            self.skipTest("set APPLE_CALENDAR_TEST_CALENDAR_ID to a disposable writable calendar id")
        self.write_script = str(WRITE_SCRIPT)
        self.read_script = str(READ_SCRIPT)

    def run_tool(self, *args: str) -> str:
        result = subprocess.run([*args], cwd=SCRIPT_DIR, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            raise AssertionError(f"command failed: {result.stderr.strip() or result.stdout.strip()}")
        return result.stdout

    def test_live_create_update_delete_synthetic_event(self) -> None:
        marker = f"apple-calendar synthetic live test {os.getpid()}"
        with tempfile.TemporaryDirectory() as tmp:
            create_payload = Path(tmp) / "create.json"
            create_payload.write_text(
                json.dumps(
                    {
                        "title": marker,
                        "calendar_id": self.calendar_id,
                        "start": "2099-01-01 09:00",
                        "duration_min": 30,
                        "location": "Synthetic Room",
                    }
                ),
                encoding="utf-8",
            )
            created = self.run_tool(self.write_script, "create", "--payload", str(create_payload), "--confirm")
            event_id = next(part.removeprefix("event_id=") for part in created.split(" | ") if part.startswith("event_id="))
            self.addCleanup(self.run_tool, self.write_script, "delete", "--event-id", event_id, "--confirm")

            patch_payload = Path(tmp) / "patch.json"
            patch_payload.write_text(json.dumps({"title": f"{marker} updated"}), encoding="utf-8")
            self.run_tool(self.write_script, "update", "--event-id", event_id, "--payload", str(patch_payload), "--confirm")
            shown = self.run_tool(self.read_script, "show", "--event-id", event_id)
            self.assertIn(f"{marker} updated", shown)


if __name__ == "__main__":
    unittest.main()
