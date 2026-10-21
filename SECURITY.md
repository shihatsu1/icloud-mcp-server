# Security

## What is stored where

| Item | LOCAL mode | HOSTED mode |
|---|---|---|
| Apple ID password | **Never used, never stored** | **Never used, never stored** |
| App-specific password | Not used | Hosting platform's secret store (e.g. `fly secrets`); macOS Keychain on your Mac for development only |
| Calendar data | Read live from macOS Calendar; not cached to disk | Read live from iCloud over CalDAV; not cached to disk |
| Pending write tokens | In memory; lost on restart | In memory; lost on restart |
| OAuth tokens (hosted) | n/a | Issued by the identity provider; the server validates them and stores none beyond what the framework needs (to be confirmed at build time) |
| Logs | No credentials, no event titles or notes | Same |

The repository must never contain credentials. `.gitignore` excludes env files, keys and local configs, and CI runs [gitleaks](https://github.com/gitleaks/gitleaks) over the full history on every push. GitHub push protection for users is on by default for personal accounts on github.com. Enable repository secret scanning in the repo settings as well.

## Blast radius (HOSTED mode)

An iCloud app-specific password is **not limited to Calendar**. Per Apple, it lets a third-party app sign in to your Apple Account services such as Mail, Contacts and Calendar. If the hosted server or its secret store is compromised, an attacker could:

- read and send iCloud Mail as you,
- read, change and delete contacts,
- read, change and delete calendar events.

It does not reveal your Apple ID password. Whether it reaches other iCloud data (Drive, Photos) is **UNVERIFIED**; Apple's page does not list scope.

LOCAL mode has no such credential. Its exposure is limited to whatever can run as your macOS user.

## Revoking access

- **App-specific password:** sign in at account.apple.com → Sign-In and Security → App-Specific Passwords → remove it (or revoke all). Apps using it are signed out. Changing or resetting your Apple Account password also revokes all app-specific passwords. ([Apple Support, Oct 2025](https://support.apple.com/en-us/102654))
- **LOCAL calendar permission:** System Settings → Privacy & Security → Calendars → switch off the helper.
- **ChatGPT / Claude connectors:** remove the connector in the app's settings, then revoke the OAuth app at your identity provider.
- **If you suspect a leak:** revoke the app-specific password first, then rotate the OAuth client secret, then check iCloud Mail rules and forwarding addresses.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting for this repository (Security tab → Report a vulnerability). Please do not open a public issue for security problems or paste real credentials or calendar data anywhere in this repo.

## Known limits

- Prompt injection through event text is mitigated (labelling, previews, two-step writes), not eliminated.
- Confirmation is only as good as the person reading the preview.
- Single-user design: hosted mode must reject every account except the owner's.
