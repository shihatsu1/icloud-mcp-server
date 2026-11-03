# Project assessment — 3 November 2026

Reviewed local commit `717b2f3`. Existing untracked AGENTS.md was read and left untouched. No production settings, events, secrets, commits or remote branches were changed.

## Conclusion

A working personal calendar MCP with a sensible separation of backend, service and transport. Owner-only authentication, encrypted OAuth storage, off-by-default writes, explicit recurring scope, preview tokens and local CalDAV contract tests are strong foundations. Reliability and write correctness should come before new features. This is not full Google Calendar parity: invitations/RSVP, calendar management, moves, this-and-future recurrence edits, ChatGPT scopes and Mail are absent or deferred.

## Updated scope decision — 3 November 2026

After this assessment, the user chose native ChatGPT iCloud Calendar and iCloud Mail plugins. Custom ChatGPT support and its testing are now out of scope, rather than an outstanding feature gap. The improvement prompt instructs removal of ChatGPT-specific callback defaults, setup instructions, tests and roadmap tasks while preserving shared MCP/OAuth functionality and Claude support. Native plugins were not tested in this review. Custom Mail remains out of scope. Implementation code has not yet been changed.

## Fresh verification

- Existing suite: 210 passed, 5 integration tests deselected, 44.19 seconds. Initial restricted run failed because local socket binding and file writes were restricted; the permitted rerun passed.
- Ruff: all checks passed with the documented EXE002 exclusion.
- Four isolated offline probes reproduced the issues below.
- An additional local Radicale test reproduced deletion after a concurrent external edit. No real iCloud writes were run.
- Temporary evidence: /tmp/icloud-assessment-pytest.log, /tmp/icloud_assessment_probes.py, /tmp/test_icloud_assessment_race.py, /tmp/icloud-race-result.log. These temporary files are not durable project tests.

## Confirmed findings, ordered by priority

### High: whole-event deletion is unconditional

`backends/caldav_backend.py:338-340` checks the version in `_open`, then calls `obj.delete()` with no condition. Installed python-caldav explicitly defaults to an unconditional DELETE. A local server test changed the event after `_open` returned and before DELETE; the external edit was deleted successfully. Require a strong server-enforced precondition and fail closed when no usable version is available. Also check update PUT preconditions: save already supports conditional requests, so do not assume it has the same default as delete.

### High: guest safeguard inspects only the master

`core/service.py:397` and `:450` inspect `master`, not the selected occurrence or every component affected by a series operation. An isolated fake-backend probe created a master without attendees and an occurrence with attendees: preview_delete issued a token for the occurrence. Check actual targets and relevant overrides, plus organizer/scheduling metadata, and recheck at confirmation with concurrency protection. This demonstrates a guard bypass; actual email delivery was not tested.

### Medium: repeat verification ignores UNTIL

`core/changes.py:287-296` compares frequency, interval, weekday, month-day and count but omits until. Two events ending on 5 December and 30 December produced no differences. Normalize date versus UTC UNTIL correctly and verify all supported recurrence fields; reject unsupported semantics rather than asserting success.

### Medium: removing recurrence leaves extra repeat dates

`backends/caldav_backend.py:_apply` removes RRULE but preserves RDATE. An in-memory VEVENT retained RDATE after applying recurrence=None. An event with such dates can continue recurring. Define repeat-removal behavior for RDATE/EXDATE/EXRULE/overrides and verify expanded instances.

### Medium: approval preview is not exact for long notes

`changes.py` accepts 5,000 note characters, but `service.py:_fields` truncates using MAX_NOTES=4,000. A 4,500-character request produced a 4,000-character preview while retaining the full write payload. Align input/display limits or provide an explicit complete review mechanism. Preserve the distinction between untrusted calendar text and instructions in preview fields too.

## Important engineering gaps

- Confirmation tokens are memory-only and consumed before I/O. Restarts safely invalidate pending approvals, but a lost response after a successful write has no outcome lookup. Creation chooses its UID during execution; repeating a newly previewed request can create duplicates. Use stable operation IDs and result reconciliation, and distinguish not-applied, applied and unknown outcomes. Do not blindly retry writes.
- Name-based calendar allow-lists are ambiguous when names duplicate or change. Prefer stable calendar IDs, with explicit migration of existing configuration. Source write permissions are currently represented too optimistically.
- `_event_by_uid` catches broad DAV errors and may scan the entire calendar on lookup failures; `_urls` is unbounded and the backend serializes operations under one lock. Bound caches, classify failures, and measure latency and lookup counts before optimizing.
- Warm Scheduler requests reduce ordinary scale-to-zero cold starts but cannot guarantee that restarts/replacements never happen. Measure discovery, OAuth, tool and CalDAV latency separately; make alerting actionable and verify actual billing before cost claims.
- Integration cleanup records IDs only after confirm returns. If creation succeeds but read-back fails, the created object is not in the ledger. Cleanup also swallows all CalendarError failures. Preassign test operation IDs, persist a restricted ledger and report leftover resources precisely.
- CI runs one automatically chosen Python version, whereas the container uses 3.13 and project metadata permits 3.11+. Test supported versions and locked production dependencies. Add type checking gradually at backend/write boundaries; current dictionaries and Any weaken operation guarantees.
- Authentication wraps private FastMCP internals, so upgrades need compatibility tests. Review logs for OAuth query leakage across platform request logging, not just application access logs. Owner-only identity checks are useful; the preview token itself does not prove a human clicked approval.
- README advertises a local EventKit route whose helper is only a placeholder/spike. Version remains 0.0.0. AGENTS.md mechanically substitutes Codex for Claude in product-specific statements (including a supposed Codex Chrome extension and timeout), which should be corrected rather than treated as verified facts.
- Deployment setup mixes reproducible setup with manual production state. Separate bootstrap from routine deploys, preserve intentional settings, document rollback, and use retention policies that keep a usable rollback image. Do not delete every old revision/image simply to minimize clutter.

## Recommended sequence

1. Fix deletion concurrency and guest checks with failing regression tests.
2. Correct recurrence changes, comparison and preview fidelity.
3. Add operation outcome reconciliation and reliable test cleanup.
4. Improve schemas, calendar identity, bounded lookup behavior, telemetry and deployment reproducibility.
5. Correct documentation and publish a supported-feature matrix. Add further calendar features only after these foundations pass.

Production status and costs were not rechecked in this assessment. Historical successful integration tests are recorded in docs/TESTING.md, not claimed as newly executed here.
