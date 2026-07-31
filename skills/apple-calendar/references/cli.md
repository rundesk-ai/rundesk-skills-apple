---
name: apple-calendar
description: Reading and safely managing local Apple Calendar / Calendar.app events.
category: local
---

# apple-calendar

## Use When

Use this tool when an agent needs Apple Calendar / Calendar.app as the local source of truth for iCloud-synced calendars and events. Read commands are for EventKit source, calendar, event, availability, search, and export context. Write commands are for safe event create, update, and delete operations.

## Entry Point

- Verify read access: `apple-calendar read status`
- List sources/accounts: `apple-calendar read sources`
- List calendars: `apple-calendar read calendars`
- List events: `apple-calendar read events --today`
- Search events: `apple-calendar read search "Example planning"`
- Show one event: `apple-calendar read show --event-id EVENT_ID`
- Show availability: `apple-calendar read availability --today`
- Export bounded JSON: `apple-calendar read export --days 7 --json`
- Verify write access: `apple-calendar write status`
- Dry-run create: `apple-calendar write create --payload event.json`
- Actually save a reviewed create: `apple-calendar write create --payload event.json --confirm`

The same-name dispatcher also works:

```bash
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" read calendars
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" write create --payload event.json
```

Read and write commands use EventKit. Calendar containers are read-only in v1; event create, update, and delete commands are dry-runs unless `--confirm` is passed. Event list/search output is compact agenda text by default. Use `--full` when an agent needs event IDs or other operational fields for a follow-up action, and use `--json` when another script or agent needs the full structured event payload. Confirmed recurring event updates and deletes require both `--span this|future` and `--occurrence-start START` copied from read output.

Event invitation response status is readable through `show` attendee/organizer fields. Scripted RSVP actions such as accept, tentative, and decline are explicitly unsupported in v1 because this Mac's EventKit and Calendar AppleScript surfaces do not expose a safe response mutation API.

## Validation

- Run `python3 $RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar.d/test-apple-calendar.py` for offline tests with synthetic EventKit bridge payloads.
- Run `/usr/bin/swiftc $RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar.d/AppleCalendarBridge.swift -o /tmp/apple-calendar-bridge-check` for a Swift compile check.
- Run `apple-calendar read status` as a live read smoke test.
- Run `apple-calendar read calendars --writable` as a live calendar discovery smoke test.
- Run `apple-calendar read events --today` as a live event-query smoke test.
- Run `apple-calendar write status` as a live non-mutating write-permission smoke test.
- Optional live mutation test: `APPLE_CALENDAR_LIVE_TESTS=1 APPLE_CALENDAR_TEST_CALENDAR_ID=<disposable-calendar-id> python3 $RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar.d/test-apple-calendar.py`. This creates, updates, and deletes one synthetic event with a unique marker.

## Provider

This integration is self-contained: its provider contract lives here, in this README, not in a separate file or a shared folder. It manages local macOS Calendar.app events through EventKit.

EventKit is the source-of-truth path for both reads and writes. Calendar.app and the configured iCloud/CalDAV/Exchange accounts own sync consistency. Direct Calendar SQLite reads or writes are not part of this tool.

### Safety Rule

Read commands may inspect EventKit sources, calendars, events, availability, attendees, alarms, recurrence summaries, and event metadata. They must not mutate data.

Write commands are dry-runs unless `--confirm` is passed after the owner has explicitly approved the exact calendar/event change. V1 supports event create, update, and delete only. Calendar container create, rename, and delete are intentionally unsupported.

Invitation RSVP responses are also unsupported in v1. EventKit exposes attendee response status for reads, but this Mac's EventKit and Calendar AppleScript surfaces do not expose a safe scripted accept/tentative/decline mutation path. Agents should inspect attendee status with `apple-calendar-read.py show` and ask the owner to respond in Calendar.app when an RSVP action is needed.

### Setup

The local Mac must have Calendar configured. The terminal or Codex host app may need macOS Calendar permission.

Verify read access:

```bash
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" read status
```

Verify non-mutating write access:

```bash
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" write status
```

### Read Workflow

Use compact text by default:

```bash
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" read sources
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" read calendars --writable
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" read events --today
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" read search "Example planning" --days 14
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" read availability --today
```

Use JSON when another script or agent needs structured payloads:

```bash
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" read show --event-id EVENT_ID --json
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" read export --days 7 --json
```

Filters are generic and account-aware: use source IDs, source/account text, calendar IDs, calendar text, calendar type, writable status, and date ranges. Do not hardcode organization names in the tool.

### Write Workflow

Write commands accept synthetic event JSON payload files and are dry-runs by default:

```bash
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" write create --payload event.json
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" write update --event-id EVENT_ID --payload patch.json
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" write delete --event-id EVENT_ID
```

Save only after the owner explicitly asks for that exact change:

```bash
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" write update --event-id EVENT_ID --payload patch.json --confirm
```

Prefer exact `calendar_id`, `event_id`, and `calendar_item_id` values from the read tool. Confirmed recurring event updates and deletes require explicit `--span this|future` plus `--occurrence-start START` copied from the event row or JSON payload, so EventKit can resolve the intended occurrence before saving or deleting.

### Payload Shape

Create and update payloads may be the event object directly or `{ "event": { ... } }`. Supported fields include:

- Scalars: `calendar_id`, `title`, `start`, `end`, `duration_min`, `all_day`, `location`, `notes`, `url`, `availability`.
- Clearing fields on update: `clear_location`, `clear_notes`, `clear_url`, `clear_alarms`, `clear_recurrence`.
- Arrays: `alarms`.
- Object: `recurrence` with `frequency`, `interval`, optional `end_date`, or optional `occurrence_count`.

Attendees and organizer data are read in v1 but are not mutated by this tool.

### Notes

- Calendar data can contain personal schedule details. Do not commit command output, real event titles, real locations, attendee identities, notes, URLs, or EventKit identifiers.
- All committed examples, tests, fixtures, and docs must use synthetic data only.
- Live tests create and delete only one synthetic event with a unique marker in a disposable writable calendar selected by the owner.
