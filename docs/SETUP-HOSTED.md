# Setup: HOSTED mode (ChatGPT and remote connectors)

Status: **draft; implementation is later in Phase 4+.** Read [SECURITY.md](../SECURITY.md) first: the credential here also opens iCloud Mail and Contacts.

Time: about 30 minutes.

## 1. Create an app-specific password
1. Sign in at account.apple.com (two-factor authentication must be on).
2. Sign-In and Security → App-Specific Passwords → generate one labelled `icloud-calendar-mcp`.
3. Copy it once. **Never paste it into a chat, a file in this repo, or a command with the value typed inline.**

Source: [Apple Support](https://support.apple.com/en-us/102654) (Oct 2025). Limit: 25 active app-specific passwords.

## 2. Local development storage (Keychain)
```bash
security add-generic-password -U -s icloud-calendar-mcp -a "you@icloud.com" -w
```
With `-w` last and no value, `security` prompts for the password, so it stays out of shell history.

## 3. Host the server (Fly.io is the current recommendation)
Price at last check: about $1.94/month for shared-cpu-1x 256 MB ([Fly pricing](https://fly.io/docs/about/pricing/)); recheck before relying on it.
```bash
fly launch --no-deploy
fly secrets import      # then paste NAME=value lines on stdin, Ctrl-D; never a committed file
```
Secrets to set: `ICLOUD_APPLE_ID`, `ICLOUD_APP_PASSWORD`, OAuth client ID/secret, `ALLOWED_GITHUB_USER_ID`, `PUBLIC_BASE_URL`.

## 4. Single-user login
Create a GitHub OAuth App. The server accepts a token only if it belongs to the one numeric GitHub user ID in `ALLOWED_GITHUB_USER_ID`. Unauthenticated requests must get HTTP 401 (there is a test for this).

## 5. Add to ChatGPT
Per OpenAI's [Connect from ChatGPT](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt) page: chatgpt.com/plugins → plus button → "Add custom MCP server", URL `https://<your-app>.fly.dev/mcp`, authentication OAuth. Developer-mode wording may differ; it is changing.

1. Test read tools first (`list_calendars`, `search_events`).
2. **Then test whether a write tool is allowed on your Pro plan. This is UNVERIFIED.** Start the server with `--allow-writes --writable-calendars TEST` only after reads work.

## 6. Add to claude.ai / Claude iPhone
Settings → Connectors → add custom connector with the same URL (add it on the web; mobile uses connectors added there, per older Anthropic docs, partly UNVERIFIED).

## 7. Revoking
See [SECURITY.md](../SECURITY.md#revoking-access).
