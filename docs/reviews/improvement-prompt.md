Act as the senior engineer responsible for making this iCloud Calendar MCP dependable for daily use. Work in /Volumes/EngrJed SSD/03 Projects/iCloud MCP. Read AGENTS.md, CLAUDE.md, SECURITY.md, the source, tests, and docs/reviews/2026-11-03-assessment.md. Verify the current branch and preserve existing uncommitted work.

Updated product decision (3 November 2026): the user now has native iCloud Calendar and iCloud Mail plugins available in ChatGPT and will use those for ChatGPT. Treat that as the user's chosen integration, not as a claim that native plugins have been tested here. This custom server is for Claude's iCloud Calendar access. Custom ChatGPT integration and testing are removed from this project's scope; custom Mail remains out of scope.

Implement the improvements; do not stop at a plan. Keep the existing single-owner, hosted CalDAV architecture and preview-then-confirm workflow. Preserve the preview_* tool names and default read-only operation. Do not broaden writable calendars or add invitations, RSVP, Mail or ChatGPT support during this work.

Remove the obsolete custom ChatGPT integration as part of the implementation:
- Remove ChatGPT-specific default OAuth redirect URIs from auth.py and update registration tests to reject those defaults while continuing to accept Claude's callback. Preserve the explicit extra-redirect configuration mechanism if still useful; do not silently migrate live settings.
- Remove the custom ChatGPT setup instructions, ChatGPT-only scope work, testing tasks and roadmap promises from active docs, README, AGENTS.md and CLAUDE.md. State briefly that the user uses native ChatGPT iCloud plugins separately. Retain historical test records with a clear superseded note rather than rewriting history.
- Keep generic MCP/OAuth behavior, CIMD, dynamic registration, URL-shaped client-ID hashing and encrypted state: Claude still needs them. Remove only integration-specific code and tests, not shared protocol coverage.
- Do not uninstall or modify the native ChatGPT plugins, disconnect Claude, revoke the shared GitHub OAuth app, remove iCloud credentials, or delete production infrastructure. If old custom ChatGPT registrations need revocation, report a narrowly scoped option separately; never purge shared OAuth state.
- Do not spend time connecting or testing this custom server in ChatGPT. Verify the supported client through automated protocol tests, and use Claude for any authorized end-to-end client test.

Start with meaningful failing regressions for these confirmed issues:
1. A concurrent edit after the version check can be deleted because whole-event DELETE is unconditional. Enforce strong server-side preconditions and refuse unsafe writes when a usable version is unavailable. Inspect the installed/locked CalDAV library rather than assuming PUT and DELETE behave alike.
2. A detached recurring occurrence can have guests even when its master does not. Check the exact target, affected series components and scheduling metadata at preview and confirmation; block operations that could send invitations or cancellation emails.
3. Recurrence verification ignores UNTIL. Compare every supported field with correct local-date/UTC normalization.
4. Removing RRULE leaves RDATE and potentially other recurrence components. Define and test complete recurrence removal and exception handling by checking expanded occurrences.
5. Accepted notes can be longer than the approval preview. Make the reviewed change faithfully represent every value written, while consistently labeling calendar content as untrusted.

Then strengthen reliability:
- Give each approved operation a stable ID and create UID before external I/O. Provide bounded reconciliation/result lookup for timeouts and lost responses, preventing duplicate writes. Distinguish failed, applied and unknown outcomes; never silently retry a write that may already have applied.
- Make confirmation state immutable and bind it to the intended authenticated principal/client where appropriate. Preserve expiry, one-time execution and rate limits. Choose durable state only if deployment behavior requires it; explain the tradeoff.
- Prefer stable calendar IDs for write allow-lists, with a compatible migration and accurate source-permission reporting.
- Bound caches and request work; avoid whole-calendar scans after transient errors. Measure discovery, OAuth, tools and CalDAV latency before changing architecture. Treat the warm ping as mitigation rather than a guarantee.
- Repair integration-test cleanup so a successful create followed by read-back failure cannot leave an untracked test event, and cleanup failures are reported.
- Replace loose write dictionaries with validated models where useful. Add targeted type checking and CI for supported Python versions, including the production container version.
- Make deploy configuration reproducible, preserve write restrictions, verify OAuth/log redaction and keep an explicit rollback image. Correct stale docs, the local EventKit claims and inaccurate Codex-for-Claude substitutions. Publish a clear supported/deferred feature matrix.

Use small, reviewable changes and regression tests that fail on the original bugs. Run lint, the full offline test suite and appropriate secret scans. All real-account automated writes must be explicitly opted in and restricted to the exact TEST calendar with a recorded cleanup ledger. Use synthetic local CalDAV data by default. Never display credentials or log tokens, private event content or OAuth query values. Do not deploy, push, change account settings or alter real events as part of this prompt without explicit authorization for that action; complete and verify the local work first.

Deliver the implemented changes, fresh test results, a short explanation of each corrected behavior, remaining risks, and precise deployment/rollback instructions. Record limitations honestly: passing tests do not prove full Google Calendar parity or eliminate prompt injection. If an external step is blocked, finish independent work and state the exact remaining step once.
