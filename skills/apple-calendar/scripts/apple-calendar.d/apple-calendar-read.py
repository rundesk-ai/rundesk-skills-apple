#!/usr/bin/env python3
"""
Read local Apple Calendar.app data through EventKit for workspace agents.

Usage:
  apple-calendar read status [--json]
  apple-calendar read sources [--source QUERY] [--type TYPE] [--json]
  apple-calendar read calendars [--source QUERY] [--calendar QUERY] [--writable] [--json]
  apple-calendar read events [--today|--tomorrow|--future-only] [--days N] [--full] [--json]
  apple-calendar read search "term" [--days N] [--full] [--json]
  apple-calendar read show --event-id ID [--json]
  apple-calendar read availability [--today|--days N] [--full] [--json]
  apple-calendar read export --days N --json

Inputs:
  Reads Calendar.app / iCloud calendar data through EventKit. Override bridge
  responses with --fixture for synthetic offline tests only.

Outputs:
  Prints compact agenda text by default. Use --full for text rows with IDs and
  operational fields. Use --json for structured payloads with schema_version,
  generated_at, calendar/source metadata, event identifiers, attendees, alarms,
  recurrence summaries, and counts. This script never mutates calendars or events.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from datetime import datetime

from apple_calendar_lib import (
    AVAILABILITY_VALUES,
    DEFAULT_DAYS,
    DEFAULT_LIMIT,
    SCHEMA_VERSION,
    TYPE_VALUES,
    AppleCalendarError,
    build_bridge_request,
    date_range_from_args,
    events_payload,
    filtered_calendars,
    filtered_events,
    filtered_sources,
    generated_at,
    positive_days,
    positive_limit,
    print_availability,
    print_calendars,
    print_event_card,
    print_events,
    print_json,
    print_sources,
    run_bridge,
)


def bridge_sources(args: argparse.Namespace) -> list[dict]:
    payload = run_bridge(build_bridge_request("sources"), args.fixture)
    sources = payload.get("sources", [])
    if not isinstance(sources, list):
        raise AppleCalendarError("bridge sources payload is malformed")
    return sources


def bridge_calendars(args: argparse.Namespace) -> list[dict]:
    payload = run_bridge(build_bridge_request("calendars"), args.fixture)
    calendars = payload.get("calendars", [])
    if not isinstance(calendars, list):
        raise AppleCalendarError("bridge calendars payload is malformed")
    return calendars


def bridge_events(args: argparse.Namespace, start, end) -> list[dict]:
    payload = run_bridge(
        build_bridge_request(
            "events",
            startTimestamp=start.timestamp(),
            endTimestamp=end.timestamp(),
            includeDetails=True,
        ),
        args.fixture,
    )
    events = payload.get("events", [])
    if not isinstance(events, list):
        raise AppleCalendarError("bridge events payload is malformed")
    return events


def command_status(args: argparse.Namespace) -> int:
    payload = run_bridge(build_bridge_request("status"), args.fixture)
    output = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at(),
        "status": payload.get("status", "ok"),
        "sources": payload.get("sources", 0),
        "calendars": payload.get("calendars", 0),
        "writable_calendars": payload.get("writableCalendars", 0),
    }
    if args.json:
        print_json(output)
    else:
        print(
            "Apple Calendar access ok | "
            f"sources={output['sources']} | calendars={output['calendars']} | "
            f"writable_calendars={output['writable_calendars']}"
        )
    return 0


def command_sources(args: argparse.Namespace) -> int:
    sources = filtered_sources(bridge_sources(args), args)
    if args.json:
        print_json({"schema_version": SCHEMA_VERSION, "generated_at": generated_at(), "count": len(sources), "sources": sources})
    else:
        print_sources(sources)
    return 0


def command_calendars(args: argparse.Namespace) -> int:
    calendars = filtered_calendars(bridge_calendars(args), args)
    if args.json:
        print_json({"schema_version": SCHEMA_VERSION, "generated_at": generated_at(), "count": len(calendars), "calendars": calendars})
    else:
        print_calendars(calendars)
    return 0


def command_events(args: argparse.Namespace) -> int:
    start, end = date_range_from_args(args)
    events = filtered_events(bridge_events(args, start, end), args)
    if args.json:
        print_json(events_payload("events", start, end, events))
    else:
        print_events(f"Apple Calendar events | range={start.isoformat()}..{end.isoformat()}", events, full=args.full)
    return 0


def command_search(args: argparse.Namespace) -> int:
    start, end = date_range_from_args(args)
    events = filtered_events(bridge_events(args, start, end), args)
    if args.json:
        print_json(events_payload("search", start, end, events))
    else:
        print_events(f"Apple Calendar search | term={args.term}", events, full=args.full)
    return 0


def command_show(args: argparse.Namespace) -> int:
    if bool(args.event_id) == bool(args.calendar_item_id):
        raise AppleCalendarError("show requires exactly one of --event-id or --calendar-item-id")
    payload = run_bridge(
        build_bridge_request("show", eventIdentifier=args.event_id, calendarItemIdentifier=args.calendar_item_id),
        args.fixture,
    )
    event = payload.get("event")
    if not isinstance(event, dict):
        raise AppleCalendarError("bridge show payload is malformed")
    if args.json:
        print_json({"schema_version": SCHEMA_VERSION, "generated_at": generated_at(), "event": event})
    else:
        print_event_card(event)
    return 0


def command_availability(args: argparse.Namespace) -> int:
    start, end = date_range_from_args(args)
    events = [
        event
        for event in filtered_events(bridge_events(args, start, end), args)
        if event.get("availability") in AVAILABILITY_VALUES and event.get("availability") != "free"
    ]
    if args.json:
        print_json(events_payload("availability", start, end, events))
    else:
        print_availability(events, full=args.full)
    return 0


def command_export(args: argparse.Namespace) -> int:
    if not args.all and args.days is None and not args.from_date:
        raise AppleCalendarError("export requires --days N, --from YYYY-MM-DD, or explicit --all")
    if args.all:
        start = datetime.fromisoformat("1900-01-01T00:00:00+00:00")
        end = datetime.fromisoformat("2100-01-01T00:00:00+00:00")
    else:
        start, end = date_range_from_args(args)
    calendars = filtered_calendars(bridge_calendars(args), args)
    events = filtered_events(bridge_events(args, start, end), args)
    payload = events_payload("export", start, end, events)
    payload["calendars"] = calendars
    payload["counts"] = {"calendars": len(calendars), "events": len(events)}
    if args.json:
        print_json(payload)
    else:
        print(f"Apple Calendar export | calendars={len(calendars)} | events={len(events)} | start={start.isoformat()} | end={end.isoformat()}")
    return 0


def add_fixture(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fixture", help=argparse.SUPPRESS)


def add_filter_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-id", help="Exact EventKit sourceIdentifier.")
    parser.add_argument("--source", action="append", help="Case-insensitive source/account substring. Repeat or comma-separate.")
    parser.add_argument("--calendar-id", help="Exact EventKit calendarIdentifier.")
    parser.add_argument("--calendar", action="append", help="Case-insensitive calendar/source/id substring. Repeat or comma-separate.")
    parser.add_argument("--type", choices=TYPE_VALUES, help="Calendar/source type filter.")
    parser.add_argument("--writable", action="store_true", help="Only include writable calendars/events.")


def add_date_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--from", dest="from_date", help="Range start: YYYY-MM-DD or YYYY-MM-DD HH:MM.")
    parser.add_argument("--to", dest="to_date", help="Exclusive range end: YYYY-MM-DD or YYYY-MM-DD HH:MM.")
    parser.add_argument("--days", type=positive_days, default=DEFAULT_DAYS, help=f"Days to scan. Default: {DEFAULT_DAYS}.")
    parser.add_argument("--today", action="store_true", help="Use today.")
    parser.add_argument("--tomorrow", action="store_true", help="Use tomorrow.")
    parser.add_argument("--future-only", action="store_true", help="Start at now instead of local midnight.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read local Apple Calendar.app data through EventKit.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Examples use synthetic data only:
              apple-calendar read calendars --writable
              apple-calendar read events --today
              apple-calendar read search "Example planning" --days 14
              apple-calendar read show --event-id EVENT_ID
            """
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Verify EventKit read access.")
    add_fixture(status)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)

    sources = subparsers.add_parser("sources", help="List EventKit sources/accounts.")
    add_fixture(sources)
    sources.add_argument("--source-id")
    sources.add_argument("--source", action="append")
    sources.add_argument("--type", choices=TYPE_VALUES)
    sources.add_argument("--json", action="store_true")
    sources.set_defaults(func=command_sources)

    calendars = subparsers.add_parser("calendars", help="List calendars with source/account metadata.")
    add_fixture(calendars)
    add_filter_options(calendars)
    calendars.add_argument("--json", action="store_true")
    calendars.set_defaults(func=command_calendars)

    events = subparsers.add_parser("events", help="List bounded events.")
    add_fixture(events)
    add_filter_options(events)
    add_date_options(events)
    events.add_argument("--query", help="Search title, location, notes, attendees, organizer, URL, and calendar metadata.")
    events.add_argument("--limit", type=positive_limit, default=DEFAULT_LIMIT)
    events.add_argument("--full", action="store_true", help="Include event IDs and operational fields in text output.")
    events.add_argument("--json", action="store_true")
    events.set_defaults(func=command_events)

    search = subparsers.add_parser("search", help="Search bounded events.")
    add_fixture(search)
    add_filter_options(search)
    add_date_options(search)
    search.add_argument("term")
    search.add_argument("--limit", type=positive_limit, default=DEFAULT_LIMIT)
    search.add_argument("--full", action="store_true", help="Include event IDs and operational fields in text output.")
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=command_search)

    show = subparsers.add_parser("show", help="Show one full event card.")
    add_fixture(show)
    show.add_argument("--event-id")
    show.add_argument("--calendar-item-id")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=command_show)

    availability = subparsers.add_parser("availability", help="List busy/tentative/unavailable event blocks.")
    add_fixture(availability)
    add_filter_options(availability)
    add_date_options(availability)
    availability.add_argument("--query")
    availability.add_argument("--limit", type=positive_limit, default=DEFAULT_LIMIT)
    availability.add_argument("--full", action="store_true", help="Include event IDs and operational fields in text output.")
    availability.add_argument("--json", action="store_true")
    availability.set_defaults(func=command_availability)

    export = subparsers.add_parser("export", help="Export bounded Calendar data.")
    add_fixture(export)
    add_filter_options(export)
    export.add_argument("--from", dest="from_date")
    export.add_argument("--to", dest="to_date")
    group = export.add_mutually_exclusive_group(required=False)
    group.add_argument("--days", type=positive_days, help="Only export events newer than this many days.")
    group.add_argument("--all", action="store_true", help="Explicitly export all accessible history.")
    export.add_argument("--limit", type=positive_limit)
    export.add_argument("--json", action="store_true")
    export.set_defaults(func=command_export)

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
