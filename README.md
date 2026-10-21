# icloud-calendar-mcp

An MCP server that lets Claude and ChatGPT use your iCloud Calendar. **Read-only by default.** Any change to your calendar is shown to you as an exact preview and applied only after a separate confirmation step.

> **Status: design phase. The server is not implemented yet.** What exists: design docs, the tool spec, CI, and a small macOS permission spike. Do not try to install it yet.

## Do you need this?

- **iPhone:** Claude's iOS app already reads your calendar and creates events through the system Calendar (all plans; [Anthropic's help article](https://support.claude.com/en/articles/11869619-use-claude-with-ios-apps), Aug 2026). Try that first.
- **Mac, Claude Desktop / Claude Code:** I found no official Apple Calendar integration for Desktop (check *Settings → Connectors* on your Mac first).
- **ChatGPT:** needs a remote HTTPS MCP server with OAuth. That's what hosted mode is for.

## Two modes

| | LOCAL | HOSTED |
|---|---|---|
| For | Claude Desktop, Claude Code on your Mac | ChatGPT (Pro), claude.ai, Claude iPhone connectors |
| How | stdio server + Swift EventKit helper | HTTPS server + CalDAV + OAuth |
| Credential | **None** (macOS permission) | iCloud **app-specific password** on the server |
| Cost | $0 | about $2/month hosting (check current pricing) |
| Setup time | about 15 minutes if Xcode Command Line Tools are installed | about 30 minutes |

Setup guides: [LOCAL](docs/SETUP-LOCAL.md) · [HOSTED](docs/SETUP-HOSTED.md)

## Safety model

- Writes are off unless the server starts with `--allow-writes`, and only for calendars you list.
- `create_event`, `update_event` and `delete_event` only return a **preview** and a one-time token. `confirm_change` is the only tool that writes, and it re-reads the event afterwards to verify.
- Deletes are one event (or one occurrence) at a time. There is no bulk delete.
- Event text is returned labelled as untrusted data, to reduce prompt-injection risk. This reduces the risk; it does not remove it.

Read [SECURITY.md](SECURITY.md) before running hosted mode: the app-specific password also opens iCloud **Mail and Contacts**.

## Docs

- [docs/DESIGN.md](docs/DESIGN.md): architecture, trade-offs, blast radius
- [docs/TOOLS.md](docs/TOOLS.md): the 8 tools with schemas and examples
- [docs/TESTING.md](docs/TESTING.md): test plan
- [spike/](spike/): 5-minute macOS permission test

## License

MIT. Hosted mode depends on `recurring-ical-events` (LGPL-3.0) as an unmodified library. See [SECURITY.md](SECURITY.md) and `docs/DESIGN.md` for details.
