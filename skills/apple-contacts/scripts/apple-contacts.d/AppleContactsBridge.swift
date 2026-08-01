import AddressBook
import Contacts
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

func value(_ object: [String: Any], _ key: String) -> Any? {
    guard let item = object[key], !(item is NSNull) else {
        return nil
    }
    return item
}

func string(_ object: [String: Any], _ key: String) -> String? {
    guard let item = value(object, key) else {
        return nil
    }
    let output = String(describing: item).trimmingCharacters(in: .whitespacesAndNewlines)
    return output.isEmpty ? nil : output
}

func bool(_ object: [String: Any], _ key: String, default fallback: Bool = false) -> Bool {
    guard let item = value(object, key) else {
        return fallback
    }
    if let typed = item as? Bool {
        return typed
    }
    return ["1", "true", "yes"].contains(String(describing: item).lowercased())
}

func dictionaries(_ object: [String: Any], _ key: String) -> [[String: Any]] {
    guard let raw = value(object, key) else {
        return []
    }
    guard let items = raw as? [Any] else {
        fail("\(key) must be an array")
    }
    var output: [[String: Any]] = []
    for (index, item) in items.enumerated() {
        guard let dictionary = item as? [String: Any] else {
            fail("\(key)[\(index)] must be an object")
        }
        output.append(dictionary)
    }
    return output
}

func dictionary(_ object: [String: Any], _ key: String) -> [String: Any]? {
    return value(object, key) as? [String: Any]
}

func contactKeys(includeNote: Bool = false) -> [CNKeyDescriptor] {
    var keys: [CNKeyDescriptor] = [
        CNContactIdentifierKey as CNKeyDescriptor,
        CNContactNamePrefixKey as CNKeyDescriptor,
        CNContactGivenNameKey as CNKeyDescriptor,
        CNContactMiddleNameKey as CNKeyDescriptor,
        CNContactFamilyNameKey as CNKeyDescriptor,
        CNContactPreviousFamilyNameKey as CNKeyDescriptor,
        CNContactNameSuffixKey as CNKeyDescriptor,
        CNContactNicknameKey as CNKeyDescriptor,
        CNContactOrganizationNameKey as CNKeyDescriptor,
        CNContactDepartmentNameKey as CNKeyDescriptor,
        CNContactJobTitleKey as CNKeyDescriptor,
        CNContactPhoneNumbersKey as CNKeyDescriptor,
        CNContactEmailAddressesKey as CNKeyDescriptor,
        CNContactPostalAddressesKey as CNKeyDescriptor,
        CNContactUrlAddressesKey as CNKeyDescriptor,
        CNContactSocialProfilesKey as CNKeyDescriptor,
        CNContactInstantMessageAddressesKey as CNKeyDescriptor,
        CNContactRelationsKey as CNKeyDescriptor,
        CNContactDatesKey as CNKeyDescriptor,
        CNContactBirthdayKey as CNKeyDescriptor,
    ]
    if includeNote {
        keys.append(CNContactNoteKey as CNKeyDescriptor)
    }
    return keys
}

func requestContactsAccess(_ store: CNContactStore) {
    let status = CNContactStore.authorizationStatus(for: .contacts)
    if status == .authorized {
        return
    }
    if status != .notDetermined {
        fail("Contacts access denied or restricted")
    }

    let semaphore = DispatchSemaphore(value: 0)
    var granted = false
    var accessError: Error?
    store.requestAccess(for: .contacts) { ok, error in
        granted = ok
        accessError = error
        semaphore.signal()
    }
    if semaphore.wait(timeout: .now() + 60) == .timedOut {
        fail("timed out waiting for Contacts access")
    }
    if !granted {
        fail("Contacts access denied: \(accessError?.localizedDescription ?? "unknown error")")
    }
}

func contactLabel(_ label: String?) -> String? {
    guard let raw = label?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty else {
        return nil
    }
    switch raw.lowercased() {
    case "home":
        return CNLabelHome
    case "work":
        return CNLabelWork
    case "other":
        return CNLabelOther
    case "mobile":
        return CNLabelPhoneNumberMobile
    case "iphone":
        return CNLabelPhoneNumberiPhone
    case "main":
        return CNLabelPhoneNumberMain
    case "homefax", "home fax":
        return CNLabelPhoneNumberHomeFax
    case "workfax", "work fax":
        return CNLabelPhoneNumberWorkFax
    case "pager":
        return CNLabelPhoneNumberPager
    default:
        return raw
    }
}

func displayName(_ contact: CNContact) -> String {
    let parts = [contact.namePrefix, contact.givenName, contact.middleName, contact.familyName, contact.nameSuffix]
        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        .filter { !$0.isEmpty }
    if !parts.isEmpty {
        return parts.joined(separator: " ")
    }
    if !contact.organizationName.isEmpty {
        return contact.organizationName
    }
    return contact.identifier
}

func labeled(_ label: String?) -> String {
    return label ?? ""
}

func postalAddressSummary(_ address: CNPostalAddress) -> [String: String] {
    return [
        "street": address.street,
        "city": address.city,
        "state": address.state,
        "postal_code": address.postalCode,
        "country": address.country,
        "iso_country_code": address.isoCountryCode,
        "sub_locality": address.subLocality,
    ]
}

func dateComponentSummary(_ components: DateComponents?) -> [String: Int] {
    guard let components else {
        return [:]
    }
    var output: [String: Int] = [:]
    if let year = components.year {
        output["year"] = year
    }
    if let month = components.month {
        output["month"] = month
    }
    if let day = components.day {
        output["day"] = day
    }
    return output
}

func contactSummary(_ contact: CNContact) -> [String: Any] {
    var output: [String: Any] = [
        "id": contact.identifier,
        "display_name": displayName(contact),
        "name_prefix": contact.namePrefix,
        "given_name": contact.givenName,
        "middle_name": contact.middleName,
        "family_name": contact.familyName,
        "previous_family_name": contact.previousFamilyName,
        "name_suffix": contact.nameSuffix,
        "nickname": contact.nickname,
        "organization_name": contact.organizationName,
        "department_name": contact.departmentName,
        "job_title": contact.jobTitle,
        "birthday": dateComponentSummary(contact.birthday),
        "phone_count": contact.phoneNumbers.count,
        "email_count": contact.emailAddresses.count,
        "address_count": contact.postalAddresses.count,
        "url_count": contact.urlAddresses.count,
        "social_profile_count": contact.socialProfiles.count,
        "instant_message_count": contact.instantMessageAddresses.count,
        "relation_count": contact.contactRelations.count,
        "date_count": contact.dates.count,
        "phones": contact.phoneNumbers.map { ["label": labeled($0.label), "value": $0.value.stringValue] },
        "emails": contact.emailAddresses.map { ["label": labeled($0.label), "value": String($0.value)] },
        "addresses": contact.postalAddresses.map { item in
            var output = postalAddressSummary(item.value)
            output["label"] = labeled(item.label)
            return output
        },
        "urls": contact.urlAddresses.map { ["label": labeled($0.label), "value": String($0.value)] },
        "social_profiles": contact.socialProfiles.map {
            [
                "label": labeled($0.label),
                "service": $0.value.service,
                "username": $0.value.username,
                "user_identifier": $0.value.userIdentifier,
                "url": $0.value.urlString,
            ]
        },
        "instant_messages": contact.instantMessageAddresses.map {
            ["label": labeled($0.label), "service": $0.value.service, "username": $0.value.username]
        },
        "relations": contact.contactRelations.map { ["label": labeled($0.label), "name": $0.value.name] },
        "dates": contact.dates.map {
            [
                "label": labeled($0.label),
                "year": $0.value.year,
                "month": $0.value.month,
                "day": $0.value.day,
            ]
        },
    ]
    if contact.isKeyAvailable(CNContactNoteKey) {
        output["note"] = contact.note
    } else {
        output["note_available"] = false
    }
    return output
}

func fetchContact(_ store: CNContactStore, _ identifier: String, includeNote: Bool = false) -> CNContact {
    do {
        let predicate = CNContact.predicateForContacts(withIdentifiers: [identifier])
        let contacts = try store.unifiedContacts(matching: predicate, keysToFetch: contactKeys(includeNote: includeNote))
        guard let contact = contacts.first else {
            fail("contact not found: \(identifier)")
        }
        return contact
    } catch {
        fail("unable to fetch contact \(identifier): \(error.localizedDescription)")
    }
}

func fetchContacts(_ store: CNContactStore, predicate: NSPredicate, includeNote: Bool = false, unifyResults: Bool = false) -> [CNContact] {
    let request = CNContactFetchRequest(keysToFetch: contactKeys(includeNote: includeNote))
    request.predicate = predicate
    request.unifyResults = unifyResults
    var contacts: [CNContact] = []
    do {
        try store.enumerateContacts(with: request) { contact, _ in
            contacts.append(contact)
        }
        return contacts
    } catch {
        fail("unable to fetch contacts: \(error.localizedDescription)")
    }
}

func groupMemberIds(_ store: CNContactStore, _ group: CNGroup) -> Set<String> {
    let contacts = fetchContacts(
        store,
        predicate: CNContact.predicateForContactsInGroup(withIdentifier: group.identifier),
        unifyResults: false
    )
    return Set(contacts.map { $0.identifier })
}

func removeMemberWithAddressBook(contactID: String, groupID: String) {
    guard let book = ABAddressBook.shared() else {
        fail("AddressBook fallback failed: unable to open address book")
    }
    guard let person = book.record(forUniqueId: contactID) as? ABPerson else {
        fail("AddressBook fallback failed: contact not found: \(contactID)")
    }
    guard let group = book.record(forUniqueId: groupID) as? ABGroup else {
        fail("AddressBook fallback failed: group not found: \(groupID)")
    }
    if !group.removeMember(person) {
        fail("AddressBook fallback failed: contact was not removed from group")
    }
    if !book.save() {
        fail("AddressBook fallback failed: save returned false")
    }
}

func allGroups(_ store: CNContactStore) -> [[String: Any]] {
    do {
        let containers = try store.containers(matching: nil)
        var seen: Set<String> = []
        var output: [[String: Any]] = []
        for container in containers {
            let predicate = CNGroup.predicateForGroupsInContainer(withIdentifier: container.identifier)
            for group in try store.groups(matching: predicate) {
                if seen.contains(group.identifier) {
                    continue
                }
                seen.insert(group.identifier)
                output.append([
                    "id": group.identifier,
                    "name": group.name,
                    "container_id": container.identifier,
                    "container_name": container.name,
                    "container_type": String(describing: container.type),
                ])
            }
        }
        return output.sorted { String(describing: $0["name"] ?? "") < String(describing: $1["name"] ?? "") }
    } catch {
        fail("unable to list groups: \(error.localizedDescription)")
    }
}

func fetchGroup(_ store: CNContactStore, _ identifier: String) -> CNGroup {
    do {
        let groups = try store.groups(matching: CNGroup.predicateForGroups(withIdentifiers: [identifier]))
        guard let group = groups.first else {
            fail("group not found: \(identifier)")
        }
        return group
    } catch {
        fail("unable to fetch group \(identifier): \(error.localizedDescription)")
    }
}

func dateComponents(from object: Any?) -> DateComponents? {
    if let components = object as? [String: Any] {
        var date = DateComponents()
        if let year = value(components, "year") as? NSNumber {
            date.year = year.intValue
        }
        if let month = value(components, "month") as? NSNumber {
            date.month = month.intValue
        }
        if let day = value(components, "day") as? NSNumber {
            date.day = day.intValue
        }
        return date.month == nil && date.day == nil && date.year == nil ? nil : date
    }
    guard let raw = object else {
        return nil
    }
    let parts = String(describing: raw).split(separator: "-").compactMap { Int($0) }
    if parts.count == 3 {
        return DateComponents(year: parts[0], month: parts[1], day: parts[2])
    }
    return nil
}

func validateDateComponentInput(_ object: Any?, field: String) -> DateComponents? {
    guard let object else {
        return nil
    }
    guard let components = dateComponents(from: object) else {
        fail("\(field) requires YYYY-MM-DD or an object with numeric year/month/day")
    }
    return components
}

func requireString(_ item: [String: Any], _ key: String, field: String, index: Int) -> String {
    guard let output = string(item, key) else {
        fail("\(field)[\(index)].\(key) is required")
    }
    return output
}

func firstString(_ item: [String: Any], _ keys: [String], field: String, index: Int) -> String {
    for key in keys {
        if let output = string(item, key) {
            return output
        }
    }
    let allowed = keys.joined(separator: ", ")
    fail("\(field)[\(index)] requires one of: \(allowed)")
}

func requireAnyAddressValue(_ item: [String: Any], field: String, index: Int) {
    for key in ["street", "city", "state", "postal_code", "country", "iso_country_code", "sub_locality"] {
        if string(item, key) != nil {
            return
        }
    }
    fail("\(field)[\(index)] requires at least one address value")
}

func validateContactPayloadKeys(_ payload: [String: Any]) {
    let allowed: Set<String> = [
        "name_prefix",
        "given_name",
        "middle_name",
        "family_name",
        "previous_family_name",
        "name_suffix",
        "nickname",
        "organization_name",
        "department_name",
        "job_title",
        "note",
        "birthday",
        "phones",
        "emails",
        "addresses",
        "postal_addresses",
        "urls",
        "social_profiles",
        "instant_messages",
        "relations",
        "dates",
    ]
    let unknown = payload.keys.filter { !allowed.contains($0) }.sorted()
    if !unknown.isEmpty {
        let unknownFields = unknown.joined(separator: ", ")
        fail("unknown contact field(s): \(unknownFields)")
    }
    if payload.keys.contains("addresses") && payload.keys.contains("postal_addresses") {
        fail("use addresses or postal_addresses, not both")
    }
}

func applyContactPayload(_ payload: [String: Any], to contact: CNMutableContact) {
    validateContactPayloadKeys(payload)
    if payload.keys.contains("name_prefix") { contact.namePrefix = string(payload, "name_prefix") ?? "" }
    if payload.keys.contains("given_name") { contact.givenName = string(payload, "given_name") ?? "" }
    if payload.keys.contains("middle_name") { contact.middleName = string(payload, "middle_name") ?? "" }
    if payload.keys.contains("family_name") { contact.familyName = string(payload, "family_name") ?? "" }
    if payload.keys.contains("previous_family_name") { contact.previousFamilyName = string(payload, "previous_family_name") ?? "" }
    if payload.keys.contains("name_suffix") { contact.nameSuffix = string(payload, "name_suffix") ?? "" }
    if payload.keys.contains("nickname") { contact.nickname = string(payload, "nickname") ?? "" }
    if payload.keys.contains("organization_name") { contact.organizationName = string(payload, "organization_name") ?? "" }
    if payload.keys.contains("department_name") { contact.departmentName = string(payload, "department_name") ?? "" }
    if payload.keys.contains("job_title") { contact.jobTitle = string(payload, "job_title") ?? "" }
    if payload.keys.contains("note") { contact.note = string(payload, "note") ?? "" }
    if payload.keys.contains("birthday") {
        contact.birthday = validateDateComponentInput(value(payload, "birthday"), field: "birthday")
    }

    if payload.keys.contains("phones") {
        contact.phoneNumbers = dictionaries(payload, "phones").enumerated().map { index, item in
            let number = requireString(item, "value", field: "phones", index: index)
            return CNLabeledValue(label: contactLabel(string(item, "label")), value: CNPhoneNumber(stringValue: number))
        }
    }
    if payload.keys.contains("emails") {
        contact.emailAddresses = dictionaries(payload, "emails").enumerated().map { index, item in
            let address = requireString(item, "value", field: "emails", index: index)
            return CNLabeledValue(label: contactLabel(string(item, "label")), value: address as NSString)
        }
    }
    if payload.keys.contains("addresses") || payload.keys.contains("postal_addresses") {
        let key = payload.keys.contains("postal_addresses") ? "postal_addresses" : "addresses"
        contact.postalAddresses = dictionaries(payload, key).enumerated().map { index, item in
            requireAnyAddressValue(item, field: key, index: index)
            let address = CNMutablePostalAddress()
            address.street = string(item, "street") ?? ""
            address.city = string(item, "city") ?? ""
            address.state = string(item, "state") ?? ""
            address.postalCode = string(item, "postal_code") ?? ""
            address.country = string(item, "country") ?? ""
            address.isoCountryCode = string(item, "iso_country_code") ?? ""
            address.subLocality = string(item, "sub_locality") ?? ""
            return CNLabeledValue(label: contactLabel(string(item, "label")), value: address)
        }
    }
    if payload.keys.contains("urls") {
        contact.urlAddresses = dictionaries(payload, "urls").enumerated().map { index, item in
            let url = firstString(item, ["value", "url"], field: "urls", index: index)
            return CNLabeledValue(label: contactLabel(string(item, "label")), value: url as NSString)
        }
    }
    if payload.keys.contains("social_profiles") {
        contact.socialProfiles = dictionaries(payload, "social_profiles").enumerated().map { index, item in
            _ = firstString(item, ["username", "url", "user_identifier"], field: "social_profiles", index: index)
            let profile = CNSocialProfile(
                urlString: string(item, "url"),
                username: string(item, "username"),
                userIdentifier: string(item, "user_identifier"),
                service: string(item, "service")
            )
            return CNLabeledValue(label: contactLabel(string(item, "label")), value: profile)
        }
    }
    if payload.keys.contains("instant_messages") {
        contact.instantMessageAddresses = dictionaries(payload, "instant_messages").enumerated().map { index, item in
            let username = firstString(item, ["username", "value"], field: "instant_messages", index: index)
            let address = CNInstantMessageAddress(username: username, service: string(item, "service") ?? "")
            return CNLabeledValue(label: contactLabel(string(item, "label")), value: address)
        }
    }
    if payload.keys.contains("relations") {
        contact.contactRelations = dictionaries(payload, "relations").enumerated().map { index, item in
            let name = requireString(item, "name", field: "relations", index: index)
            return CNLabeledValue(label: contactLabel(string(item, "label")), value: CNContactRelation(name: name))
        }
    }
    if payload.keys.contains("dates") {
        contact.dates = dictionaries(payload, "dates").enumerated().map { index, item in
            let rawDate = value(item, "date") ?? value(item, "value") ?? item
            guard let date = dateComponents(from: rawDate) else {
                fail("dates[\(index)] requires date/value or year/month/day")
            }
            return CNLabeledValue(label: contactLabel(string(item, "label")), value: date as NSDateComponents)
        }
    }
}

func executeSave(_ store: CNContactStore, _ request: CNSaveRequest, confirm: Bool, noteRequested: Bool = false) {
    if !confirm {
        return
    }
    do {
        try store.execute(request)
    } catch {
        let nsError = error as NSError
        if noteRequested && nsError.domain == NSCocoaErrorDomain && nsError.code == 134092 {
            fail("Contacts save failed while writing note. This macOS Contacts.framework path rejected note mutation; direct AddressBook DB fallback is forbidden.")
        }
        fail("Contacts save failed: \(error.localizedDescription)")
    }
}

func response(_ operation: String, _ confirm: Bool, _ values: [String: Any]) -> [String: Any] {
    var output = values
    output["operation"] = operation
    output["dry_run"] = !confirm
    output["status"] = "ok"
    return output
}

let args = CommandLine.arguments
if args.count != 2 && args.count != 4 {
    fail("usage: AppleContactsBridge <request.json> [response.json error.txt]")
}

let request = jsonObject(from: args[1])
guard let operation = string(request, "operation") else {
    fail("missing operation")
}
let confirm = bool(request, "confirm")
let store = CNContactStore()

if operation == "status" {
    requestContactsAccess(store)
    printJSON([
        "status": "ok",
        "operation": operation,
        "authorization_status": CNContactStore.authorizationStatus(for: .contacts).rawValue,
    ])
    exit(0)
}

requestContactsAccess(store)

switch operation {
case "groups.list":
    printJSON(response(operation, confirm, ["groups": allGroups(store)]))

case "group.members":
    guard let groupID = string(request, "id") else {
        fail("missing group id")
    }
    let group = fetchGroup(store, groupID)
    let contacts = fetchContacts(
        store,
        predicate: CNContact.predicateForContactsInGroup(withIdentifier: group.identifier),
        unifyResults: false
    )
    printJSON(
        response(
            operation,
            confirm,
            [
                "group": ["id": group.identifier, "name": group.name],
                "members": contacts.map { contactSummary($0) },
            ]
        )
    )

case "contact.create":
    guard let payload = dictionary(request, "contact") else {
        fail("missing contact payload")
    }
    let contact = CNMutableContact()
    applyContactPayload(payload, to: contact)
    let saveRequest = CNSaveRequest()
    saveRequest.add(contact, toContainerWithIdentifier: string(request, "container_id"))
    executeSave(store, saveRequest, confirm: confirm, noteRequested: payload.keys.contains("note"))
    printJSON(response(operation, confirm, ["contact": contactSummary(contact), "contact_id": contact.identifier]))

case "contact.update":
    guard let identifier = string(request, "id") else {
        fail("missing contact id")
    }
    guard let payload = dictionary(request, "contact") else {
        fail("missing contact payload")
    }
    let before = fetchContact(store, identifier, includeNote: payload.keys.contains("note"))
    let mutable = before.mutableCopy() as! CNMutableContact
    applyContactPayload(payload, to: mutable)
    let saveRequest = CNSaveRequest()
    saveRequest.update(mutable)
    executeSave(store, saveRequest, confirm: confirm, noteRequested: payload.keys.contains("note"))
    printJSON(response(operation, confirm, ["before": contactSummary(before), "after": contactSummary(mutable), "contact_id": mutable.identifier]))

case "contact.delete":
    guard let identifier = string(request, "id") else {
        fail("missing contact id")
    }
    let contact = fetchContact(store, identifier)
    let mutable = contact.mutableCopy() as! CNMutableContact
    let saveRequest = CNSaveRequest()
    saveRequest.delete(mutable)
    executeSave(store, saveRequest, confirm: confirm)
    printJSON(response(operation, confirm, ["contact": contactSummary(contact), "contact_id": contact.identifier]))

case "group.create":
    guard let name = string(request, "name") else {
        fail("missing group name")
    }
    let group = CNMutableGroup()
    group.name = name
    let saveRequest = CNSaveRequest()
    saveRequest.add(group, toContainerWithIdentifier: string(request, "container_id"))
    executeSave(store, saveRequest, confirm: confirm)
    printJSON(response(operation, confirm, ["group": ["id": group.identifier, "name": group.name], "group_id": group.identifier]))

case "group.update":
    guard let identifier = string(request, "id") else {
        fail("missing group id")
    }
    guard let name = string(request, "name") else {
        fail("missing group name")
    }
    let group = fetchGroup(store, identifier)
    let mutable = group.mutableCopy() as! CNMutableGroup
    let before = ["id": group.identifier, "name": group.name]
    mutable.name = name
    let saveRequest = CNSaveRequest()
    saveRequest.update(mutable)
    executeSave(store, saveRequest, confirm: confirm)
    printJSON(response(operation, confirm, ["before": before, "after": ["id": mutable.identifier, "name": mutable.name], "group_id": mutable.identifier]))

case "group.delete":
    guard let identifier = string(request, "id") else {
        fail("missing group id")
    }
    let group = fetchGroup(store, identifier)
    let mutable = group.mutableCopy() as! CNMutableGroup
    let saveRequest = CNSaveRequest()
    saveRequest.delete(mutable)
    executeSave(store, saveRequest, confirm: confirm)
    printJSON(response(operation, confirm, ["group": ["id": group.identifier, "name": group.name], "group_id": group.identifier]))

case "group.addContact":
    guard let contactID = string(request, "contact_id"), let groupID = string(request, "group_id") else {
        fail("missing contact_id or group_id")
    }
    let contact = fetchContact(store, contactID)
    let group = fetchGroup(store, groupID)
    let saveRequest = CNSaveRequest()
    saveRequest.addMember(contact, to: group)
    executeSave(store, saveRequest, confirm: confirm)
    if confirm && !groupMemberIds(store, group).contains(contactID) {
        fail("group add-contact failed postcondition: contact is not a group member after save")
    }
    printJSON(response(operation, confirm, ["contact": contactSummary(contact), "group": ["id": group.identifier, "name": group.name]]))

case "group.removeContact":
    guard let contactID = string(request, "contact_id"), let groupID = string(request, "group_id") else {
        fail("missing contact_id or group_id")
    }
    let group = fetchGroup(store, groupID)
    let members = fetchContacts(
        store,
        predicate: CNContact.predicateForContactsInGroup(withIdentifier: group.identifier),
        unifyResults: false
    )
    let contact = members.first { $0.identifier == contactID } ?? fetchContact(store, contactID)
    let saveRequest = CNSaveRequest()
    saveRequest.removeMember(contact, from: group)
    executeSave(store, saveRequest, confirm: confirm)
    if confirm && groupMemberIds(store, group).contains(contactID) {
        removeMemberWithAddressBook(contactID: contactID, groupID: groupID)
    }
    if confirm && groupMemberIds(store, group).contains(contactID) {
        fail("group remove-contact failed postcondition: contact is still a group member after save")
    }
    printJSON(response(operation, confirm, ["contact": contactSummary(contact), "group": ["id": group.identifier, "name": group.name]]))

default:
    fail("unknown operation: \(operation)")
}
