"""Read-only check of how your real iCloud account behaves. Run it yourself on your Mac.

Prints counts only. Calendar names are hidden unless you pass --show-names. It never prints event
titles, notes, attendees or credentials, and it writes nothing.

    export ICLOUD_APPLE_ID=you@icloud.com      # the password comes from the Keychain:
    security add-generic-password -U -s icloud-mcp-server -a "$ICLOUD_APPLE_ID" -w   # prompts
    uv run python scripts/probe_icloud.py [--show-names] [--days 14]
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta

from icloud_mcp_server.backends.caldav_backend import CalDavBackend
from icloud_mcp_server.core.errors import CalendarError
from icloud_mcp_server.secrets import MissingCredentials, load_credentials


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-names", action="store_true")
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()

    try:
        creds = load_credentials()
    except MissingCredentials as exc:
        print(f"Setup problem: {exc}")
        return 2

    backend = CalDavBackend(creds.apple_id, creds.password)
    try:
        t0 = time.monotonic()
        calendars = backend.list_calendars()
        print(f"connected; {len(calendars)} event calendars; listing took {time.monotonic() - t0:.1f}s")
        if args.show_names:
            for cal in calendars:
                print(f"  - {cal.calendar_name}")

        start = datetime.now(UTC)
        end = start + timedelta(days=args.days)
        t0 = time.monotonic()
        events = backend.search_events(start, end)
        print(f"events in next {args.days} days (recurring expanded): {len(events)}; took {time.monotonic() - t0:.1f}s")
        print(f"  all-day: {sum(e.all_day for e in events)}")
        print(f"  recurring occurrences: {sum(e.is_recurring for e in events)}")
        print(f"  floating (no time zone): {sum(e.tzid == 'floating' for e in events)}")
        print(f"  marked free/transparent: {sum(e.availability == 'free' for e in events)}")
        zones = Counter(e.tzid for e in events if not e.all_day)
        print("  time zones seen: " + ", ".join(f"{z}={n}" for z, n in sorted(zones.items(), key=str)))
        print(f"  occurrences with an override or exception: {sum(e.exceptions_count > 0 for e in events)}")
    except CalendarError as exc:
        print(f"FAILED: {exc.render()}")
        return 1
    print("RESULT: OK (read-only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
