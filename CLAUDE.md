# icloud-mcp-server

MCP server giving Claude (and later ChatGPT) access to the owner's iCloud Calendar. Python, FastMCP 4, CalDAV
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
- Tests: `uv run pytest`. Lint: `uv run ruff check . --extend-ignore EXE002` (exFAT marks every file 755, so EXE002 is noise).
- Secrets scan before any push: `gitleaks detect --no-git --source . --redact` and `gitleaks detect --source . --redact`.
- Code-only redeploy (keeps env vars, secrets, limits): `gcloud run deploy icloud-mcp-server --source . --region us-central1 --project icloud-mcp-512404`. Claude Code's auto-mode classifier blocks Claude from running it, so the user runs it with `! <command>`.
- Cloud Run logs: use a short window (`--freshness`/timestamp filter); a traceback fills `--limit` fast. Redact `code=`/`state=` query values before pasting.

## Rules
- Never read, print or log secrets (app-specific password, GitHub client secret, JWT key). No `gcloud secrets versions access`, no `security find-generic-password -w`, no env dumps.
- One step at a time. Ask before any git push or outward action. Mark anything unverified as UNVERIFIED.
- Keep `core.fileMode false` on the repo (exFAT). `.venv` and `._*` are in `.git/info/exclude`, not `.gitignore`.
- Writes (M4) only ever target a calendar named exactly `TEST` in automated tests.

## Gotchas found in real use (do not regress)
- Claude's OAuth client ID is a URL (CIMD). Firestore rejects `/` in document names, so storage keys are hashed (`HashedKeys` in `state.py`).
- GitHub OAuth App "Token expiration" must stay opted out (Optional features tab; the button then reads "Opt-in"). Any refused upstream refresh is mapped to `invalid_grant` in `auth.py`.
- Cold starts range from about 7 s to over 22 s; Claude allows OAuth endpoints 10 s. Warm requests take about 0.2 s.
- A cold-start measurement is only valid after the logs show "Shutting down" (about 15 minutes after the last request).
- Reloading the OAuth callback page shows "Invalid or expired authorization transaction"; the first load already succeeded.
- `gcloud billing budgets create` fails in `deploy.sh`; budgets were made by hand (billing account is in JPY).
- Browser steps need the Claude in Chrome extension connected.
