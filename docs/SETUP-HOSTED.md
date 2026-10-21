# Setup: HOSTED mode (browser Claude, ChatGPT, iPhone connector)

This is the **primary** setup. The step-by-step procedure is in [RUNBOOK-DEPLOY.md](RUNBOOK-DEPLOY.md); this page is the overview. Status: the read-only server exists (milestones M1 to M3); writes and mail do not yet.

Read [SECURITY.md](../SECURITY.md) first. The credential here also opens iCloud **Mail and Contacts**. Mail tools are a separate opt-in module (see [MAIL.md](MAIL.md)); they add prompt-injection risk, not a new credential.

Time: about 30 minutes.

## 1. Create an app-specific password
1. Sign in at account.apple.com (two-factor authentication must be on).
2. Sign-In and Security → App-Specific Passwords → generate one labelled `icloud-mcp-server`.
3. Copy it once. **Never paste it into a chat (including with Claude), a file in this repo, or a command with the value typed inline.**

Source: [Apple Support](https://support.apple.com/en-us/102654) (Oct 2025). Limit: 25 active app-specific passwords. Apple's newer "authorise with your Apple Account" flow is a pilot and not available to us (DESIGN.md §4.3b).

## 2. Local development storage (Keychain)
```bash
security add-generic-password -U -s icloud-mcp-server -a "you@icloud.com" -w
```
With `-w` last and no value, `security` prompts for the password, so it stays out of shell history.

## 3. Host the server (Fly.io is the current recommendation) (build)
Price at last check (undated page): shared-cpu-1x is $2.19/month for 256 MB or $3.69/month for 512 MB if running continuously; no free tier, only a short trial ([Fly pricing](https://docs.fly.io/about/pricing)). Plan on 512 MB until measured. Recheck before relying on it.
```bash
fly launch --no-deploy
fly secrets import      # paste NAME=value lines on stdin, Ctrl-D; never a committed file
```
Secrets: `ICLOUD_APPLE_ID`, `ICLOUD_APP_PASSWORD`, OAuth client ID/secret, `ALLOWED_GITHUB_USER_ID`, `PUBLIC_BASE_URL`.

## 4. Single-user login (build)
Create a GitHub OAuth App. The server accepts a token only if it belongs to the one numeric GitHub user ID in `ALLOWED_GITHUB_USER_ID`. Unauthenticated requests must get HTTP 401 (there is a test for this).

## 5. Add to Claude (browser, desktop, phone) (build)
Anthropic's [custom connector article](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) (undated) says Claude connects **from Anthropic's cloud**, so your server must be reachable on the public internet. Custom connectors are available on Free (limit 1), Pro, Max, Team and Enterprise.

1. In claude.ai: Settings → Connectors → add a custom connector. URL: `https://<your-app>.fly.dev/mcp`. Choose OAuth sign-in. (Menu wording is provisional; check the article.)
2. Sign in with the single allowed GitHub account when prompted. (The server's OAuth layer must allow the redirect `https://claude.ai/api/mcp/auth_callback` and answer unauthenticated calls with HTTP 401 plus a `resource_metadata` pointer; see DESIGN.md §4.2b.)
3. Try a read first: "What's on my iCloud calendar tomorrow?"
4. The iPhone app uses connectors added on the web (older Anthropic documentation; partly **UNVERIFIED**).

## 6. Add to ChatGPT (build)
Per OpenAI's [Connect from ChatGPT](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt) page: chatgpt.com/plugins → plus button → "Add custom MCP server", the same URL, authentication OAuth. ChatGPT does not accept static API keys ([Apps SDK auth](https://developers.openai.com/apps-sdk/build/auth)).

1. Test read tools first.
2. **Expect read-only on a Pro plan.** OpenAI's help text (as relayed by secondary sources; the page itself was unreadable to me) limits Plus/Pro custom MCP connectors to read and fetch; write actions are Business/Enterprise/Edu. The ChatGPT connection therefore gets the `calendar.read` scope only. To add events, use Claude.

## 7. Writes: how "add this to my calendar" works
You say it in a Claude chat. Claude calls `create_event`, which returns a **preview**; Claude shows it, you say yes, and it calls `confirm_change`. One extra "yes" per change, by design. ChatGPT (read-only scope) can look at your calendar but not make this call.

## 8. Mail (optional, later)
After calendar works: start the server with `--enable-mail` (read + drafts only; Claude only, not ChatGPT; see [MAIL.md](MAIL.md)).

## 9. Revoking
See [SECURITY.md](../SECURITY.md#revoking-access).
