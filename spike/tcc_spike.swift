// Phase 3 spike: does macOS show a Calendar permission prompt for THIS binary,
// and can it then read? READ-ONLY. Prints counts only, never event titles,
// notes, attendees or calendar names. Writes nothing.
import EventKit
import Foundation

let store = EKEventStore()
let done = DispatchSemaphore(value: 0)
var granted = false
var requestError: String?

print("macOS: \(ProcessInfo.processInfo.operatingSystemVersionString)")
print("status before: \(EKEventStore.authorizationStatus(for: .event).rawValue)  (0=notDetermined 1=restricted 2=denied 3=fullAccess 4=writeOnly)")

if #available(macOS 14.0, *) {
    store.requestFullAccessToEvents { ok, error in
        granted = ok
        requestError = error.map { "\($0)" }
        done.signal()
    }
} else {
    store.requestAccess(to: .event) { ok, error in
        granted = ok
        requestError = error.map { "\($0)" }
        done.signal()
    }
}

if done.wait(timeout: .now() + 120) == .timedOut {
    print("RESULT: timed out after 120 s waiting for the permission dialog")
    exit(2)
}

print("access granted: \(granted)")
if let requestError { print("request error: \(requestError)") }
print("status after: \(EKEventStore.authorizationStatus(for: .event).rawValue)")

guard granted else {
    print("RESULT: NOT GRANTED. Check System Settings > Privacy & Security > Calendars.")
    exit(1)
}

let calendars = store.calendars(for: .event)
let bySource = Dictionary(grouping: calendars, by: { $0.source.sourceType.rawValue })
print("calendars: \(calendars.count)")
for (sourceType, group) in bySource.sorted(by: { $0.key < $1.key }) {
    print("  sourceType \(sourceType) (0=local 1=exchange 2=calDAV/iCloud 3=mobileMe 4=subscribed 5=birthdays): \(group.count)")
}
print("writable calendars: \(calendars.filter { $0.allowsContentModifications }.count)")

let now = Date()
let weekAhead = now.addingTimeInterval(7 * 24 * 3600)
let predicate = store.predicateForEvents(withStart: now, end: weekAhead, calendars: nil)
let events = store.events(matching: predicate)
print("events in next 7 days: \(events.count)")
print("recurring among them: \(events.filter { $0.hasRecurrenceRules }.count)")
print("all-day among them: \(events.filter { $0.isAllDay }.count)")
print("RESULT: OK (read succeeded)")
