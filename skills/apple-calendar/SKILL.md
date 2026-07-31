---
name: apple-calendar
description: Read and safely manage local Apple Calendar / Calendar.app events through the local apple-calendar CLI. Use when a task mentions calendar, schedule, availability, meetings, EventKit, or Calendar.app.
---

# Apple Calendar

Run the bundled CLI at `$RUNDESK_SKILLS/apple-calendar/scripts/apple-calendar`. It talks
to the signed-in Mac's EventKit store; macOS Calendar permission may be required. Read
`references/cli.md` only for setup, payload, or validation details.

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
