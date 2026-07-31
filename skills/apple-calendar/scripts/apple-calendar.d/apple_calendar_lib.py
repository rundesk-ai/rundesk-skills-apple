from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCHEMA_VERSION = "1.0"
SCRIPT_DIR = Path(__file__).resolve().parent
BRIDGE_SOURCE = SCRIPT_DIR / "AppleCalendarBridge.swift"


def _cache_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "rundesk-scripts" / "apple-calendar"


CACHE_DIR = _cache_dir()
BRIDGE_BINARY = CACHE_DIR / "apple-calendar-eventkit"
DEFAULT_DAYS = 30
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
MAX_DAYS = 36500
CALENDAR_TYPES = ("local", "caldav", "exchange", "subscription", "birthday")
SOURCE_TYPES = ("local", "exchange", "caldav", "mobileme", "subscribed", "birthdays")
TYPE_VALUES = tuple(sorted(set(CALENDAR_TYPES) | set(SOURCE_TYPES)))
AVAILABILITY_VALUES = ("busy", "free", "tentative", "unavailable")
SPAN_VALUES = ("this", "future")
EVENT_ALLOWED_FIELDS = {
    "alarms",
    "all_day",
    "availability",
    "calendarIdentifier",
    "calendar_id",
    "clear_alarms",
    "clear_location",
    "clear_notes",
    "clear_recurrence",
    "clear_url",
    "duration_min",
    "duration_minutes",
    "end",
    "isAllDay",
    "is_all_day",
    "location",
    "notes",
    "recurrence",
    "start",
    "title",
    "url",
}
EVENT_UNSUPPORTED_FIELDS = {"attendees", "organizer"}
EVENT_STRUCTURAL_FIELDS = {"duration_min", "duration_minutes"}


class AppleCalendarError(RuntimeError):
    pass


def generated_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_tz() -> timezone:
    tz_name = os.environ.get("TZ")
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass

    localtime = Path("/etc/localtime")
    try:
        target = localtime.resolve()
        marker = "zoneinfo/"
        if marker in str(target):
            return ZoneInfo(str(target).split(marker, 1)[1])
    except Exception:
        pass

    tz = datetime.now().astimezone().tzinfo
    return tz if tz is not None else timezone.utc


def text(value: Any, fallback: str = "-") -> str:
    if value is None:
        return fallback
    value = str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ").strip()
    return value if value else fallback


def truncate(value: Any, limit: int = 180) -> str:
    value = text(value)
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def bounded_int(value: str, *, minimum: int, maximum: int, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise argparse.ArgumentTypeError(f"{label} must be between {minimum} and {maximum}")
    return parsed


def positive_limit(value: str) -> int:
    return bounded_int(value, minimum=1, maximum=MAX_LIMIT, label="limit")


def positive_days(value: str) -> int:
    return bounded_int(value, minimum=1, maximum=MAX_DAYS, label="days")


def parse_local_datetime(value: str) -> datetime:
    raw = value.strip()
    if raw.lower() == "now":
        return datetime.now().astimezone()

    normalized = raw.replace("Z", "+00:00")
    try:
        if len(normalized) == 10:
            parsed_date = date.fromisoformat(normalized)
            return datetime.combine(parsed_date, time.min, tzinfo=local_tz())
        parsed = datetime.fromisoformat(normalized.replace(" ", "T", 1))
    except ValueError as exc:
        raise AppleCalendarError(f"invalid date/time {value!r}; use YYYY-MM-DD or YYYY-MM-DD HH:MM") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_tz())
    return parsed.astimezone()


def parse_bridge_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def iso_local(value: datetime) -> str:
    return value.astimezone().isoformat(timespec="seconds")


def date_range_from_args(args: argparse.Namespace, *, default_days: int = DEFAULT_DAYS) -> tuple[datetime, datetime]:
    now = datetime.now().astimezone()
    selectors = [
        bool(getattr(args, "from_date", None) or getattr(args, "to_date", None)),
        bool(getattr(args, "today", False)),
        bool(getattr(args, "tomorrow", False)),
    ]
    if sum(selectors) > 1:
        raise AppleCalendarError("choose one date scope: explicit range, today, tomorrow, or future")

    if getattr(args, "from_date", None) or getattr(args, "to_date", None):
        if not getattr(args, "from_date", None):
            raise AppleCalendarError("--from is required when --to is used")
        start = parse_local_datetime(args.from_date)
        end = parse_local_datetime(args.to_date) if args.to_date else start + timedelta(days=args.days)
    elif getattr(args, "today", False):
        start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        end = start + timedelta(days=1)
    elif getattr(args, "tomorrow", False):
        tomorrow = now.date() + timedelta(days=1)
        start = datetime.combine(tomorrow, time.min, tzinfo=now.tzinfo)
        end = start + timedelta(days=1)
    else:
        start = now if getattr(args, "future_only", False) else datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        end = start + timedelta(days=getattr(args, "days", default_days))

    if end <= start:
        raise AppleCalendarError("date range end must be after start")
    return start, end


def event_start_end_for_payload(event: dict[str, Any], *, require_start: bool) -> tuple[datetime | None, datetime | None]:
    start_raw = event.get("start")
    end_raw = event.get("end")
    if require_start and not start_raw:
        raise AppleCalendarError("event payload requires start")

    if not start_raw:
        return None, parse_local_datetime(str(end_raw)) if end_raw else None

    start = parse_local_datetime(str(start_raw))
    if event.get("all_day") or event.get("is_all_day") or event.get("isAllDay"):
        start = datetime.combine(start.date(), time.min, tzinfo=start.tzinfo)
        if end_raw:
            end = parse_local_datetime(str(end_raw))
            end = datetime.combine(end.date(), time.min, tzinfo=end.tzinfo)
        else:
            end = start + timedelta(days=1)
    elif end_raw:
        end = parse_local_datetime(str(end_raw))
    else:
        duration = int(event.get("duration_min") or event.get("duration_minutes") or 60)
        if duration <= 0:
            raise AppleCalendarError("duration_min must be positive")
        end = start + timedelta(minutes=duration)

    if end <= start:
        raise AppleCalendarError("event end must be after start")
    return start, end


def bridge_source_hash() -> str:
    return hashlib.sha256(BRIDGE_SOURCE.read_bytes()).hexdigest()[:12]


def ensure_bridge_binary() -> Path:
    if not BRIDGE_SOURCE.is_file():
        raise AppleCalendarError(f"EventKit bridge source not found: {BRIDGE_SOURCE}")

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
            raise AppleCalendarError(f"failed to compile EventKit bridge: {result.stderr.strip() or result.stdout.strip()}")
        tmp_binary.replace(BRIDGE_BINARY)
        for old_stamp in CACHE_DIR.glob(f"{BRIDGE_BINARY.name}.*.stamp"):
            old_stamp.unlink(missing_ok=True)
        stamp.write_text("", encoding="utf-8")
        return BRIDGE_BINARY
    finally:
        tmp_binary.unlink(missing_ok=True)


def run_bridge(request: dict[str, Any], fixture: str | None = None) -> dict[str, Any]:
    if fixture:
        path = Path(fixture).expanduser()
        if not path.is_file():
            raise AppleCalendarError(f"fixture not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise AppleCalendarError("fixture must contain a JSON object")
        return payload

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
        raise AppleCalendarError(f"EventKit bridge failed: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AppleCalendarError(f"EventKit bridge returned non-JSON output: {result.stdout[:500]!r}") from exc
    if not isinstance(payload, dict):
        raise AppleCalendarError("EventKit bridge returned a non-object JSON payload")
    return payload


def build_bridge_request(operation: str, **values: Any) -> dict[str, Any]:
    request = {"operation": operation}
    request.update({key: value for key, value in values.items() if value is not None})
    return request


def split_filters(values: list[str] | None) -> list[str]:
    output: list[str] = []
    for value in values or []:
        output.extend(part.strip().lower() for part in value.split(",") if part.strip())
    return output


def calendar_haystack(calendar: dict[str, Any]) -> str:
    source = calendar.get("source") if isinstance(calendar.get("source"), dict) else {}
    return " ".join(
        text(value, "")
        for value in (
            calendar.get("title"),
            calendar.get("calendarIdentifier"),
            calendar.get("type"),
            calendar.get("sourceTitle"),
            calendar.get("sourceType"),
            calendar.get("sourceIdentifier"),
            source.get("title"),
            source.get("sourceIdentifier"),
            source.get("type"),
        )
    ).lower()


def source_haystack(source: dict[str, Any]) -> str:
    return " ".join(
        text(value, "")
        for value in (
            source.get("title"),
            source.get("sourceIdentifier"),
            source.get("type"),
        )
    ).lower()


def event_haystack(event: dict[str, Any]) -> str:
    calendar = event.get("calendar") if isinstance(event.get("calendar"), dict) else {}
    organizer = event.get("organizer") if isinstance(event.get("organizer"), dict) else {}
    attendees = event.get("attendees") if isinstance(event.get("attendees"), list) else []
    attendee_text = " ".join(
        " ".join(text(attendee.get(key), "") for key in ("name", "url", "status", "role", "type"))
        for attendee in attendees
        if isinstance(attendee, dict)
    )
    return " ".join(
        text(value, "")
        for value in (
            event.get("title"),
            event.get("location"),
            event.get("notes"),
            event.get("url"),
            organizer.get("name"),
            organizer.get("url"),
            attendee_text,
            calendar_haystack(calendar),
        )
    ).lower()


def source_matches(source: dict[str, Any], args: argparse.Namespace) -> bool:
    if getattr(args, "source_id", None) and source.get("sourceIdentifier") != args.source_id:
        return False
    filters = split_filters(getattr(args, "source", None))
    if filters and not any(value in source_haystack(source) for value in filters):
        return False
    if getattr(args, "type", None) and source.get("type") != args.type:
        return False
    return True


def calendar_matches(calendar: dict[str, Any], args: argparse.Namespace) -> bool:
    if getattr(args, "calendar_id", None) and calendar.get("calendarIdentifier") != args.calendar_id:
        return False
    source = calendar.get("source") if isinstance(calendar.get("source"), dict) else calendar
    if getattr(args, "source_id", None) and source.get("sourceIdentifier") != args.source_id and calendar.get("sourceIdentifier") != args.source_id:
        return False
    source_filters = split_filters(getattr(args, "source", None))
    if source_filters and not any(value in calendar_haystack(calendar) for value in source_filters):
        return False
    calendar_filters = split_filters(getattr(args, "calendar", None))
    if calendar_filters and not any(value in calendar_haystack(calendar) for value in calendar_filters):
        return False
    if getattr(args, "type", None):
        source = calendar.get("source") if isinstance(calendar.get("source"), dict) else {}
        types = {calendar.get("type"), calendar.get("sourceType"), source.get("type")}
        if args.type not in types:
            return False
    if getattr(args, "writable", False) and not bool(calendar.get("allowsContentModifications")):
        return False
    return True


def event_matches(event: dict[str, Any], args: argparse.Namespace) -> bool:
    calendar = event.get("calendar") if isinstance(event.get("calendar"), dict) else {}
    if not calendar_matches(calendar, args):
        return False
    query = getattr(args, "query", None) or getattr(args, "term", None)
    if query and query.lower() not in event_haystack(event):
        return False
    return True


def filtered_sources(sources: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    return [source for source in sources if source_matches(source, args)]


def filtered_calendars(calendars: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    return [calendar for calendar in calendars if calendar_matches(calendar, args)]


def filtered_events(events: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    output = [event for event in events if event_matches(event, args)]
    return output[: getattr(args, "limit", DEFAULT_LIMIT)]


def event_time_text(event: dict[str, Any]) -> str:
    start = parse_bridge_datetime(str(event["start"]))
    end = parse_bridge_datetime(str(event["end"]))
    if event.get("isAllDay"):
        return f"{start.strftime('%Y-%m-%d')} all-day"
    return f"{start.strftime('%Y-%m-%d %H:%M')}-{end.strftime('%H:%M %Z')}"


def event_source(event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    calendar = event.get("calendar") if isinstance(event.get("calendar"), dict) else {}
    source = calendar.get("source") if isinstance(calendar.get("source"), dict) else calendar
    return calendar, source


def event_row(event: dict[str, Any]) -> str:
    calendar, source = event_source(event)
    return " | ".join(
        [
            f"event_id={text(event.get('eventIdentifier'))}",
            f"item_id={text(event.get('calendarItemIdentifier'))}",
            f"when={event_time_text(event)}",
            f"start={text(event.get('start'))}",
            f"end={text(event.get('end'))}",
            f"calendar={text(calendar.get('title'))}",
            f"source={text(source.get('title') or calendar.get('sourceTitle'))}",
            f"writable={str(bool(calendar.get('allowsContentModifications'))).lower()}",
            f"all_day={str(bool(event.get('isAllDay'))).lower()}",
            f"availability={text(event.get('availability'))}",
            f"status={text(event.get('status'))}",
            f"title={truncate(event.get('title'))}",
            f"location={truncate(event.get('location'), 80)}",
        ]
    )


def event_agenda_row(event: dict[str, Any]) -> str:
    calendar, _source = event_source(event)
    fields = [
        f"when={event_time_text(event)}",
        f"title={truncate(event.get('title'))}",
        f"calendar={text(calendar.get('title'))}",
    ]
    location = text(event.get("location"), "")
    if location:
        fields.append(f"location={truncate(location, 80)}")
    return " | ".join(fields)


def events_payload(command: str, start: datetime, end: datetime, events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at(),
        "command": command,
        "range": {"start": iso_local(start), "end": iso_local(end)},
        "count": len(events),
        "events": events,
    }


def load_event_payload(path: str | Path) -> dict[str, Any]:
    payload_path = Path(path).expanduser()
    if not payload_path.is_file():
        raise AppleCalendarError(f"payload file not found: {payload_path}")
    parsed = json.loads(payload_path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise AppleCalendarError("payload must be a JSON object")
    if "event" in parsed:
        siblings = sorted(set(parsed) - {"event"})
        if siblings:
            raise AppleCalendarError("wrapped payload must not include fields beside event: " + ", ".join(siblings))
    event = parsed.get("event", parsed)
    if not isinstance(event, dict):
        raise AppleCalendarError("payload event must be a JSON object")
    return event


def normalized_alarms(event: dict[str, Any]) -> list[dict[str, Any]] | None:
    if "alarms" not in event:
        return None
    alarms = event.get("alarms")
    if alarms is None:
        return []
    if not isinstance(alarms, list):
        raise AppleCalendarError("alarms must be an array")
    output: list[dict[str, Any]] = []
    for alarm in alarms:
        if not isinstance(alarm, dict):
            raise AppleCalendarError("each alarm must be an object")
        item = dict(alarm)
        if "absolute_date" in item and "absoluteDateTimestamp" not in item:
            item["absoluteDateTimestamp"] = parse_local_datetime(str(item.pop("absolute_date"))).timestamp()
        if "absoluteDate" in item and "absoluteDateTimestamp" not in item:
            item["absoluteDateTimestamp"] = parse_local_datetime(str(item.pop("absoluteDate"))).timestamp()
        output.append(item)
    return output


def normalized_recurrence(event: dict[str, Any]) -> dict[str, Any] | None:
    if "recurrence" not in event:
        return None
    recurrence = event.get("recurrence")
    if recurrence in (None, False):
        return None
    if not isinstance(recurrence, dict):
        raise AppleCalendarError("recurrence must be an object")
    output = dict(recurrence)
    if "end_date" in output and "endDateTimestamp" not in output:
        output["endDateTimestamp"] = parse_local_datetime(str(output.pop("end_date"))).timestamp()
    if "endDate" in output and "endDateTimestamp" not in output:
        output["endDateTimestamp"] = parse_local_datetime(str(output.pop("endDate"))).timestamp()
    if "occurrence_count" in output and "occurrenceCount" not in output:
        output["occurrenceCount"] = output.pop("occurrence_count")
    return output


def ensure_unsupported_fields_absent(event: dict[str, Any]) -> None:
    unsupported = sorted(set(event) & EVENT_UNSUPPORTED_FIELDS)
    if unsupported:
        raise AppleCalendarError(
            "attendee/organizer mutation is not supported by apple-calendar v1; unsupported field(s): "
            + ", ".join(unsupported)
        )
    unknown = sorted(set(event) - EVENT_ALLOWED_FIELDS - EVENT_UNSUPPORTED_FIELDS)
    if unknown:
        raise AppleCalendarError("unknown event payload field(s): " + ", ".join(unknown))


def applied_request_keys(request: dict[str, Any]) -> set[str]:
    ignored = {"operation", "confirm"}
    return set(request) - ignored - EVENT_STRUCTURAL_FIELDS


def bridge_request_from_event_payload(operation: str, event: dict[str, Any], *, confirm: bool) -> dict[str, Any]:
    ensure_unsupported_fields_absent(event)
    request: dict[str, Any] = {"operation": operation, "confirm": confirm}
    field_map = {
        "calendar_id": "calendarIdentifier",
        "calendarIdentifier": "calendarIdentifier",
        "title": "title",
        "location": "location",
        "notes": "notes",
        "url": "url",
        "availability": "availability",
    }
    for source_key, target_key in field_map.items():
        if source_key in event:
            request[target_key] = event[source_key]

    if "is_all_day" in event:
        request["isAllDay"] = bool(event["is_all_day"])
    if "all_day" in event:
        request["isAllDay"] = bool(event["all_day"])
    if "isAllDay" in event:
        request["isAllDay"] = bool(event["isAllDay"])

    start, end = event_start_end_for_payload(event, require_start=operation == "create")
    if start:
        request["startTimestamp"] = start.timestamp()
    if end:
        request["endTimestamp"] = end.timestamp()

    alarms = normalized_alarms(event)
    if alarms is not None:
        request["alarms"] = alarms
    recurrence = normalized_recurrence(event)
    if recurrence is not None:
        request["recurrence"] = recurrence

    for key, request_key in (
        ("clear_location", "clearLocation"),
        ("clear_notes", "clearNotes"),
        ("clear_url", "clearURL"),
        ("clear_alarms", "clearAlarms"),
        ("clear_recurrence", "clearRecurrence"),
    ):
        if event.get(key):
            request[request_key] = True
    if operation == "update" and not applied_request_keys(request):
        raise AppleCalendarError("update payload must include at least one supported field change")
    return request


def print_sources(sources: list[dict[str, Any]]) -> None:
    print(f"Apple Calendar sources | count={len(sources)}")
    for source in sources:
        print(
            " | ".join(
                [
                    f"source_id={text(source.get('sourceIdentifier'))}",
                    f"title={text(source.get('title'))}",
                    f"type={text(source.get('type'))}",
                    f"calendars={source.get('calendarCount', '-')}",
                ]
            )
        )


def print_calendars(calendars: list[dict[str, Any]]) -> None:
    print(f"Apple Calendar calendars | count={len(calendars)}")
    for calendar in calendars:
        source = calendar.get("source") if isinstance(calendar.get("source"), dict) else {}
        print(
            " | ".join(
                [
                    f"calendar_id={text(calendar.get('calendarIdentifier'))}",
                    f"title={text(calendar.get('title'))}",
                    f"type={text(calendar.get('type'))}",
                    f"source={text(source.get('title') or calendar.get('sourceTitle'))}",
                    f"source_id={text(source.get('sourceIdentifier') or calendar.get('sourceIdentifier'))}",
                    f"source_type={text(source.get('type') or calendar.get('sourceType'))}",
                    f"writable={str(bool(calendar.get('allowsContentModifications'))).lower()}",
                    f"color={text(calendar.get('color'))}",
                ]
            )
        )


def print_events(label: str, events: list[dict[str, Any]], *, full: bool = False) -> None:
    print(f"{label} | count={len(events)}")
    for event in events:
        print(event_row(event) if full else event_agenda_row(event))


def print_event_card(event: dict[str, Any]) -> None:
    calendar, source = event_source(event)
    print(
        "Apple Calendar event | "
        f"event_id={text(event.get('eventIdentifier'))} | "
        f"item_id={text(event.get('calendarItemIdentifier'))} | "
        f"calendar={text(calendar.get('title'))} | "
        f"source={text(source.get('title') or calendar.get('sourceTitle'))}"
    )
    print(f"title: {text(event.get('title'))}")
    print(f"when: {event_time_text(event)}")
    if text(event.get("location"), ""):
        print(f"location: {text(event.get('location'))}")
    if text(event.get("url"), ""):
        print(f"url: {text(event.get('url'))}")
    print(f"availability: {text(event.get('availability'))}")
    print(f"status: {text(event.get('status'))}")
    print(f"all_day: {str(bool(event.get('isAllDay'))).lower()}")
    if text(event.get("timeZone"), ""):
        print(f"time_zone: {text(event.get('timeZone'))}")
    if text(event.get("creationDate"), ""):
        print(f"created: {text(event.get('creationDate'))}")
    if text(event.get("lastModifiedDate"), ""):
        print(f"modified: {text(event.get('lastModifiedDate'))}")
    organizer = event.get("organizer") if isinstance(event.get("organizer"), dict) else {}
    if text(organizer.get("name"), "") or text(organizer.get("url"), ""):
        print(f"organizer: {text(organizer.get('name'), '')} {text(organizer.get('url'), '')}".strip())
    attendees = event.get("attendees") if isinstance(event.get("attendees"), list) else []
    if attendees:
        print("attendees:")
        for attendee in attendees:
            if isinstance(attendee, dict):
                print(
                    "  "
                    + " | ".join(
                        [
                            f"name={text(attendee.get('name'))}",
                            f"url={text(attendee.get('url'))}",
                            f"status={text(attendee.get('status'))}",
                            f"role={text(attendee.get('role'))}",
                            f"type={text(attendee.get('type'))}",
                        ]
                    )
                )
    alarms = event.get("alarms") if isinstance(event.get("alarms"), list) else []
    if alarms:
        print("alarms:")
        for alarm in alarms:
            if isinstance(alarm, dict):
                print(f"  relative_offset={alarm.get('relativeOffset', '-')} | absolute_date={text(alarm.get('absoluteDate'))}")
    recurrence = event.get("recurrenceRules") if isinstance(event.get("recurrenceRules"), list) else []
    if recurrence:
        print("recurrence:")
        for rule in recurrence:
            if isinstance(rule, dict):
                print(
                    "  "
                    + " | ".join(
                        [
                            f"frequency={text(rule.get('frequency'))}",
                            f"interval={rule.get('interval', '-')}",
                            f"end_date={text(rule.get('endDate'))}",
                            f"occurrence_count={rule.get('occurrenceCount', '-')}",
                        ]
                    )
                )
    if text(event.get("notes"), ""):
        print("notes:")
        print(text(event.get("notes")))


def availability_row(event: dict[str, Any]) -> str:
    calendar, _source = event_source(event)
    return " | ".join(
        [
            f"when={event_time_text(event)}",
            f"availability={text(event.get('availability'))}",
            f"title={truncate(event.get('title'))}",
            f"calendar={text(calendar.get('title'))}",
        ]
    )


def availability_full_row(event: dict[str, Any]) -> str:
    calendar, source = event_source(event)
    return " | ".join(
        [
            f"event_id={text(event.get('eventIdentifier'))}",
            f"when={event_time_text(event)}",
            f"availability={text(event.get('availability'))}",
            f"calendar={text(calendar.get('title'))}",
            f"source={text(source.get('title') or calendar.get('sourceTitle'))}",
            f"title={truncate(event.get('title'))}",
        ]
    )


def print_availability(events: list[dict[str, Any]], *, full: bool = False) -> None:
    print(f"Apple Calendar availability | blocks={len(events)}")
    for event in events:
        print(availability_full_row(event) if full else availability_row(event))


def print_mutation_result(operation: str, payload: dict[str, Any], confirmed: bool) -> None:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    before = payload.get("before") if isinstance(payload.get("before"), dict) else None
    prefix = operation if confirmed else f"dry-run {operation}"
    if before:
        print(f"{prefix}: before | {event_row(before)}")
        print(f"{prefix}: after | saved={str(confirmed).lower()} | {event_row(event)}")
        return
    print(f"{prefix}: saved={str(confirmed).lower()} | {event_row(event)}")
