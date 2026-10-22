# Runbook: deploy on Google Cloud Run (free tier), read-only

Status: written 2026-10-21. **Nothing here has been run against real Google Cloud, GitHub, iCloud, Claude or ChatGPT.** The code and the deploy script were tested with fakes (a fake `gcloud` that records what it is given, a fake Firestore store, a local CalDAV server). The first real deployment is the first real test, so expect to fix small things and tell me what you see.

**Golden rule:** your app-specific password is typed only at the hidden prompt of `deploy.sh` (it goes straight to Secret Manager). Never paste it into a chat (including with Claude), a file in this repo, or a command line.

## Is it really free? What is verified
- Cloud Run's request-based free tier is 2 million requests, 180,000 vCPU-seconds and 360,000 GiB-seconds a month in the cheaper "Tier 1" regions (Google's [pricing page](https://cloud.google.com/run/pricing), undated; the allowance is smaller in Tier 2 regions such as Tokyo). The script uses `us-central1`. A personal calendar server uses a tiny fraction of that.
- **UNVERIFIED (Google's pages were cut off when I read them; check them yourself in the console):** the Firestore free quota (I recall 1 GiB, 50,000 reads and 20,000 writes a day), Secret Manager's free tier (I recall 6 active versions and 10,000 accesses a month), and the free allowances for Cloud Build and Artifact Registry, which `deploy.sh` uses to build the container.
- **A billing account (card) is probably required** to use Cloud Run and Firestore even within the free tier. I could not confirm that. The script creates a **$1 budget alert** and limits the service to one instance, so a surprise cannot grow large silently.
- Cloud Run scales to zero when idle. The first request after idle is a **cold start**. Claude gives OAuth endpoints 10 seconds ([Anthropic](https://claude.com/docs/connectors/building/authentication)). Whether we meet that is **UNVERIFIED** until step 6.

## 1. Google Cloud project and billing
1. Go to console.cloud.google.com, create a project (note the **project ID**, not the name), and link a billing account. Read the free-tier terms there.
2. Optional but wise: Billing → Budgets & alerts → confirm you get emails. The script also tries to create a $1 budget.

## 2. Install and sign in to `gcloud`
Install the Google Cloud CLI, then `gcloud auth login`. Run all later commands from the root of this repository on your Mac.

## 3. Your numeric GitHub user ID
```bash
curl -s https://api.github.com/users/YOUR_GITHUB_LOGIN | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])"
```
This number, not your login, is what the server checks.

## 4. App-specific password
account.apple.com → Sign-In and Security → App-Specific Passwords → generate one labelled `icloud-mcp-server` and keep the window open until step 5 is done. ([Apple](https://support.apple.com/en-us/102654))

## 5. Run the deploy script
```bash
deploy/cloudrun/deploy.sh YOUR_PROJECT_ID
```
It will:
1. Print your service URL. Cloud Run's URL has the predictable form `https://SERVICE-PROJECT_NUMBER.us-central1.run.app` ([Google](https://docs.cloud.google.com/run/docs/triggering/https-request)), so you can create the GitHub OAuth App **before** the first deploy. It prints the exact **Homepage URL** and **callback URL** (`.../auth/callback`) to use, then waits.
2. Ask for the GitHub Client ID, your numeric ID, your Apple Account email, then (hidden) the GitHub Client secret and the app-specific password.
3. Enable the needed APIs, create the Firestore database (encrypted OAuth state), a signing key (created once), a service account that can only read three secrets and use Firestore, and deploy with `--allow-unauthenticated` (Claude cannot send Google credentials; the app does its own OAuth and only your GitHub account passes), `--max-instances 1`, `--min-instances 0`.
4. Try to create the $1 budget alert.

If a command fails, read its message, fix it, and re-run; the script is safe to repeat (it never silently rotates your signing key).

## 6. Check it, including the cold start
```bash
URL=https://SERVICE-PROJECT_NUMBER.us-central1.run.app     # printed by the script
curl -s -o /dev/null -w '%{http_code}\n' $URL/ping                  # expect 200
curl -si -X POST $URL/mcp | head -5                                    # expect 401 and a WWW-Authenticate header
```
Then measure a **cold** start: wait about 20 minutes with no traffic, then run
```bash
curl -s -o /dev/null -w 'cold: %{time_total}s  http %{http_code}\n' $URL/.well-known/oauth-protected-resource/mcp
curl -s -o /dev/null -w 'warm: %{time_total}s  http %{http_code}\n' $URL/.well-known/oauth-protected-resource/mcp
```
**Send me both numbers.** Under about 6 seconds cold is comfortable; over 10 seconds will make Claude's connection fail intermittently. If it is too slow, the fix is one always-warm instance (`--min-instances 1`), which can leave the free tier: I will tell you the cost and ask before you do it.

## 7. Real iCloud check (read-only)
On your Mac, with the password in the Keychain (it prompts; not echoed):
```bash
security add-generic-password -U -s icloud-mcp-server -a "you@icloud.com" -w
export ICLOUD_APPLE_ID=you@icloud.com
uv run python scripts/probe_icloud.py          # counts only; add --show-names only if you are happy to share names
```
Send me the output. This is the one check the local test server cannot provide.

## 8. Add to Claude
Per Anthropic's [custom connector article](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp): claude.ai → Settings → Connectors → add a custom connector, URL `$URL/mcp`, OAuth sign-in with GitHub as the owner. A consent screen from the server appears first; read the client name and redirect host before approving. Try "What's on my iCloud calendar tomorrow?" and "Find a 1-hour slot this week that works in both Tokyo and Manila working hours."

## 9. ChatGPT (read-only on Pro)
chatgpt.com/plugins → plus → Add custom MCP server → the same URL, OAuth ([OpenAI](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt)). The server already allows both documented ChatGPT redirect forms. If registration reports a redirect error, copy the redirect URI shown, add it to the service's `EXTRA_REDIRECT_URIS` environment variable (`gcloud run services update ... --update-env-vars`), and retry. On Pro expect reads only (see DESIGN.md §9).

## 10. Watching the cost
- Console → Billing → Reports (group by service); the $1 budget emails you at 50% and 100%.
- Free-tier use can be checked per service in the Cloud Run metrics page. Expect near zero.
- If anything looks wrong: `gcloud run services delete icloud-mcp-server --region us-central1` stops everything billable except Firestore data and secrets, which cost almost nothing.

## 11. If something looks wrong
| Symptom | Likely cause |
|---|---|
| Claude can't reach the server, only after idle | Cold start longer than 10 s (step 6) |
| Sign-in fails at GitHub | Callback URL in the OAuth App is not exactly `$URL/auth/callback` |
| Signed in, but no tools appear | Wrong GitHub account (the server hides all tools from non-owners) |
| `CREDENTIALS_REJECTED` | App-specific password revoked or mistyped, or your Apple Account password changed (that revokes all of them) |
| Deploy fails on permissions or billing | Billing not linked, or an API still enabling; re-run |
| Asked to sign in again after idle | Firestore state not working; check Cloud Run logs for Firestore errors |

## 12. Tearing it down and revoking
Delete the service, the three secrets, the Firestore database and the service account in the console (or with `gcloud`), remove the GitHub OAuth App, and **revoke the app-specific password** at account.apple.com. See [SECURITY.md](../SECURITY.md#revoking-access).
