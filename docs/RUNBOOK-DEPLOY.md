# Runbook: deploy on Fly.io (paid alternative), read-only

> The free route is [RUNBOOK-CLOUDRUN.md](RUNBOOK-CLOUDRUN.md). Use this page only if Cloud Run does not work out. On Fly the server fits the 256 MB machine (about $2.19/month; it peaked near 91 MB right after start-up in a sandbox measurement, not measured under load), so you can set `memory = "256mb"` in `fly.toml` and recheck with `fly logs` for out-of-memory restarts.

Status: written 2026-10-21 for milestone M3. **Nothing here has been run against real GitHub, Fly.io, iCloud, Claude or ChatGPT**: the code was tested with fakes and a local CalDAV server. The first real deployment is the first real test, so expect to fix small things. Report what you see and I will help.

**Golden rule:** your app-specific password is typed only into your own terminal, into Fly's secret store. Never paste it into a chat (including with Claude), a file in this repo, or a command line argument.

Time: about 40 minutes the first time. Cost: roughly $3.69 a month for the machine plus about $0.15 for the volume (Fly docs, undated; recheck).

## 0. What you need
- An Apple Account with two-factor authentication (personal account is fine).
- A GitHub account (the one that will be "the owner").
- `flyctl` installed and signed in (`fly auth login`), and a Fly.io account with billing set up.
- This repository cloned on your Mac.

## 1. Find your numeric GitHub user ID
It is public information:
```bash
curl -s https://api.github.com/users/YOUR_GITHUB_LOGIN | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])"
```
Write it down. This number, not your login name, is what the server checks.

## 2. Pick the app name and create the app and volume
```bash
cp deploy/fly.toml.example fly.toml        # fly.toml is git-ignored
# edit fly.toml: replace both CHANGE-ME values (app name, and PUBLIC_BASE_URL = https://<app>.fly.dev)
fly apps create <app>
fly volumes create oauth_state --size 1 --region iad --app <app>
```

## 3. Create a GitHub OAuth App
GitHub → Settings → Developer settings → OAuth Apps → New OAuth App (menu wording may differ):
- Homepage URL: `https://<app>.fly.dev`
- Authorization callback URL: `https://<app>.fly.dev/auth/callback`  (this path is FastMCP's default, confirmed in its source)
- Create it, copy the **Client ID**, and generate a **Client secret** (shown once).

The server asks GitHub only for the `read:user` scope (identity). It never reads your repositories.

**Opt out of token expiration (do this before the first sign-in).** On the app's **Optional features** tab, if "Token expiration" is on, click **Opt-out** (afterwards the button reads "Opt-in"). With it on, GitHub's access tokens last 8 hours and the server must refresh them; found on 2026-10-23, that refresh failed overnight with `bad_refresh_token` and the connector showed "Reconnect". With it off, tokens do not expire. Existing tokens are not affected, so reconnect once after changing it.

## 4. Create the app-specific password
account.apple.com → Sign-In and Security → App-Specific Passwords → generate one labelled `icloud-mcp-server`. Keep the window open until step 5 is done. ([Apple Support](https://support.apple.com/en-us/102654))

## 5. Set the secrets (in your terminal only)
Generate a signing key first:
```bash
openssl rand -base64 48
```
Then run `fly secrets import --app <app>` and, when it waits for input, paste these six lines with your real values and finish with Ctrl-D. Do not put them in a file:
```
ICLOUD_APPLE_ID=you@icloud.com
ICLOUD_APP_PASSWORD=<the app-specific password>
GITHUB_CLIENT_ID=<from step 3>
GITHUB_CLIENT_SECRET=<from step 3>
ALLOWED_GITHUB_USER_ID=<number from step 1>
JWT_SIGNING_KEY=<output of openssl rand>
```
(Check `fly secrets import --help` if the command has changed.) Then clear your terminal scrollback.

## 6. Deploy and check
```bash
fly deploy --app <app>
curl -i https://<app>.fly.dev/ping        # expect: 200 ok
curl -i -X POST https://<app>.fly.dev/mcp    # expect: 401 and a WWW-Authenticate header with resource_metadata
fly logs --app <app>                         # no credentials or event text should ever appear
```
If the machine exits at start-up, `fly logs` shows which **setting name** is missing or invalid (values are never printed).

## 7. First real test, read-only (the iCloud probe)
On your Mac, with the password in the Keychain (it prompts; it is not echoed):
```bash
security add-generic-password -U -s icloud-mcp-server -a "you@icloud.com" -w
export ICLOUD_APPLE_ID=you@icloud.com
uv run python scripts/probe_icloud.py            # add --show-names only if you are happy to share names
```
It prints counts only. Send me the output. It tells us whether iCloud behaves the way python-caldav's profile says (the one thing the local test server cannot show).

## 8. Add to Claude
Per Anthropic's [custom connector article](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp): claude.ai → Settings → Connectors → add a custom connector with the URL `https://<app>.fly.dev/mcp`, choose OAuth sign-in, and sign in with GitHub as the owner. A consent screen from the server appears first; read the client name and redirect host before approving.

Try: "What's on my iCloud calendar tomorrow?" and "Find a 1-hour slot this week that works in both Tokyo and Manila working hours." Tell me what happens, including any error text.

## 9. ChatGPT (read-only on Pro)
Per OpenAI's [connect page](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt): chatgpt.com/plugins → plus → Add custom MCP server → the same URL, OAuth. The server already allows both of ChatGPT's documented redirect forms ([OpenAI auth page](https://developers.openai.com/apps-sdk/build/auth)). If registration fails with a redirect error, copy the redirect URI ChatGPT shows and add it to the `EXTRA_REDIRECT_URIS` secret (comma separated, https only), then redeploy. On a Pro plan expect reads only (secondary sources; see docs/DESIGN.md §9).

## 10. If something looks wrong
| Symptom | Likely cause |
|---|---|
| Claude says it couldn't reach the server | Wrong URL; `PUBLIC_BASE_URL` does not equal the URL you entered (path included: `/mcp`) |
| Sign-in loops or fails at GitHub | Callback URL in the OAuth App is not exactly `https://<app>.fly.dev/auth/callback` |
| Sign-in works but every tool is missing | Signed in with a GitHub account other than the owner (the server hides all tools from other accounts) |
| `CREDENTIALS_REJECTED` from a tool | App-specific password revoked, mistyped, or your Apple Account password was changed (that revokes them all) |
| `ICLOUD_UNREACHABLE` | Temporary Apple outage or network issue; retry |

## 11. Revoking everything
See [SECURITY.md](../SECURITY.md#revoking-access). Fastest: remove the app-specific password at account.apple.com, then `fly apps destroy <app>`.
