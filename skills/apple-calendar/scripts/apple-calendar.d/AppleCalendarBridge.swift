import EventKit
import Foundation

let bridgeResponsePath = CommandLine.arguments.count == 4 ? CommandLine.arguments[2] : nil
let bridgeErrorPath = CommandLine.arguments.count == 4 ? CommandLine.arguments[3] : nil

func writeBridgeData(_ data: Data, path: String?, fallback: FileHandle) {
    if let path = path {
        do {
            try data.write(to: URL(fileURLWithPath: path), options: .atomic)
            return
        } catch {
            fallback.write(Data("error: unable to write bridge result: \(error.localizedDescription)\n".utf8))
            exit(1)
        }
    }
    fallback.write(data)
}

func fail(_ message: String) -> Never {
    writeBridgeData(Data("error: \(message)\n".utf8), path: bridgeErrorPath, fallback: .standardError)
    exit(1)
}

func jsonObject(from path: String) -> [String: Any] {
    do {
        let data = try Data(contentsOf: URL(fileURLWithPath: path))
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            fail("request JSON must be an object")
        }
        return object
    } catch {
        fail("unable to read request JSON: \(error.localizedDescription)")
    }
}

func printJSON(_ object: Any) {
    do {
        let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        writeBridgeData(data + Data("\n".utf8), path: bridgeResponsePath, fallback: .standardOutput)
    } catch {
        fail("unable to encode JSON: \(error.localizedDescription)")
    }
}

func string(_ request: [String: Any], _ key: String) -> String? {
    guard let value = request[key] else {
        return nil
    }
    if value is NSNull {
        return nil
    }
    let output = String(describing: value).trimmingCharacters(in: .whitespacesAndNewlines)
    return output.isEmpty ? nil : output
}

func bool(_ request: [String: Any], _ key: String, default fallback: Bool = false) -> Bool {
    guard let value = request[key] else {
        return fallback
    }
    if let typed = value as? Bool {
        return typed
    }
    return ["1", "true", "yes"].contains(String(describing: value).lowercased())
}

func int(_ request: [String: Any], _ key: String, default fallback: Int = 0) -> Int {
    guard let value = request[key] else {
        return fallback
    }
    if let typed = value as? NSNumber {
        return typed.intValue
    }
    return Int(String(describing: value)) ?? fallback
}

func double(_ request: [String: Any], _ key: String) -> Double? {
    guard let value = request[key] else {
        return nil
    }
    if let typed = value as? NSNumber {
        return typed.doubleValue
    }
    return Double(String(describing: value))
}

func timestamp(_ request: [String: Any], _ key: String) -> Date? {
    guard let value = double(request, key) else {
        return nil
    }
    return Date(timeIntervalSince1970: value)
}

func requireTimestamp(_ request: [String: Any], _ key: String) -> Date {
    guard let value = timestamp(request, key) else {
        fail("missing or invalid timestamp: \(key)")
    }
    return value
}

func dictionary(_ request: [String: Any], _ key: String) -> [String: Any]? {
    guard let value = request[key], !(value is NSNull) else {
        return nil
    }
    guard let output = value as? [String: Any] else {
        fail("\(key) must be an object")
    }
    return output
}

func dictionaryArray(_ request: [String: Any], _ key: String) -> [[String: Any]]? {
    guard let value = request[key], !(value is NSNull) else {
        return nil
    }
    guard let output = value as? [[String: Any]] else {
        fail("\(key) must be an array of objects")
    }
    return output
}

func requestCalendarAccess(_ store: EKEventStore) {
    let semaphore = DispatchSemaphore(value: 0)
    var granted = false
    var accessError: Error?

    if #available(macOS 14.0, *) {
        store.requestFullAccessToEvents { ok, error in
            granted = ok
            accessError = error
            semaphore.signal()
        }
    } else {
        store.requestAccess(to: .event) { ok, error in
            granted = ok
            accessError = error
            semaphore.signal()
        }
    }

    if semaphore.wait(timeout: .now() + 60) == .timedOut {
        fail("timed out waiting for Calendar access")
    }
    if !granted {
        fail("Calendar access denied: \(accessError?.localizedDescription ?? "unknown error")")
    }
}

let isoFormatter: ISO8601DateFormatter = {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withDashSeparatorInDate, .withColonSeparatorInTime, .withColonSeparatorInTimeZone]
    return formatter
}()

func sourceTypeName(_ type: EKSourceType) -> String {
    switch type {
    case .local:
        return "local"
    case .exchange:
        return "exchange"
    case .calDAV:
        return "caldav"
    case .mobileMe:
        return "mobileme"
    case .subscribed:
        return "subscribed"
    case .birthdays:
        return "birthdays"
    @unknown default:
        return "unknown"
    }
}

func calendarTypeName(_ type: EKCalendarType) -> String {
    switch type {
    case .local:
        return "local"
    case .calDAV:
        return "caldav"
    case .exchange:
        return "exchange"
    case .subscription:
        return "subscription"
    case .birthday:
        return "birthday"
    @unknown default:
        return "unknown"
    }
}

func eventStatusName(_ status: EKEventStatus) -> String {
    switch status {
    case .none:
        return "none"
    case .confirmed:
        return "confirmed"
    case .tentative:
        return "tentative"
    case .canceled:
        return "canceled"
    @unknown default:
        return "unknown"
    }
}

func availabilityName(_ availability: EKEventAvailability) -> String {
    switch availability {
    case .notSupported:
        return "not-supported"
    case .busy:
        return "busy"
    case .free:
        return "free"
    case .tentative:
        return "tentative"
    case .unavailable:
        return "unavailable"
    @unknown default:
        return "unknown"
    }
}

func availabilityValue(_ name: String?) -> EKEventAvailability? {
    switch name?.lowercased() {
    case "busy":
        return .busy
    case "free":
        return .free
    case "tentative":
        return .tentative
    case "unavailable":
        return .unavailable
    case nil:
        return nil
    default:
        fail("unknown availability: \(name ?? "")")
    }
}

func participantStatusName(_ status: EKParticipantStatus) -> String {
    switch status {
    case .unknown:
        return "unknown"
    case .pending:
        return "pending"
    case .accepted:
        return "accepted"
    case .declined:
        return "declined"
    case .tentative:
        return "tentative"
    case .delegated:
        return "delegated"
    case .completed:
        return "completed"
    case .inProcess:
        return "in-process"
    @unknown default:
        return "unknown"
    }
}

func participantRoleName(_ role: EKParticipantRole) -> String {
    switch role {
    case .unknown:
        return "unknown"
    case .required:
        return "required"
    case .optional:
        return "optional"
    case .chair:
        return "chair"
    case .nonParticipant:
        return "non-participant"
    @unknown default:
        return "unknown"
    }
}

func participantTypeName(_ type: EKParticipantType) -> String {
    switch type {
    case .unknown:
        return "unknown"
    case .person:
        return "person"
    case .room:
        return "room"
    case .resource:
        return "resource"
    case .group:
        return "group"
    @unknown default:
        return "unknown"
    }
}

func recurrenceFrequencyName(_ frequency: EKRecurrenceFrequency) -> String {
    switch frequency {
    case .daily:
        return "daily"
    case .weekly:
        return "weekly"
    case .monthly:
        return "monthly"
    case .yearly:
        return "yearly"
    @unknown default:
        return "unknown"
    }
}

func recurrenceFrequencyValue(_ name: String?) -> EKRecurrenceFrequency? {
    switch name?.lowercased() {
    case "daily":
        return .daily
    case "weekly":
        return .weekly
    case "monthly":
        return .monthly
    case "yearly":
        return .yearly
    case nil:
        return nil
    default:
        fail("unknown recurrence frequency: \(name ?? "")")
    }
}

func colorHex(_ cgColor: CGColor?) -> String? {
    guard let components = cgColor?.components else {
        return nil
    }
    let red: CGFloat
    let green: CGFloat
    let blue: CGFloat
    if components.count >= 3 {
        red = components[0]
        green = components[1]
        blue = components[2]
    } else if let first = components.first {
        red = first
        green = first
        blue = first
    } else {
        return nil
    }
    return String(format: "#%02X%02X%02X", Int(red * 255), Int(green * 255), Int(blue * 255))
}

func sourceJSON(_ source: EKSource, calendarCount: Int? = nil) -> [String: Any] {
    var output: [String: Any] = [
        "sourceIdentifier": source.sourceIdentifier,
        "title": source.title,
        "type": sourceTypeName(source.sourceType),
    ]
    if let calendarCount {
        output["calendarCount"] = calendarCount
    }
    return output
}

func calendarJSON(_ calendar: EKCalendar) -> [String: Any] {
    var output: [String: Any] = [
        "calendarIdentifier": calendar.calendarIdentifier,
        "title": calendar.title,
        "type": calendarTypeName(calendar.type),
        "allowsContentModifications": calendar.allowsContentModifications,
        "source": sourceJSON(calendar.source),
        "sourceIdentifier": calendar.source.sourceIdentifier,
        "sourceTitle": calendar.source.title,
        "sourceType": sourceTypeName(calendar.source.sourceType),
    ]
    if let hex = colorHex(calendar.cgColor) {
        output["color"] = hex
    }
    return output
}

func participantJSON(_ participant: EKParticipant) -> [String: Any] {
    [
        "name": participant.name ?? "",
        "url": participant.url.absoluteString,
        "status": participantStatusName(participant.participantStatus),
        "role": participantRoleName(participant.participantRole),
        "type": participantTypeName(participant.participantType),
    ]
}

func alarmJSON(_ alarm: EKAlarm) -> [String: Any] {
    [
        "absoluteDate": alarm.absoluteDate.map { isoFormatter.string(from: $0) } ?? "",
        "relativeOffset": alarm.relativeOffset,
    ]
}

func recurrenceRuleJSON(_ rule: EKRecurrenceRule) -> [String: Any] {
    var output: [String: Any] = [
        "frequency": recurrenceFrequencyName(rule.frequency),
        "interval": rule.interval,
        "description": String(describing: rule),
    ]
    if let end = rule.recurrenceEnd {
        output["endDate"] = end.endDate.map { isoFormatter.string(from: $0) } ?? ""
        output["occurrenceCount"] = end.occurrenceCount
    }
    return output
}

func eventJSON(_ event: EKEvent) -> [String: Any] {
    var output: [String: Any] = [
        "eventIdentifier": event.eventIdentifier ?? "",
        "calendarItemIdentifier": event.calendarItemIdentifier,
        "calendarItemExternalIdentifier": event.calendarItemExternalIdentifier ?? "",
        "title": event.title ?? "",
        "start": isoFormatter.string(from: event.startDate),
        "end": isoFormatter.string(from: event.endDate),
        "isAllDay": event.isAllDay,
        "status": eventStatusName(event.status),
        "availability": availabilityName(event.availability),
        "calendar": calendarJSON(event.calendar),
        "hasAlarms": !(event.alarms ?? []).isEmpty,
        "hasRecurrenceRules": !(event.recurrenceRules ?? []).isEmpty,
        "location": event.location ?? "",
        "timeZone": event.timeZone?.identifier ?? "",
        "creationDate": event.creationDate.map { isoFormatter.string(from: $0) } ?? "",
        "lastModifiedDate": event.lastModifiedDate.map { isoFormatter.string(from: $0) } ?? "",
        "organizer": event.organizer.map { participantJSON($0) } ?? [:],
    ]
    output["notes"] = event.notes ?? ""
    output["url"] = event.url?.absoluteString ?? ""
    output["attendees"] = (event.attendees ?? []).map { participantJSON($0) }
    output["alarms"] = (event.alarms ?? []).map { alarmJSON($0) }
    output["recurrenceRules"] = (event.recurrenceRules ?? []).map { recurrenceRuleJSON($0) }
    return output
}

func findCalendar(_ store: EKEventStore, identifier: String?) -> EKCalendar {
    guard let identifier else {
        fail("missing calendarIdentifier")
    }
    guard let calendar = store.calendar(withIdentifier: identifier) else {
        fail("calendar not found: \(identifier)")
    }
    guard calendar.allowsContentModifications else {
        fail("calendar is read-only: \(calendar.title)")
    }
    return calendar
}

func findEvent(_ store: EKEventStore, request: [String: Any]) -> EKEvent {
    if let occurrenceStart = timestamp(request, "occurrenceStartTimestamp") {
        let calendars = allCalendars(store)
        let predicate = store.predicateForEvents(
            withStart: occurrenceStart.addingTimeInterval(-60),
            end: occurrenceStart.addingTimeInterval(60),
            calendars: calendars
        )
        let matches = store.events(matching: predicate).filter { event in
            let identifierMatches = string(request, "eventIdentifier").map { event.eventIdentifier == $0 } ?? false
            let itemMatches = string(request, "calendarItemIdentifier").map { event.calendarItemIdentifier == $0 } ?? false
            return (identifierMatches || itemMatches) && abs(event.startDate.timeIntervalSince(occurrenceStart)) < 1
        }
        if matches.count == 1 {
            return matches[0]
        }
        if matches.isEmpty {
            fail("event occurrence not found")
        }
        fail("event occurrence lookup was ambiguous")
    }
    if let identifier = string(request, "eventIdentifier"), let event = store.event(withIdentifier: identifier) {
        return event
    }
    if let itemIdentifier = string(request, "calendarItemIdentifier"),
       let event = store.calendarItem(withIdentifier: itemIdentifier) as? EKEvent {
        return event
    }
    fail("event not found")
}

func ekSpan(_ value: String?) -> EKSpan {
    switch value?.lowercased() {
    case "future":
        return .futureEvents
    case nil, "this":
        return .thisEvent
    default:
        fail("unknown recurring span: \(value ?? "")")
    }
}

func alarmFromPayload(_ payload: [String: Any]) -> EKAlarm {
    if let timestamp = timestamp(payload, "absoluteDateTimestamp") {
        return EKAlarm(absoluteDate: timestamp)
    }
    if let offset = double(payload, "relativeOffset") {
        return EKAlarm(relativeOffset: offset)
    }
    if let minutes = double(payload, "relativeOffsetMinutes") {
        return EKAlarm(relativeOffset: minutes * 60)
    }
    if let minutes = double(payload, "relative_offset_minutes") {
        return EKAlarm(relativeOffset: minutes * 60)
    }
    fail("alarm requires relativeOffset, relativeOffsetMinutes, relative_offset_minutes, or absoluteDateTimestamp")
}

func recurrenceRuleFromPayload(_ payload: [String: Any]) -> EKRecurrenceRule {
    guard let frequency = recurrenceFrequencyValue(string(payload, "frequency")) else {
        fail("recurrence.frequency is required")
    }
    let interval = max(1, int(payload, "interval", default: 1))
    var recurrenceEnd: EKRecurrenceEnd?
    if let endDate = timestamp(payload, "endDateTimestamp") {
        recurrenceEnd = EKRecurrenceEnd(end: endDate)
    } else {
        let occurrenceCount = int(payload, "occurrenceCount")
        if occurrenceCount > 0 {
            recurrenceEnd = EKRecurrenceEnd(occurrenceCount: occurrenceCount)
        }
    }
    return EKRecurrenceRule(recurrenceWith: frequency, interval: interval, end: recurrenceEnd)
}

func applyOptionalChanges(to event: EKEvent, request: [String: Any]) {
    if let value = string(request, "location") {
        event.location = value
    }
    if let value = string(request, "notes") {
        event.notes = value
    }
    if let value = string(request, "url") {
        guard let url = URL(string: value) else {
            fail("invalid event URL: \(value)")
        }
        event.url = url
    }
    if let value = availabilityValue(string(request, "availability")) {
        event.availability = value
    }
    if let alarms = dictionaryArray(request, "alarms") {
        event.alarms = alarms.map { alarmFromPayload($0) }
    }
    if let recurrence = dictionary(request, "recurrence") {
        event.recurrenceRules = [recurrenceRuleFromPayload(recurrence)]
    }
}

func applyChanges(to event: EKEvent, store: EKEventStore, request: [String: Any]) {
    applyOptionalChanges(to: event, request: request)
    if let value = string(request, "title") {
        event.title = value
    }
    if let value = timestamp(request, "startTimestamp") {
        event.startDate = value
    }
    if let value = timestamp(request, "endTimestamp") {
        event.endDate = value
    }
    if request.keys.contains("isAllDay") {
        event.isAllDay = bool(request, "isAllDay")
    }
    if bool(request, "clearLocation") {
        event.location = nil
    }
    if bool(request, "clearNotes") {
        event.notes = nil
    }
    if bool(request, "clearURL") {
        event.url = nil
    }
    if let identifier = string(request, "calendarIdentifier") {
        event.calendar = findCalendar(store, identifier: identifier)
    }
    if bool(request, "clearAlarms") {
        event.alarms = nil
    }
    if bool(request, "clearRecurrence") {
        event.recurrenceRules = nil
    }
}

func allCalendars(_ store: EKEventStore) -> [EKCalendar] {
    store.calendars(for: .event).sorted {
        if $0.source.title != $1.source.title {
            return $0.source.title < $1.source.title
        }
        return $0.title < $1.title
    }
}

func commandStatus(_ store: EKEventStore) {
    let calendars = allCalendars(store)
    let writable = calendars.filter { $0.allowsContentModifications }.count
    printJSON([
        "operation": "status",
        "status": "ok",
        "sources": store.sources.count,
        "calendars": calendars.count,
        "writableCalendars": writable,
    ])
}

func commandSources(_ store: EKEventStore) {
    let sources = store.sources.sorted { $0.title < $1.title }
    printJSON([
        "sources": sources.map { source in
            sourceJSON(source, calendarCount: source.calendars(for: .event).count)
        }
    ])
}

func commandCalendars(_ store: EKEventStore) {
    printJSON(["calendars": allCalendars(store).map { calendarJSON($0) }])
}

func commandEvents(_ store: EKEventStore, request: [String: Any]) {
    let start = requireTimestamp(request, "startTimestamp")
    let end = requireTimestamp(request, "endTimestamp")
    let calendars = allCalendars(store)
    let predicate = store.predicateForEvents(withStart: start, end: end, calendars: calendars)
    let events = store.events(matching: predicate).sorted {
        if $0.startDate != $1.startDate {
            return $0.startDate < $1.startDate
        }
        if $0.endDate != $1.endDate {
            return $0.endDate < $1.endDate
        }
        return ($0.title ?? "") < ($1.title ?? "")
    }
    printJSON(["events": events.map { eventJSON($0) }])
}

func commandShow(_ store: EKEventStore, request: [String: Any]) {
    let event = findEvent(store, request: request)
    printJSON(["event": eventJSON(event)])
}

func commandCreate(_ store: EKEventStore, request: [String: Any]) {
    let event = EKEvent(eventStore: store)
    event.calendar = findCalendar(store, identifier: string(request, "calendarIdentifier"))
    guard let title = string(request, "title") else {
        fail("missing title")
    }
    event.title = title
    event.startDate = requireTimestamp(request, "startTimestamp")
    event.endDate = requireTimestamp(request, "endTimestamp")
    event.isAllDay = bool(request, "isAllDay")
    event.timeZone = TimeZone.current
    applyOptionalChanges(to: event, request: request)

    let confirmed = bool(request, "confirm")
    if confirmed {
        do {
            try store.save(event, span: .thisEvent, commit: true)
        } catch {
            fail("unable to save event: \(error.localizedDescription)")
        }
    }
    printJSON(["operation": "create", "saved": confirmed, "event": eventJSON(event)])
}

func commandUpdate(_ store: EKEventStore, request: [String: Any]) {
    let event = findEvent(store, request: request)
    let before = eventJSON(event)
    let wasRecurring = !(event.recurrenceRules ?? []).isEmpty
    applyChanges(to: event, store: store, request: request)
    let confirmed = bool(request, "confirm")
    if confirmed && wasRecurring {
        guard string(request, "span") != nil else {
            fail("confirmed recurring update requires --span this|future")
        }
        guard timestamp(request, "occurrenceStartTimestamp") != nil else {
            fail("confirmed recurring update requires --occurrence-start copied from read output")
        }
    }
    if confirmed {
        do {
            try store.save(event, span: ekSpan(string(request, "span")), commit: true)
        } catch {
            fail("unable to save event: \(error.localizedDescription)")
        }
    }
    printJSON(["operation": "update", "saved": confirmed, "before": before, "event": eventJSON(event)])
}

func commandDelete(_ store: EKEventStore, request: [String: Any]) {
    let event = findEvent(store, request: request)
    let snapshot = eventJSON(event)
    let confirmed = bool(request, "confirm")
    if confirmed {
        if !(event.recurrenceRules ?? []).isEmpty {
            guard string(request, "span") != nil else {
                fail("confirmed recurring delete requires --span this|future")
            }
            guard timestamp(request, "occurrenceStartTimestamp") != nil else {
                fail("confirmed recurring delete requires --occurrence-start copied from read output")
            }
        }
        do {
            try store.remove(event, span: ekSpan(string(request, "span")), commit: true)
        } catch {
            fail("unable to delete event: \(error.localizedDescription)")
        }
    }
    printJSON(["operation": "delete", "saved": confirmed, "event": snapshot])
}

guard CommandLine.arguments.count == 2 || CommandLine.arguments.count == 4 else {
    fail("usage: AppleCalendarBridge <request.json> [response.json error.txt]")
}

let request = jsonObject(from: CommandLine.arguments[1])
let store = EKEventStore()
requestCalendarAccess(store)

switch string(request, "operation") {
case "status":
    commandStatus(store)
case "sources":
    commandSources(store)
case "calendars":
    commandCalendars(store)
case "events":
    commandEvents(store, request: request)
case "show":
    commandShow(store, request: request)
case "create":
    commandCreate(store, request: request)
case "update":
    commandUpdate(store, request: request)
case "delete":
    commandDelete(store, request: request)
default:
    fail("unknown operation: \(string(request, "operation") ?? "")")
}
