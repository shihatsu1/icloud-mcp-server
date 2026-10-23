"""The interface every calendar backend implements (CalDAV for iCloud; a fake for tests).

Backends return already-expanded occurrences and never filter by text: the service does that,
because iCloud's server-side text search is unreliable (python-caldav's iCloud profile).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from ..core.models import CalendarInfo, Event, NewEvent


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

    # ---- writes (milestone M4). Reached only through CalendarService.confirm_change. ----------

    def create_event(self, new: NewEvent) -> Event:
        """Create one event (or one recurring series) and return it as stored."""
        ...

    def update_event(
        self,
        event_id: str,
        changes: dict,
        *,
        scope: str,
        occurrence_start: datetime | date | None,
        expected_version: str | None,
    ) -> Event:
        """Apply normalized changes. scope is 'whole' (the event or the whole series) or 'occurrence'.
        Raises CalendarError('CONFLICT') if expected_version is given and no longer matches."""
        ...

    def delete_event(
        self,
        event_id: str,
        *,
        scope: str,
        occurrence_start: datetime | date | None,
        expected_version: str | None,
    ) -> None:
        """Delete the event or series (scope 'whole') or exactly one occurrence (scope 'occurrence')."""
        ...
