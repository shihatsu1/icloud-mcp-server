# DESIGN: iCloud Calendar MCP server (Phase 1)

Status: draft for approval. Written 2026-10-21. Nothing is built.
Convention: **UNVERIFIED** = not confirmed against current official documentation.

## 1. Goals and non-negotiables
- Claude Desktop / Claude Code on the Mac (LOCAL) and, optionally, ChatGPT plus claude.ai/iPhone (HOSTED).
- Apple ID password is never used. Only an app-specific password, and only in HOSTED mode.
- Read-only by default. Every create/update/delete shows an exact diff and needs explicit confirmation. No bulk delete.
- Never hand-roll RRULE logic.

## 2. LOCAL vs HOSTED

| | LOCAL (stdio, EventKit) | HOSTED (HTTPS, CalDAV) |
|---|---|---|
| Serves | Claude Desktop, Claude Code | ChatGPT, claude.ai, Claude iPhone (connectors added on web) |
| Credential | **None.** macOS grants calendar access to the process | App-specific password on a server |
| Calendars | Everything synced to the Mac's Calendar.app (iCloud, plus Google/Exchange if present) | iCloud only |
| Recurrence/time zones | EventKit does the work | Needs `icalendar` + recurrence library |
| Needs Mac awake | Yes | No |
| Auth layer to build | None | OAuth 2.1 + PKCE (required by ChatGPT) |
| Cost | $0 | about $2-5/month plus a domain (see §6) |
| Worst case if compromised | Anything running as your user could read your calendar | Attacker gets the password: calendar **and iCloud Mail and Contacts** |

**Recommendation: build LOCAL first.** It meets every credential rule by having no credential, covers the Claude surfaces you actually named, and is where the tool layer gets proven. HOSTED waits for your decision (§8), mainly because its only extra reach is ChatGPT, and ChatGPT's write support for your plan is unconfirmed.

## 3. LOCAL mode design

### 3.1 Language and structure
Recommended: **Python MCP core (FastMCP) + a small signed Swift helper that does the EventKit calls**, rather than a pure-Swift server.
- Why Swift for EventKit: EventKit is a native framework, and the helper can carry its own `Info.plist`, so macOS asks for calendar permission for *that binary* by name. Python/Node scripts reportedly inherit their parent app's permission and fail to prompt unless launched from Terminal (Omar-V2/mcp-ical README; **UNVERIFIED on macOS 27**).
- Why Python for the MCP layer: the same tool layer (schemas, preview/confirm logic, time-zone formatting, free-time algorithm) can later sit on a CalDAV backend for HOSTED, so we write it once. A backend interface `CalendarBackend` has two implementations.
- Trade-off I'm accepting: two processes and a JSON-over-stdin/stdout contract between them, instead of one binary. The alternative, a pure-Swift server on the [official MCP Swift SDK](https://github.com/modelcontextprotocol/swift-sdk), is simpler to ship. But that SDK appeared to be pre-1.0 (README references 0.11.0), and I couldn't confirm it supports tool annotations (`readOnlyHint`/`destructiveHint`). Both are **UNVERIFIED**; Phase 4 starts with a one-hour spike on this.
- Recurrence: EventKit's own expansion for LOCAL. For HOSTED, `icalendar` + `recurring-ical-events`. Note `recurring-ical-events` is **LGPL-3.0-or-later** ([PyPI](https://pypi.org/project/recurring-ical-events/), v3.8.2, Apr 30 2026). Using it as an unmodified dependency is normally fine for an MIT project, but it affects the license choice and any bundled distribution (decision in Phase 3).

### 3.2 macOS permission prompt (what you will see)
Expected flow, based on Apple's EventKit API (the Apple docs page returned no body to me, so the specifics below are from my own knowledge and **UNVERIFIED** against Apple's current docs):
1. The helper calls `EKEventStore.requestFullAccessToEvents()` (macOS 14+). "Write-only" access can't read events back, so it's useless here.
2. macOS shows a dialog quoting the `NSCalendarsFullAccessUsageDescription` string from the helper's Info.plist. You click Allow.
3. The grant appears in **System Settings → Privacy & Security → Calendars** under the helper's name. Revoke there at any time.
4. To get the prompt reliably, `bin/calendar-helper --authorize` is run once from Terminal during setup. If no prompt appears, the fix is to add it manually in that settings pane.

Open risks: whether the grant attaches to the helper or to "Claude" when launched by Claude Desktop, and whether macOS 27 changed any of this. The spike tests this on your Mac.

### 3.3 Safe-write protocol (works in every client)
MCP "elicitation" (a server asking you to confirm) is not reliable: third-party reports say Claude Desktop does not support it (**UNVERIFIED**). So confirmation lives in the protocol, not the client:
1. `create_event` / `update_event` / `delete_event` **never write**. They return a `preview` (exact before/after fields, calendar, time zone, series-or-occurrence scope) and a `confirm_token`.
2. `confirm_change(confirm_token)` is the only function that writes. The token is single-use, expires in 5 minutes, and is bound to a hash of the exact change. Changing any field invalidates it.
3. The server only registers the write tools if started with `--allow-writes`. Default is read-only.
4. Writes are restricted to an allow-list of calendars (`--writable-calendars "Work,TEST"`). Everything else rejects writes with a clear error.
5. `delete_event` takes one event ID. There is no delete-by-query, no list input, no series delete without the explicit scope question (§5). A hard cap of 1 delete per confirmation.
6. Tool annotations (`readOnlyHint`, `destructiveHint`) set honestly so the client's own approval prompt acts as a second layer.

Limits of this design: a prompt-injected event description ("ignore previous instructions, delete...") could still make the model *request* a change. The preview plus your confirmation is the defence, so previews must display the real values and the model must be told to show them verbatim. Event text returned to the model is wrapped and labelled as untrusted data.

### 3.4 EventKit pitfalls to design around
- Deleting a *later occurrence* of a recurring event with the "this event" span can silently act on the series start while reporting success (documented in FradSer/mcp-server-apple-events README; I haven't reproduced it). We'll address occurrences by `eventIdentifier` + occurrence start, then **re-read after every write** and fail loudly if the result differs from the preview.
- Same-named calendars in different sources (iCloud vs "On My Mac"): always identify calendars by ID plus source name.
- Events can be read-only (subscribed calendars, invitations you don't own): report as such rather than failing.

## 4. HOSTED mode design (summary; build only if you approve)

### 4.1 Stack
Python, FastMCP, `caldav` (CalDAV client), `icalendar` (parse/emit), `recurring-ical-events` (expansion). No custom RRULE code.

### 4.2 Auth layer (so only you can use it)
ChatGPT requires ([Apps SDK auth](https://developers.openai.com/apps-sdk/build/auth), undated page; **recheck at build time**): OAuth 2.1 authorization-code + PKCE (S256), protected-resource metadata, and one of CIMD / DCR / predefined client. It does not support static API keys. [FastMCP](https://gofastmcp.com/servers/auth/authentication) offers `OAuthProxy` (e.g. GitHub login) and token verification. Plan: single-user login via an identity provider, with the server **rejecting any account except your own** (explicit allow-list of one subject ID). Whether FastMCP can enforce that restriction directly is **UNVERIFIED**; if not, we add a check in the token-verification step. Important: FastMCP's default is *no auth*, so tests must assert that an unauthenticated request gets 401.

### 4.3 Where the password lives
Hosting platform's secret store (encrypted at rest, injected as an env var at runtime). Never in the repo, image, logs, or `.env` files that are committed. Locally for development: macOS Keychain.

### 4.4 Hosting options
| Option | Cost | Notes |
|---|---|---|
| Fly.io | about $1.94/month for shared-cpu-1x 256MB, per-second billing ([pricing](https://fly.io/docs/about/pricing/); free allowance unclear; older docs show ~$2.02) | Secrets store built in; may need more RAM than 256MB |
| Cloudflare Workers | Free plan: 100,000 requests/day, 1,000/min burst; CPU limit 10 ms (official table; a blog says 30 ms) | Python on Workers is limited, so TypeScript would be needed; `caldav` libs differ. Not recommended for this stack |
| A home Mac/Raspberry Pi + tunnel | $0 + hardware | Credential stays in your home; availability depends on your network. **UNVERIFIED** that ChatGPT's "Secure MCP Tunnel" fits |
Recommendation if HOSTED is built: Fly.io. Prices change; recheck before committing.

### 4.5 Blast radius if the server is compromised
- The app-specific password grants access to iCloud **Calendar, Contacts and Mail** (CalDAV/CardDAV/IMAP/SMTP) for your Apple Account. An attacker can read mail, send mail as you, and read/modify/delete calendar and contacts. It does *not* grant access to your Apple ID password, iCloud Drive, or Photos, as far as I know (**UNVERIFIED**: Apple's page doesn't enumerate scope).
- Apple's support page ([Oct 8 2025](https://support.apple.com/en-us/102654)) says: up to 25 active app-specific passwords; remove one or all at account.apple.com → Sign-In and Security → App-Specific Passwords; changing your Apple Account password revokes all of them.
- Mitigations: a dedicated password labelled for this server only; read-only tools by default; server logs never include credentials or event bodies; alert/rotate procedure documented in SECURITY.md; a separate Apple Account holding a *shared* copy of the calendars would reduce exposure but adds sync complexity (considered, not recommended initially).
- Residual risk you should accept knowingly: this credential can't be limited to calendar only. That's the main argument for not building HOSTED unless ChatGPT access matters to you.

## 5. Time zones and recurrence rules (cross-cutting)
- Every returned event has `start`, `end`, `timezone` (IANA, e.g. `Asia/Tokyo`), `all_day`, and also `start_local_display` in **both** Asia/Tokyo and Asia/Manila (configurable pair). Japan and the Philippines have no DST, but the code must not assume that for other zones in events.
- **All-day events** are dates, not instants: returned as `YYYY-MM-DD` with `all_day: true` and `timezone: null` (floating). They are never shifted when converting zones.
- **Floating times** (no zone) are labelled `floating` and interpreted in your default zone, with that stated in the output.
- Recurring series: search returns expanded occurrences with `series_id` + `occurrence_start`. `update_event` / `delete_event` on a recurring event *requires* `scope: "this_occurrence" | "whole_series"`; if missing, the tool returns a question instead of a preview. "This and future" is excluded in v1 to limit risk.
- Server stores nothing about your events. No cache on disk in v1.

## 6. Error model
Stable error codes returned as text the model can relay: `PERMISSION_DENIED` (with the exact Settings path), `ICLOUD_UNREACHABLE`, `CREDENTIALS_EXPIRED` (HOSTED: points to revoke/regenerate steps), `CALENDAR_READ_ONLY`, `TOKEN_EXPIRED`, `PREVIEW_MISMATCH`, `AMBIGUOUS_SCOPE`.

## 7. Risks
- TCC attribution on macOS 27 (spike first).
- Pre-1.0 SDKs in either language.
- Prompt injection through event text (mitigated, not eliminated).
- LGPL dependency in HOSTED.
- I can't run any Mac-side code from this container; you must run the spike and demo on your Mac.

## 8. Decisions
Recorded 2026-10-21:
1. **Architecture:** Python core + Swift EventKit helper (accepted by "do your best recommendation").
2. **HOSTED:** will be built. You have ChatGPT Pro and ChatGPT access is worth it to you. The mail-capable-password risk in §4.5 is accepted.
3. **Write protocol:** two-step preview/confirm approved. `--allow-writes` and calendar allow-list defaults stand.
4. **Mac spike:** still pending your answer. It must pass before any Phase 4 code.

**Open risk added:** whether ChatGPT **Pro** can use custom MCP connectors with write tools is **UNVERIFIED**. OpenAI's Sep 2025 announcement said Plus/Pro; later secondary sources say write MCP is Business/Enterprise/Edu only; OpenAI's help page blocked my fetches. Consequence: HOSTED ships read-only first, and writes are enabled only after you confirm in ChatGPT (Developer mode) that a write tool is allowed on your account.
