"""In-memory backend for tests. Recurring series are stored pre-expanded: no recurrence logic here."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..core.errors import CalendarError
from ..core.models import CalendarInfo, Event
from ..core.timezones import as_aware


class FakeBackend:
    def __init__(
        self,
        calendars: list[CalendarInfo] | None = None,
        events: list[Event] | None = None,
        default_tz: str = "Asia/Tokyo",
    ):
        self.calendars = calendars or []
        self.events = events or []
        self._tz = ZoneInfo(default_tz)

    def list_calendars(self) -> list[CalendarInfo]:
        return list(self.calendars)

    def search_events(
        self, start: datetime, end: datetime, calendar_ids: list[str] | None = None
    ) -> list[Event]:
        found = []
        for event in self.events:
            if calendar_ids and event.calendar_id not in calendar_ids:
                continue
            e_start, e_end = as_aware(event, self._tz)
            if e_end > start and e_start < end:
                found.append(event)
        return found

    def get_event(self, event_id: str, occurrence_start: datetime | date | None = None) -> Event:
        matches = [e for e in self.events if e.event_id == event_id]
        if occurrence_start is not None:
            matches = [e for e in matches if _same(e.occurrence_start or e.start, occurrence_start)]
        if not matches:
            raise CalendarError("NOT_FOUND", f"No event {event_id!r} was found.")
        return matches[0]


def _same(a: date | datetime, b: date | datetime) -> bool:
    if isinstance(a, datetime) and isinstance(b, datetime):
        if a.tzinfo is None or b.tzinfo is None:
            return a.replace(tzinfo=None) == b.replace(tzinfo=None)
        return a == b
    return a == b
