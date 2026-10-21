# Permission spike (about 5 minutes, on your Mac)

Answers one question before we write the real helper: **does macOS show a Calendar permission prompt for a small signed Swift binary, and can that binary then read your calendars?**

The spike is read-only. It prints counts, never titles, notes, attendees or calendar names. Review `tcc_spike.swift` (about 50 lines) before running it.

## Run it

You need Xcode Command Line Tools (`xcode-select --install` if `swiftc --version` fails).

```bash
cd spike
swiftc tcc_spike.swift -o tcc_spike \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker Info.plist
codesign -s - --force tcc_spike
./tcc_spike
```

A macOS dialog should appear, quoting the sentence from `Info.plist`. Click **Allow**.

## What to send back

Paste the full terminal output. It contains no event content. Also tell me:

1. Did the dialog appear? Did it name `tcc_spike` or `Terminal`?
2. After running, open **System Settings → Privacy & Security → Calendars**. What is listed (`tcc_spike`, `Terminal`, both)?
3. Your macOS version line is printed by the script (this is the macOS 27 check).

## Cleaning up

To remove the grant: System Settings → Privacy & Security → Calendars → switch off or remove the entry. To reset only this binary's decision from Terminal: `tccutil reset Calendar io.github.icloud-calendar-mcp.spike`. Do **not** run `tccutil reset Calendar` without the identifier, because that resets every app's calendar permission.

Note: the project was renamed to `icloud-mcp-server` after the first spike run. The spike keeps its original bundle identifier `io.github.icloud-calendar-mcp.spike` on purpose, so it matches the permission entry already on your Mac.

## What this does not test

How the grant behaves when the binary is launched *by Claude Desktop* rather than by Terminal. That is the first test in Phase 4, because it needs the real server.
