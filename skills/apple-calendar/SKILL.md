---
name: apple-calendar
description: Use when the user asks to inspect availability, calendars, or events on this Mac, or to create, change, or delete a specific Apple Calendar event. It supplies bounded EventKit reads and guarded event writes. Do not use for RSVP responses or calendar-container changes; those are unsupported.
---

# Apple Calendar

Run `$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar`. Approve its macOS Calendar prompt only
when the owner is present. Read `references/cli.md` only for permission setup, payload fields,
recurring-event rules, or validation.

Start with access checks:

```sh
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" read status
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" write status
```

Bounded reads by default:

```sh
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" read sources
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" read calendars --writable
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" read events --today
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" read search '<term>' --days 14
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" read show --event-id <EVENT_ID>
"$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar" read availability --today
```

Use `--full` when follow-up actions need event IDs. Use `--json` only for structured
payloads. Event create/update/delete are dry-runs without `--confirm`. Calendar
container changes and RSVP responses are unsupported. Never confirm a mutation unless
the owner approved that exact event change.
