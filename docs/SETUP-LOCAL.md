# Setup: LOCAL mode (macOS), optional and later

LOCAL serves only apps installed on your Mac (Claude Desktop, Claude Code). It cannot serve browser Claude, ChatGPT or the phone; for those use [HOSTED](SETUP-HOSTED.md), which is built first. It also sees every calendar on the Mac (including Exchange), unlike hosted CalDAV.

Status: **draft; steps marked (build) work only once LOCAL is built.** No password is used anywhere in this mode.

Time: about 15 minutes if Xcode Command Line Tools are already installed. If not, `xcode-select --install` can take much longer.

## 0. Before you start
Run the [permission spike](../spike/README.md) first. If it fails on your Mac, stop; the design depends on it.

## 1. Prerequisites
```bash
xcode-select --install        # skip if `swiftc --version` works
curl -LsSf https://astral.sh/uv/install.sh | sh   # or: brew install uv
```

## 2. Get the code (build)
```bash
git clone https://github.com/shihatsu1/icloud-mcp-server.git
cd icloud-mcp-server
uv sync
make helper        # builds bin/calendar-helper and ad-hoc signs it with an embedded Info.plist
```

## 3. Grant calendar access once (build)
```bash
bin/calendar-helper authorize
```
Click **Allow** in the macOS dialog. Check it under System Settings → Privacy & Security → Calendars. If no dialog appears, add the helper manually in that pane.

## 4. Add to Claude Desktop (build)
Config file: `~/Library/Application Support/Claude/claude_desktop_config.json` (or Claude menu → Settings → Developer → Edit Config). Use absolute paths:

```json
{
  "mcpServers": {
    "icloud-calendar": {
      "command": "/ABSOLUTE/PATH/TO/uv",
      "args": ["run", "--project", "/ABSOLUTE/PATH/TO/icloud-mcp-server",
               "icloud-mcp-server", "--mode", "local"]
    }
  }
}
```
Fully quit and restart Claude Desktop. ([MCP docs: connect local servers](https://modelcontextprotocol.io/docs/develop/connect-local-servers).) Server name and CLI arguments are provisional until Phase 4.

## 5. Add to Claude Code (build)
```bash
claude mcp add icloud-calendar -- /ABSOLUTE/PATH/TO/uv run --project /ABSOLUTE/PATH/TO/icloud-mcp-server icloud-mcp-server --mode local
```
**UNVERIFIED:** confirm the `claude mcp add` syntax with `claude mcp add --help` at build time.

## 6. Turning writes on (optional)
Reads only by default. To allow previewed-and-confirmed changes to a test calendar only:
```
... icloud-mcp-server --mode local --allow-writes --writable-calendars TEST
```
Create a calendar named `TEST` in Calendar.app first. Add real calendars to the list only when you are comfortable.

## Troubleshooting
- `PERMISSION_DENIED`: re-check the Calendars pane; run `bin/calendar-helper authorize` from Terminal again.
- Server not listed in Claude Desktop: check JSON syntax and absolute paths; logs are in `~/Library/Logs/Claude/` (`mcp*.log`).
