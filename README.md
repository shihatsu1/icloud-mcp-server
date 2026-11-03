# icloud-mcp-server

A hosted MCP server that lets **Claude** read, and with your confirmation change, the owner's **iCloud** Calendar from anywhere: browser, phone, desktop. Say "add dentist Friday 3pm" in a Claude chat and it lands in your calendar after you approve an exact preview. **Read-only by default.**

> **Status (2026-11-03): live on Google Cloud Run for one owner, connected to claude.ai.** Reads and previewed writes work on the calendars the owner listed. Hardened on the `hardening-2026-11` branch (not yet deployed): see [docs/reviews/](docs/reviews/) and [docs/FEATURES.md](docs/FEATURES.md) for exactly what is and is not supported.

**ChatGPT:** the owner uses ChatGPT's native iCloud Calendar and Mail plugins separately. This server no longer ships any ChatGPT-specific setup, callback URLs or roadmap; it serves Claude (generic MCP/OAuth behavior is unchanged).

## What it is

- **Single owner.** Sign-in goes through a GitHub OAuth App and only the owner's numeric GitHub ID is accepted ([SECURITY.md](SECURITY.md)).
- **CalDAV to iCloud** with an app-specific password kept in Secret Manager. That password cannot be limited to calendar only; read the blast-radius section of [docs/DESIGN.md](docs/DESIGN.md).
- **Preview, then confirm.** `preview_create_event`, `preview_update_event` and `preview_delete_event` write nothing and return an exact preview with a one-time token and an `operation_id`. `confirm_change` is the only tool that writes; it answers `applied`, `not_applied` or `unknown` and never silently retries.
- **Untrusted text is labelled** (`untrusted` fields, a notice on every result). This reduces prompt-injection risk; it does not remove it.

## Not built

A local EventKit mode is **not built**: `helper/` is a placeholder and `spike/` only tests macOS Calendar permission. There is no iCloud Mail module (design notes only: [docs/MAIL.md](docs/MAIL.md)), no invitations or RSVP, and this is not full Google Calendar parity.

## Develop

```
uv sync
uv run pytest                                   # offline: fakes plus a local Radicale CalDAV server
uv run ruff check . --extend-ignore EXE002      # EXE002 is exFAT noise on the owner's SSD; CI runs plain `ruff check .`
uv run mypy
```

Real-iCloud tests are opt-in and touch only a calendar named exactly `TEST` ([docs/TESTING.md](docs/TESTING.md)).

## Docs
- [docs/FEATURES.md](docs/FEATURES.md): supported, deferred and out-of-scope features
- [docs/TOOLS.md](docs/TOOLS.md): tools, schemas, outcome states
- [docs/DESIGN.md](docs/DESIGN.md): architecture, trade-offs, blast radius
- [docs/RUNBOOK-CLOUDRUN.md](docs/RUNBOOK-CLOUDRUN.md): first deploy, routine release, rollback
- [docs/TESTING.md](docs/TESTING.md): what is tested and what is not
- [docs/reviews/](docs/reviews/): the 2026-11-03 assessment and the improvement prompt

## License
MIT. Depends on `recurring-ical-events` (LGPL-3.0) as an unmodified library; see `docs/DESIGN.md`.
