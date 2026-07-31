#!/usr/bin/env python3
"""
Safely create, update, and delete Apple Calendar.app events through EventKit.

Usage:
  apple-calendar write status [--json]
  apple-calendar write create --payload event.json [--confirm] [--json]
  apple-calendar write update --event-id ID --payload patch.json [--span this|future] [--occurrence-start START] [--confirm] [--json]
  apple-calendar write delete --event-id ID [--span this|future] [--occurrence-start START] [--confirm] [--json]
  apple-calendar write respond --event-id ID --response accept|tentative|decline

Inputs:
  Reads JSON event payload files and sends requests to the bundled Swift
  EventKit bridge. Payload examples must be synthetic only.

Outputs:
  Mutations are dry-runs by default and print the target event/calendar summary.
  Use --json for structured before/after payloads. This script never writes
  Calendar SQLite directly; confirmed writes go through EventKit.
  Confirmed recurring updates/deletes require explicit --span and
  --occurrence-start copied from read output.
"""

from __future__ import annotations

import argparse
import sys
import textwrap

from apple_calendar_lib import (
    SCHEMA_VERSION,
    SPAN_VALUES,
    AppleCalendarError,
    bridge_request_from_event_payload,
    build_bridge_request,
    generated_at,
    load_event_payload,
    parse_local_datetime,
    print_json,
    print_mutation_result,
    run_bridge,
)


def command_status(args: argparse.Namespace) -> int:
    payload = run_bridge(build_bridge_request("status"), args.fixture)
    output = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at(),
        "status": payload.get("status", "ok"),
        "sources": payload.get("sources", 0),
        "calendars": payload.get("calendars", 0),
        "writable_calendars": payload.get("writableCalendars", 0),
        "dry_run_default": True,
    }
    if args.json:
        print_json(output)
    else:
        print(
            "Apple Calendar write access ok | "
            f"sources={output['sources']} | calendars={output['calendars']} | "
            f"writable_calendars={output['writable_calendars']} | dry_run_default=true"
        )
    return 0


def command_create(args: argparse.Namespace) -> int:
    event = load_event_payload(args.payload)
    request = bridge_request_from_event_payload("create", event, confirm=args.confirm)
    payload = run_bridge(request, args.fixture)
    if args.json:
        print_json({"schema_version": SCHEMA_VERSION, "generated_at": generated_at(), **payload})
    else:
        print_mutation_result("create", payload, args.confirm)
    return 0


def command_update(args: argparse.Namespace) -> int:
    if bool(args.event_id) == bool(args.calendar_item_id):
        raise AppleCalendarError("update requires exactly one of --event-id or --calendar-item-id")
    event = load_event_payload(args.payload)
    request = bridge_request_from_event_payload("update", event, confirm=args.confirm)
    request["eventIdentifier"] = args.event_id
    request["calendarItemIdentifier"] = args.calendar_item_id
    if args.span:
        request["span"] = args.span
    if args.occurrence_start:
        request["occurrenceStartTimestamp"] = parse_local_datetime(args.occurrence_start).timestamp()
    payload = run_bridge(request, args.fixture)
    if args.json:
        print_json({"schema_version": SCHEMA_VERSION, "generated_at": generated_at(), **payload})
    else:
        print_mutation_result("update", payload, args.confirm)
    return 0


def command_delete(args: argparse.Namespace) -> int:
    if bool(args.event_id) == bool(args.calendar_item_id):
        raise AppleCalendarError("delete requires exactly one of --event-id or --calendar-item-id")
    request = build_bridge_request(
        "delete",
        confirm=args.confirm,
        eventIdentifier=args.event_id,
        calendarItemIdentifier=args.calendar_item_id,
    )
    if args.span:
        request["span"] = args.span
    if args.occurrence_start:
        request["occurrenceStartTimestamp"] = parse_local_datetime(args.occurrence_start).timestamp()
    payload = run_bridge(request, args.fixture)
    if args.json:
        print_json({"schema_version": SCHEMA_VERSION, "generated_at": generated_at(), **payload})
    else:
        print_mutation_result("delete", payload, args.confirm)
    return 0


def command_respond(args: argparse.Namespace) -> int:
    if bool(args.event_id) == bool(args.calendar_item_id):
        raise AppleCalendarError("respond requires exactly one of --event-id or --calendar-item-id")
    raise AppleCalendarError(
        "scripted RSVP responses are unsupported by this Mac's EventKit/Calendar AppleScript surface; "
        "use apple-calendar-read.py show to inspect attendee status and respond manually in Calendar.app"
    )


def add_fixture(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fixture", help=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely mutate Apple Calendar.app events through EventKit.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Synthetic payload example:
              {
                "title": "Example Planning",
                "calendar_id": "CALENDAR_ID",
                "start": "2026-06-25 10:00",
                "duration_min": 30,
                "location": "Example Room"
              }
            """
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Verify EventKit write access without mutating data.")
    add_fixture(status)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)

    create = subparsers.add_parser("create", help="Create an event. Dry-run unless --confirm is passed.")
    add_fixture(create)
    create.add_argument("--payload", required=True, help="Synthetic event JSON payload file.")
    create.add_argument("--confirm", action="store_true", help="Actually save the event.")
    create.add_argument("--json", action="store_true")
    create.set_defaults(func=command_create)

    update = subparsers.add_parser("update", help="Update an event. Dry-run unless --confirm is passed.")
    add_fixture(update)
    update.add_argument("--event-id")
    update.add_argument("--calendar-item-id")
    update.add_argument("--payload", required=True, help="Synthetic event patch JSON payload file.")
    update.add_argument("--span", choices=SPAN_VALUES, help="Recurring-event span. Required with --confirm for recurring events.")
    update.add_argument("--occurrence-start", help="Occurrence start from read output. Required with --confirm for recurring events.")
    update.add_argument("--confirm", action="store_true", help="Actually save the update.")
    update.add_argument("--json", action="store_true")
    update.set_defaults(func=command_update)

    delete = subparsers.add_parser("delete", help="Delete an event. Dry-run unless --confirm is passed.")
    add_fixture(delete)
    delete.add_argument("--event-id")
    delete.add_argument("--calendar-item-id")
    delete.add_argument("--span", choices=SPAN_VALUES, help="Recurring-event span. Required with --confirm for recurring events.")
    delete.add_argument("--occurrence-start", help="Occurrence start from read output. Required with --confirm for recurring events.")
    delete.add_argument("--confirm", action="store_true", help="Actually delete the event.")
    delete.add_argument("--json", action="store_true")
    delete.set_defaults(func=command_delete)

    respond = subparsers.add_parser("respond", help="Report that scripted RSVP responses are unsupported in v1.")
    respond.add_argument("--event-id")
    respond.add_argument("--calendar-item-id")
    respond.add_argument("--response", choices=("accept", "tentative", "decline"), required=True)
    respond.add_argument("--confirm", action="store_true", help="Accepted for interface clarity; RSVP is still unsupported.")
    respond.add_argument("--json", action="store_true")
    respond.set_defaults(func=command_respond)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AppleCalendarError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
