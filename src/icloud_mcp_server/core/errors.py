"""Stable error codes (docs/TOOLS.md section 9). Messages never contain credentials."""

from __future__ import annotations

from fastmcp.exceptions import ToolError

NEXT_STEPS = {
    "PERMISSION_DENIED": "Grant calendar access to the helper in System Settings, Privacy & Security, Calendars.",
    "ICLOUD_UNREACHABLE": "Retry in a minute; check Apple's system status page.",
    "CREDENTIALS_REJECTED": (
        "The app-specific password was revoked or expired. Create a new one at account.apple.com "
        "(Sign-In and Security, App-Specific Passwords) and update the server's secret."
    ),
    "CALENDAR_NOT_WRITABLE": "Add the calendar to the server's writable calendar list.",
    "WRITES_DISABLED": "Restart the server with writes enabled.",
    "AMBIGUOUS_SCOPE": "Ask the user whether to change this occurrence or the whole series.",
    "TOKEN_INVALID": "Run the preview again to get a new token.",
    "TOKEN_EXPIRED": "Run the preview again to get a new token.",
    "CONFLICT": "The event changed since the preview. Run the preview again.",
    "WRITE_VERIFICATION_FAILED": "The calendar does not match the preview. Check the actual state shown.",
    "INVALID_TIMEZONE": "Use an IANA time zone such as Asia/Tokyo or Asia/Manila.",
    "INVALID_RANGE": "Use a start before the end, spanning at most 366 days.",
    "NOT_FOUND": "Check the event_id (and occurrence_start for a recurring event).",
    "INVALID_INPUT": "Fix the input and try again.",
    "HAS_GUESTS": "Change or delete this event in the Calendar app, where you can choose whether to notify the guests.",
    "RATE_LIMITED": "Too many changes were confirmed recently. Wait a few minutes, then run the preview again.",
}


class CalendarError(Exception):
    """Domain error carrying a stable code."""

    def __init__(self, code: str, message: str, next_step: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_step = next_step or NEXT_STEPS.get(code, "")

    def render(self) -> str:
        text = f"{self.code}: {self.message}"
        return f"{text} Next step: {self.next_step}" if self.next_step else text


def to_tool_error(exc: CalendarError) -> ToolError:
    return ToolError(exc.render())
