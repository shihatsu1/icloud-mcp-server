# icloud-mcp-server

An MCP server that lets Claude and ChatGPT use your **iCloud** Calendar (and, later, iCloud Mail), from anywhere: browser, phone, desktop. Say "add dentist Friday 3pm" in a Claude chat and it lands in your iCloud calendar. ChatGPT can read the calendar; on a Plus/Pro plan it very likely cannot add events (OpenAI limits those plans' custom connectors to read and fetch; see [docs/DESIGN.md](docs/DESIGN.md) §9). **Read-only by default.** Any change is shown to you as an exact preview and applied only after you confirm.

> **Status (2026-10-21): the read-only hosted server is built and tested with fakes and a local CalDAV server; it has not yet been run against real iCloud, GitHub, Claude or ChatGPT.** Calendar writes and iCloud Mail are not built yet. To try it, follow [docs/RUNBOOK-DEPLOY.md](docs/RUNBOOK-DEPLOY.md).

## Do you need this?

- **iPhone:** Claude's iOS app already reads your calendar and creates events through the system Calendar (all plans; [Anthropic's help article](https://support.claude.com/en/articles/11869619-use-claude-with-ios-apps), Aug 2026). Try that first.
- **Claude in the browser and ChatGPT:** these call your server from their own clouds, so it must be a public HTTPS server with OAuth. That is what this project builds ([Anthropic](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp), [OpenAI](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt)).

## Modes

| | HOSTED (built first) | LOCAL (optional, later) |
|---|---|---|
| For | browser Claude, ChatGPT, Claude iPhone via connector | Claude Desktop, Claude Code on your Mac |
| How | HTTPS server + CalDAV + OAuth | stdio server + Swift EventKit helper |
| Credential | iCloud **app-specific password** on the server | **None** (macOS permission) |
| Calendars | iCloud only | everything on the Mac |
| Cost | about $2.19 to $3.69/month on Fly.io (undated page; no free tier) | $0 |
| Setup time | about 30 minutes | about 15 minutes with Xcode Command Line Tools |

Guides: [Deploy runbook](docs/RUNBOOK-DEPLOY.md) · [HOSTED overview](docs/SETUP-HOSTED.md) · [LOCAL](docs/SETUP-LOCAL.md)

## iCloud Mail (planned, off by default)
v1 is **read + drafts only**: search and read mail, and create drafts in your Drafts folder. It never sends, deletes or moves mail. Mail is the main prompt-injection risk, so it is a separate opt-in module that ships after calendar works. Design: [docs/MAIL.md](docs/MAIL.md).

## Safety model
- Writes are off unless the server starts with `--allow-writes`, and only for calendars you list.
- `create_event`, `update_event`, `delete_event` and `create_draft` only return a **preview** and a one-time token. `confirm_change` is the only tool that writes, and it re-reads afterwards to verify.
- Deletes are one event (or one occurrence) at a time. There is no bulk delete.
- Event and mail text is returned labelled as untrusted data, to reduce prompt-injection risk. This reduces the risk; it does not remove it.

Read [SECURITY.md](SECURITY.md) before running hosted mode: the app-specific password also opens iCloud **Mail and Contacts**.

## Docs
- [docs/DESIGN.md](docs/DESIGN.md): architecture, trade-offs, blast radius
- [docs/TOOLS.md](docs/TOOLS.md): calendar tools with schemas and examples
- [docs/MAIL.md](docs/MAIL.md): mail module design
- [docs/TESTING.md](docs/TESTING.md): test plan
- [spike/](spike/): 5-minute macOS permission test (LOCAL mode)

## License
MIT. Hosted mode depends on `recurring-ical-events` (LGPL-3.0) as an unmodified library; see `docs/DESIGN.md`.
