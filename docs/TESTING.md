# Testing

All fixtures are synthetic. No real calendar names, email addresses or events may be committed.

| Layer | Runs where | What it covers |
|---|---|---|
| unit | CI (Linux) | Time zones: Tokyo to Manila (minus 1 hour); all-day events never shifted; floating times labelled. Free-time interval arithmetic. Confirm tokens: single use, 5-minute TTL, bound to the exact change. Error mapping. Untrusted-text wrapping |
| fixtures | CI | Synthetic `.ics` files under `tests/fixtures/`: weekly series created in Asia/Manila; series with one edited occurrence (RECURRENCE-ID) and one deleted (EXDATE); multi-day all-day event; floating-time event |
| contract | CI | One suite run against the `fake` backend and the `caldav` backend. The CalDAV backend runs against a local Radicale server started in the CI job, so no iCloud account is needed (test tool only, not distributed). **Radicale passing does not prove iCloud compatibility** (python-caldav's docs don't mention iCloud); the opt-in integration run against your own iCloud `TEST` calendar is a launch requirement |
| auth (hosted) | CI | Unauthenticated request gets **401 with `WWW-Authenticate: Bearer resource_metadata=...`** (a header on a 200 is ignored by Claude); metadata `resource` equals the server URL exactly; token for a different GitHub user is rejected (FastMCP has no built-in allow-list, so this is our code); `/token` accepts form-encoded bodies and returns `invalid_grant` on a bad refresh token; PKCE S256 required. Checked with the MCP Inspector and then as a custom connector in Claude |
| Swift | CI (macOS) | `swift build` and `swift test` for helper JSON encoding and recurrence mapping. Runners have no Calendar access, so EventKit calls are not exercised |
| integration | **your Mac only**, opt-in | `pytest -m integration --calendar TEST` against EventKit |

## Integration test safety rules
1. Refuses to run unless the target calendar is named exactly `TEST`.
2. Creates events with UID prefix `icmcp-test-` and records every UID it creates.
3. Deletes only UIDs created in the same run, one at a time, through the same preview then confirm path.
4. Never calls delete without an ID. Never touches any other calendar.
5. Flow: create preview → confirm → verify by re-read → update (this occurrence, then whole series) → delete preview → confirm → verify gone.

## Status
Phase 3: only `tests/unit/test_package.py` exists. The suites above are built in Phase 4.
