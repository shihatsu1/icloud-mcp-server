"""The interface every calendar backend implements (CalDAV for iCloud; a fake for tests).

Backends return already-expanded occurrences and never filter by text: the service does that,
because iCloud's server-side text search is unreliable (python-caldav's iCloud profile).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from ..core.models import CalendarInfo, Event


class CalendarBackend(Protocol):
    def list_calendars(self) -> list[CalendarInfo]: ...

    def search_events(
        self, start: datetime, end: datetime, calendar_ids: list[str] | None = None
    ) -> list[Event]:
        """Events and recurring occurrences overlapping [start, end), expanded."""
        ...

    def get_event(self, event_id: str, occurrence_start: datetime | date | None = None) -> Event:
        """One event or one occurrence. Raises CalendarError('NOT_FOUND') if absent."""
        ...
