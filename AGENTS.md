# icloud-mcp-server (instructions for any coding agent)

This file mirrors `CLAUDE.md`. Statements about "Claude" below are about the Claude product (claude.ai, the connector, Claude in Chrome), not about you. If you are not Claude Code, your tool names, permission classifier and browser tooling differ: verify rather than assume (e.g. the Claude in Chrome extension is only available to Claude Code, and whether your agent can run `gcloud` depends on its own permissions).

MCP server giving Claude access to the owner's iCloud Calendar (the owner uses ChatGPT's native iCloud plugins separately; no custom ChatGPT work). Python, FastMCP 4, CalDAV
backend, OAuth through a GitHub OAuth App (owner-only), deployed on Google Cloud Run (project
`icloud-mcp-512404`, region `us-central1`, service `icloud-mcp-server`). Repo:
github.com/shihatsu1/icloud-mcp-server. Design: `docs/DESIGN.md`; tool spec: `docs/TOOLS.md`.

## Layout
- `src/icloud_mcp_server/core/`: models, service (tool logic), time zones, free/busy, errors.
- `src/icloud_mcp_server/backends/`: `caldav_backend.py` (iCloud), `fake.py` (tests), `ical_convert.py`.
- `src/icloud_mcp_server/{auth,state,server,__main__,secrets,config}.py`: OAuth, Firestore state, MCP tools, CLI.
- `tests/{unit,contract,auth,deploy}`: contract tests run against a local Radicale CalDAV server.
- `scripts/probe_icloud.py`: read-only real-iCloud check (counts only). `deploy/cloudrun/deploy.sh`: first deploy.

## Commands
- Setup: `uv sync` (the `.venv` here is a symlink to `~/.venvs/icloud-mcp-server`; exFAT's 1 MB blocks would make a venv on this drive ~11 GB).
- Tests: `uv run pytest`. Lint: `uv run ruff check . --extend-ignore EXE002` (exFAT marks every file 755, so EXE002 is noise). Types: `uv run mypy`. CI runs Python 3.11/3.12/3.13 (the container is 3.13).
- Secrets scan before any push: `gitleaks detect --no-git --source . --redact` and `gitleaks detect --source . --redact`.
- Release: the user runs `! deploy/cloudrun/release.sh` from a clean committed tree (no-traffic candidate, protected-settings check, smoke test, then traffic; prints the rollback). `deploy.sh` is bootstrap-only and refuses if the service exists. Some agents' permission classifiers (Claude Code's does) block deploys, so the user runs it with `! <command>`.
- Cloud Run logs: use a short window (`--freshness`/timestamp filter); a traceback fills `--limit` fast. Redact `code=`/`state=` query values before pasting.

## Rules
- Never read, print or log secrets (app-specific password, GitHub client secret, JWT key). No `gcloud secrets versions access`, no `security find-generic-password -w`, no env dumps.
- One step at a time. Ask before any git push or outward action. Mark anything unverified as UNVERIFIED.
- Keep `core.fileMode false` on the repo (exFAT). `.venv` and `._*` are in `.git/info/exclude`, not `.gitignore`.
- Writes (M4) only ever target a calendar named exactly `TEST` in automated tests, and real-account tests need `ICLOUD_TEST_WRITES=yes` (ask first). Planned event IDs go to `tests/integration/.ledger.jsonl` before each confirm; cleanup failures fail the run.

## Gotchas found in real use (do not regress)
- Claude's OAuth client ID is a URL (CIMD). Firestore rejects `/` in document names, so storage keys are hashed (`HashedKeys` in `state.py`).
- GitHub OAuth App "Token expiration" must stay opted out (Optional features tab; the button then reads "Opt-in"). Any refused upstream refresh is mapped to `invalid_grant` in `auth.py`.
- Cold starts range from about 7 s to over 22 s; Claude allows OAuth endpoints 10 s. Warm requests take about 0.2 s. The warm ping is a mitigation, not a guarantee; per-call `timing op=` and per-request `http ... in N ms` log lines show where time goes.
- A cold-start measurement is only valid after the logs show "Shutting down" (about 15 minutes after the last request).
- Reloading the OAuth callback page shows "Invalid or expired authorization transaction"; the first load already succeeded.
- `gcloud billing budgets create` fails in `deploy.sh`; budgets were made by hand (billing account is in JPY).
- Browser steps (driving claude.ai) need browser tooling your agent actually has; with Claude Code that is the Claude in Chrome extension. UNVERIFIED for other agents.
- iCloud answers python-caldav's UID query (`event_by_uid`, `search(uid=...)`) with `412 Precondition Failed` for EVERY event, so events are fetched by address (`CalDavBackend._event_by_uid`: remembered address, then `<UID>.ics`, then a scan). Radicale does not show this, so the opt-in integration run against real iCloud is what catches iCloud-only bugs.
- python-caldav 3.5: mutating `obj.icalendar_instance` then `obj.save()` persists; `edit_icalendar_instance()` needs an explicit `save()` too; `event_by_url(url).load()` works on iCloud.
- Name preview tools `preview_*`. claude.ai's automatic permission check blocked a tool named `create_event` because it sounded like a write (it only previews).
- Events with guests or invitations are refused for update/delete (`HAS_GUESTS`): iCloud may email guests, and attendees are not supported.

## Writes (milestone M4, live since 2026-10-23)
- Tools: `preview_create_event`, `preview_update_event`, `preview_delete_event` (never write) and `confirm_change` (the only write: single-use 5-minute token, version re-check, re-read and compare). Details in `docs/TOOLS.md`.
- Off by default. `ALLOW_WRITES=true` plus `WRITABLE_CALENDAR_IDS=<cal-...>` (preferred; wins) or `WRITABLE_CALENDARS=<names>` (names shared by two calendars are refused); startup refuses writes with no calendar named or with `--dev-no-auth`. Live setting: TEST, Personal, Work, School, Church. MLBB and Calendar are deliberately read-only.
- Real-iCloud tests only ever touch the calendar named exactly `TEST`: `scripts/create_test_calendar.py` (a write to the real account; ask first), then `ICLOUD_APPLE_ID=<id> ICLOUD_TEST_WRITES=yes uv run pytest -m integration tests/integration -q`.
- Deferred on purpose: attendees/invites, `respond_to_event`, Mail. Full matrix: `docs/FEATURES.md`.

## Write-path rules (do not regress; tests exist for each)
- python-caldav deletes unconditionally and sends `If-Match` only with a STRONG ETag: the backend requires a strong ETag equal to the previewed version (`VERSION_UNAVAILABLE` otherwise) and uses `save(precondition="etag")` / `delete(if_match=tag, precondition="etag")`.
- Guests/organizer data on ANY component of the stored resource block update/delete (`Event.scheduling`), at preview and again in `_open`.
- Removing a repeat pops RRULE, RDATE, EXRULE and EXDATE; rule changes are refused when RDATE/EXRULE exist. UNTIL is compared as a local date.
- Input limits equal display limits (`core/untrusted.py`); control characters are stripped on input.
- `confirm_change` returns applied / not_applied / unknown with an `operation_id`; a used token replays the journal and never writes again; an uncertain write is settled by reading. Creates fix their UID at preview.

## Operating the live system
- Deploy: `deploy/cloudrun/release.sh` (above), run by the user. To change env vars whose value has commas, use the alternate delimiter: `--update-env-vars "^|^WRITABLE_CALENDARS=TEST,Personal,Work,School,Church"`.
- After a deploy: claude.ai may keep an old tool list after a deploy. A "Refresh tools list" menu item was seen on 2026-10-23 but is UNVERIFIED now: on 2026-11-03 the ⋮ menu offered only Disconnect, and Anthropic's help article documents no refresh (only remove and re-add). If the tools or descriptions look stale, disconnect and re-add the connector (GitHub sign-in again). A release that keeps tool names unchanged needed no refresh. Read-only verification afterwards: `gcloud run services describe …`, then a short-window `gcloud logging read` (redact `code=`/`state=`).
- Warm ping: Cloud Scheduler job `icloud-mcp-warm-ping` (GET `/ping` every 5 minutes, us-central1) keeps the instance warm so Claude's OAuth calls avoid 7-22 s cold starts (limit 10 s). Created by the user (the classifier blocks Claude from creating scheduled jobs). Its free-tier fit is UNVERIFIED; check the bill monthly, delete with `gcloud scheduler jobs delete icloud-mcp-warm-ping --location us-central1 --project icloud-mcp-512404`.
- Each deploy leaves an image, a source zip and a revision. Clean up, keeping the serving revision AND the previous one (that is the rollback; see `last-release.txt`): `gcloud artifacts docker images delete <repo>/icloud-mcp-server@<digest> --delete-tags --quiet`, `gcloud storage rm gs://run-sources-icloud-mcp-512404-us-central1/services/icloud-mcp-server/<old>.zip`, `gcloud run revisions delete <old> --region us-central1 --quiet`. Compare against `gcloud run services describe … --format='value(spec.template.spec.containers[0].image)'` first.
- Claude Code's Bash tool blocks foreground `sleep` (other agents may not); wait with a bounded `until` loop run in the background.
- Driving claude.ai in Chrome: typing into the chat box can drop everything after the first word (select all, delete and retype); single waits are capped at 10 s; the reply box is near the bottom centre; a first call after a redeploy can take 45 s. Do not type approvals on the user's behalf without their say-so: a safety-check block in claude.ai is the user's to clear.
