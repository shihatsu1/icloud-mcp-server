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

**Recommendation (revised 2026-10-21): build HOSTED first; LOCAL is optional and later.** The original plan was LOCAL first. That changed when the goal became "prompt it from anywhere in Claude, including the browser and phone, plus ChatGPT":
- Anthropic's custom-connector article says connections come from **Anthropic's cloud**, so the server must be reachable on the public internet ([support.claude.com](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp); page undated, "updated over 2 weeks ago"). The same article says local servers configured in `claude_desktop_config.json` are not available on claude.ai. So a LOCAL server cannot serve browser Claude.
- ChatGPT needs a public HTTPS MCP server with OAuth (§4.2).
- LOCAL only serves apps installed on the Mac that can launch programs (Claude Desktop, Claude Code). It stays a possible later backend behind the same `CalendarBackend` interface.

Cost accepted: HOSTED needs the app-specific password on a server (§4.5). Scope: **iCloud calendars only.** Your Mac also holds Exchange calendars; CalDAV cannot see those, and they are out of scope.

## 3. LOCAL mode design (optional, later)

Spike result (2026-10-21, your Mac, run from Terminal): on macOS 27.0.1 (build 26A434) a small ad-hoc-signed Swift binary with an embedded `Info.plist` triggered the Calendar prompt (status 0 to 3, full access), read 15 calendars (6 Exchange, 7 iCloud/CalDAV, 1 subscribed, 1 birthdays; 13 writable) and 19 events in the next 7 days, 16 of them recurring. Still unknown: whether the dialog named the binary or Terminal, and how the grant behaves when launched by Claude Desktop. LOCAL stays viable but is no longer first.

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

### 4.2b What Claude's and FastMCP's docs require (verified 2026-10-21)
From Anthropic's connector docs ([Authentication](https://claude.com/docs/connectors/building/authentication), [Build an MCP server](https://claude.com/docs/connectors/building/index); undated):
- OAuth 2.0 with DCR or CIMD works by default. Claude uses CIMD only if the authorization server advertises `client_id_metadata_document_supported: true` **and** `"none"` in `token_endpoint_auth_methods_supported`; otherwise it falls back to DCR.
- Unauthenticated requests must get **HTTP 401** with `WWW-Authenticate: Bearer resource_metadata="..."`; a header on a 200 is ignored. The metadata `resource` must equal the server URL exactly. S256 PKCE is required. Callback for the hosted apps (web, Desktop, **mobile**, Cowork): `https://claude.ai/api/mcp/auth_callback`.
- `/token` must accept `application/x-www-form-urlencoded`; refresh failures must return `invalid_grant`; refresh tokens should rotate. Discovery, registration and token endpoints get 10 seconds (refresh 30 seconds).
- Transport: Streamable HTTP. Tool results up to about 150,000 characters on claude.ai/Desktop; 240-second tool timeout. Sampling and draft MCP features are not supported, which is why confirmation is done in our protocol and not through elicitation.
- Anthropic's traffic comes from `160.79.104.0/21` ([Anthropic](https://claude.com/docs/connectors/building/authentication)). Allowlisting it would block ChatGPT unless OpenAI's ranges are also known (**UNVERIFIED**); not used.
- A fixed bearer-token option for Claude exists but is a **beta for a limited set of organizations**, so we use OAuth.

From FastMCP's docs ([OAuth Proxy](https://gofastmcp.com/servers/auth/oauth-proxy), [GitHub](https://gofastmcp.com/integrations/github); undated):
- CIMD is enabled by default and DCR is accepted, so one proxy can serve both Claude and ChatGPT. The consent screen is on by default.
- **There is no documented single-user allow-list.** We enforce "only the owner" ourselves by checking the GitHub `login`/numeric ID claim in a custom token-verifier or middleware, and test that other accounts and unauthenticated requests get rejected.
- Default token/client storage is documented as "only suitable for development"; production needs `jwt_signing_key` from the environment and an encrypted, persistent `client_storage` (on one Fly machine: an encrypted store on a small volume, **UNVERIFIED** until built). Pin `allowed_client_redirect_uris` to the known callbacks once ChatGPT's is observed in logs.

Built and tested in M3 (fake credentials, in-process and local HTTP; **not** against real GitHub, Claude or ChatGPT):
- Unauthenticated `/mcp` returns 401 with a `resource_metadata` pointer; protected-resource metadata `resource` equals `<base>/mcp`; authorization-server metadata advertises S256, a registration endpoint, `client_id_metadata_document_supported: true` and `none` as a token auth method; `/token` accepts form-encoded bodies.
- Registration is limited to Claude's callback and ChatGPT's two documented forms (`https://chatgpt.com/connector_platform_oauth_redirect`, `https://chatgpt.com/connector/oauth/{id}`; [OpenAI](https://developers.openai.com/apps-sdk/build/auth)); other redirects get HTTP 400.
- Owner-only: the upstream verifier is wrapped so only the owner's numeric GitHub user ID passes on every request, and each tool repeats the check. Over real HTTP, a non-owner sees **no tools** (FastMCP answers "Unknown tool"), which resolves the earlier open question: unauthorised tools are hidden, not just rejected.
- GitHub is asked for `read:user` only (FastMCP's default would be the broader `user`). Whether GitHub accepts this scope in the verifier's checks is **UNVERIFIED** until the first real sign-in.
- Uvicorn's access log is disabled because OAuth callback URLs carry one-time codes.

### 4.3 Where the password lives
Hosting platform's secret store (encrypted at rest, injected as an env var at runtime). Never in the repo, image, logs, or `.env` files that are committed. Locally for development: macOS Keychain.

### 4.3b Possible future replacement for the app-specific password
Apple documents an alternative where a "supported" third-party app is authorised with your Apple Account instead of an app-specific password, and access is later removed under account.apple.com, Sign-In and Security, Account Data Sharing ([Apple, Oct 21 2025](https://support.apple.com/en-gb/121539)). That page does not say how an app becomes supported. In Apple's developer forum (Sep to Oct 2026) the only official answer is that a **pilot programme** exists and developers can register interest through a web form; one developer on a free account could not open the form, and another reported no reply to a request made on Oct 8 ([thread](https://developer.apple.com/forums/thread/825772)). Conclusion: **not available to us today**; the app-specific password is the only path. Revisit later; it could shrink the blast radius.

### 4.4 Hosting options
| Option | Cost | Notes |
|---|---|---|
| Fly.io | shared-cpu-1x: **$2.19/month for 256MB, $3.69/month for 512MB** if running continuously; no free tier, only a trial of up to 2 machine-hours or 7 days; stopped machines pay only $0.15/GB/month of root filesystem; volume $0.15/GB/month; outbound $0.02/GB North America and Europe, $0.04/GB Asia Pacific ([Fly pricing](https://docs.fly.io/about/pricing), undated; prices listed for Ashburn and Secaucus, Tokyo **UNVERIFIED**) | Secrets store built in. Python + FastMCP probably needs 512MB (**UNVERIFIED**, measure at build). A small volume is likely needed for OAuth state (§4.2) |
| Cloudflare Workers | Free plan: 100,000 requests/day, 1,000/min burst; CPU limit 10 ms (official table; a blog says 30 ms) | Python on Workers is limited, so TypeScript would be needed; `caldav` libs differ. Not recommended for this stack |
| A home Mac/Raspberry Pi + tunnel | $0 + hardware | Credential stays in your home; availability depends on your network. **UNVERIFIED** that ChatGPT's "Secure MCP Tunnel" fits |
Recommendation if HOSTED is built: Fly.io. Prices change; recheck before committing.

### 4.5 Blast radius if the server is compromised
- The app-specific password grants access to iCloud **Calendar, Contacts and Mail** (CalDAV/CardDAV/IMAP/SMTP) for your Apple Account. An attacker can read mail, send mail as you, and read/modify/delete calendar and contacts. Apple's page ([102654](https://support.apple.com/en-us/102654)) gives mail, contacts and calendars as examples of what an app can access, says nothing about iCloud Drive or Photos, and does not enumerate scope or say whether Apple Account settings are reachable. So the exact scope is **UNVERIFIED**; the documented purpose is signing in to apps without your main password, which the password does not reveal.
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

Recorded 2026-10-21 (later):
5. **Scope:** iCloud calendars only.
6. **Order:** HOSTED first; LOCAL optional and later (see §2).
7. **iCloud Mail:** wanted. v1 = read + drafts only; no send, delete or move. Design in [MAIL.md](MAIL.md). Ships after hosted calendar works, off by default.
8. **Spike:** passed on macOS 27.0.1 (§3).
9. **Friction:** the two-step write protocol means one extra "yes" in chat per change. A one-step mode that leans on the AI app's own approval prompt would be an opt-in, only after confirming those prompts show the exact arguments on every surface you use. No change made.

10. **ChatGPT is read-only (decided 2026-10-21).** On your Pro plan, ChatGPT very likely cannot use write tools on custom MCP connectors (§9). Events are added through Claude (browser, phone, desktop). The iCloud account is a personal Apple Account.
11. **Read-only scope for ChatGPT:** the server issues separate OAuth scopes, `calendar.read` and `calendar.write`. The ChatGPT connection is granted `calendar.read` only, so write tools are not offered to it; Claude's connection gets both. Whether FastMCP can hide tools per scope is **UNVERIFIED** and is tested at build time. Fallback: `confirm_change` rejects any token without `calendar.write`, with a clear error.
12. **Mail goes to Claude only** in v1 (see MAIL.md), not to ChatGPT.

**Remaining open risk:** the ChatGPT finding above is secondary-source only. If OpenAI's page, read in a browser, says otherwise, the scope in item 11 can simply be widened.

## 9. Verification log (2026-10-21)
What happened to the items earlier marked UNVERIFIED. "Still" means I could not settle it from here.

| Item | Result | Source / next step |
|---|---|---|
| ChatGPT **Pro** can add custom MCP connectors **with write tools** | **Very likely NO: read/fetch only on Plus/Pro; write/modify actions are Business/Enterprise/Edu.** Not confirmed from OpenAI's own page, which returns 403 to my fetcher. Three converging secondary sources: (1) a search snippet quoting OpenAI help text, "Pro users can connect MCPs with read/fetch permissions in developer mode"; (2) another snippet, full MCP "only available to Business and Enterprise/Edu users, currently"; (3) a Google AI Overview you sent that cites OpenAI's Help Center and says Plus/Pro get read and fetch only. An older Sep 2025 launch post said Plus/Pro could write; the newer help text supersedes it | [OpenAI help](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt) (unreadable here). Optional: read it in your browser. Design consequence: ChatGPT is read-only; events are added through Claude. Business-plan upgrade not worth it for a personal setup |
| Claude shows approval prompts for tools | **Verified in principle.** Anthropic's connector docs say every tool needs a `title` plus hints; read-only tools can run without per-call confirmation and destructive tools always prompt ([review checklist](https://claude.com/docs/connectors/building/review-criteria), written for directory connectors; same behaviour for your custom connector is **UNVERIFIED**) | Consequence: previews are `readOnlyHint: true`, `confirm_change` is `destructiveHint: true`, so Claude should prompt on the confirm call. The prompt will show only a token, so the chat preview remains the real confirmation. Hints must match behaviour |
| Claude mobile can use connectors | **Verified:** Anthropic says the same connector infrastructure backs claude.ai, Desktop, **mobile**, Cowork and Claude Code, and hosted apps share one callback URL ([docs](https://claude.com/docs/connectors/building/authentication)). **Adding** a connector from the phone is still **UNVERIFIED** | Add it on the web |
| Claude iOS native Calendar works | **Verified by you:** the iPhone test succeeded (you didn't say whether create, edit and delete were all tried). **Edit/delete still UNVERIFIED.** Anthropic says "edit or delete events as needed" with an ownership caveat; MacStories tested Reminders modify/delete and only *read* Calendar ([MacStories](https://www.macstories.net/notes/testing-claudes-native-integration-with-reminders-and-calendar-on-ios-and-ipados/)) | Tell me whether editing and deleting a `TEST` event worked |
| FastMCP can restrict to one user | **Verified: no built-in allow-list.** We implement it (§4.2b) | [FastMCP docs](https://gofastmcp.com/servers/auth/oauth-proxy) |
| Claude's OAuth needs | **Verified** (§4.2b) | [Anthropic](https://claude.com/docs/connectors/building/authentication) |
| Fly.io price | **Verified, undated:** $2.19 (256MB) / $3.69 (512MB) per month; no free tier | [Fly](https://docs.fly.io/about/pricing) |
| App-specific password scope | **Still unenumerated** by Apple (§4.5) | [Apple](https://support.apple.com/en-us/102654) |
| iCloud IMAP search / threading / rate limits | **Still UNVERIFIED.** No Apple documentation. Third-party notes: no server-side threading (headers used instead), no MOVE/IDLE, and published limits concern *sending*, not reading ([Nylas](https://developer.nylas.com/docs/cookbook/email/threads/list-threads-icloud/) and blogs; one source claims search only covers about 90 days, but that describes Nylas's own sync) | Build includes a read-only capability probe you run once against your account (CAPABILITY and a sample search; prints no mail content) |
| `python-caldav` works with iCloud | **Still UNVERIFIED.** Its docs (v3.5.0) don't mention iCloud; `search(..., expand=True)` is documented for recurrence ([docs](https://caldav.readthedocs.io/stable/)) | A local Radicale test server does **not** prove iCloud compatibility; an opt-in integration test against your real iCloud TEST calendar is required before launch |
| Claude Desktop elicitation; Swift SDK annotations; macOS TCC attribution when launched by Claude Desktop | **Moot while LOCAL is deferred.** Revisit if LOCAL is built | n/a |
