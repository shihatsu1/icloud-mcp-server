# Supported, deferred and out-of-scope features

Reviewed 2026-11-03 on the `hardening-2026-11` branch. "Tested" means automated tests against fakes and a local Radicale CalDAV server unless stated; **passing tests do not prove iCloud or Google Calendar parity** and do not remove prompt-injection risk.

| Area | Status | Notes |
|---|---|---|
| Read: list calendars, search (recurring expanded), get event, find free time | Supported | iCloud via CalDAV; text search and free/busy are computed here (iCloud's server-side versions are unreliable) |
| Create, update, delete events and single occurrences | Supported, off by default | `ALLOW_WRITES` plus an allow-list; preview, then `confirm_change` |
| Whole-series and one-occurrence edits | Supported | The user must choose; no "this and following" |
| Removing a repeat | Supported | Also removes RDATE/EXRULE/EXDATE; refused if edited occurrences exist |
| Changing a repeat rule | Supported for simple rules | Refused when the series has RDATE/EXRULE or edited occurrences |
| Alarms, location, notes, all-day, time zones | Supported | Notes up to 4,000 characters, shown in full in previews |
| Calendar allow-list | By stable ID (`WRITABLE_CALENDAR_IDS`) or by name (`WRITABLE_CALENDARS`) | Names shared by two calendars are refused for writes |
| Concurrent-edit protection | Supported | Strong `If-Match` on updates and deletes; fails closed without one (`VERSION_UNAVAILABLE`). Behavior on real iCloud: **UNVERIFIED** until the opt-in run (`scripts/probe_icloud.py --deep`) |
| Lost-answer handling | Supported | `operation_id`, applied / not_applied / unknown, replay, reconcile by reading. Journal is memory-only (lost on restart) |
| Events with guests or organizer data | Deliberately refused for update/delete | Checked on every component of the stored resource, at preview and at write time |
| Attendees / invitations, RSVP (`respond_to_event`) | Deferred | iCloud may email guests |
| Calendar create/rename/delete, moving an event between calendars | Not supported | |
| "This and following" recurrence edits, editing RDATE/EXRULE | Not supported | |
| Attachments, conferencing links, categories beyond what is preserved | Not supported | Unknown properties are preserved on edit, not editable |
| Mail | Out of scope | Design notes only (`docs/MAIL.md`). The owner uses ChatGPT's native iCloud Mail plugin separately |
| ChatGPT (custom connector) | Out of scope (removed 2026-11-03) | The owner uses ChatGPT's native iCloud plugins separately. Default ChatGPT OAuth callbacks were removed; `EXTRA_REDIRECT_URIS` still exists for explicit additions |
| Local EventKit mode | Not built | `helper/` is a placeholder; `spike/` only tests macOS permission |
| Clients | Claude (claude.ai, mobile, Desktop) | Generic MCP/OAuth: CIMD, dynamic registration, hashed URL-shaped client IDs, encrypted state |
| Cold starts | Mitigated, not guaranteed | Cloud Scheduler ping every 5 minutes; instance replacement or scale-to-zero can still cause 7-22 s starts (Claude allows OAuth endpoints 10 s). Per-call timing is logged (`timing op=...`) |

## Known limits that tests cannot settle
- Prompt injection: calendar text is labelled `untrusted` and previews must be shown to the user, but a model can still be misled; the human preview is the control.
- The preview token proves a preview existed for this sign-in, not that a human clicked approve.
- The iCloud app-specific password cannot be scoped to calendar only (docs/DESIGN.md §4.5).
- A concurrent edit made by a client that does not use ETags, or a change inside the window between the server's check and write, is the server's responsibility.
