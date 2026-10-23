"""Create the empty iCloud calendar named TEST that every write test is limited to.

This WRITES to your real iCloud account (one new, empty calendar). It refuses if a calendar with that
name already exists. Run it yourself, or let Claude run it after you say go.

    export ICLOUD_APPLE_ID=you@icloud.com      # the password comes from the Keychain
    uv run python scripts/create_test_calendar.py
"""

from __future__ import annotations

import sys

from icloud_mcp_server.backends.caldav_backend import CalDavBackend
from icloud_mcp_server.core.errors import CalendarError
from icloud_mcp_server.secrets import MissingCredentials, load_credentials

NAME = "TEST"


def main() -> int:
    try:
        creds = load_credentials()
    except MissingCredentials as exc:
        print(f"Setup problem: {exc}")
        return 2
    backend = CalDavBackend(creds.apple_id, creds.password)
    try:
        if any(c.calendar_name == NAME for c in backend.list_calendars()):
            print(f"A calendar named {NAME!r} already exists. Nothing was created.")
            return 0
        backend._client.principal().make_calendar(name=NAME, supported_calendar_component_set=["VEVENT"])
        total = len(backend._load_calendars(force=True))
    except CalendarError as exc:
        print(f"FAILED: {exc.render()}")
        return 1
    except Exception:  # noqa: BLE001 - library text can contain URLs, so print only a generic line
        print("FAILED: iCloud did not accept the new calendar. Create a calendar named TEST in the Calendar app instead.")
        return 1
    print(f"Created an empty calendar named {NAME!r}. Calendars now: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
