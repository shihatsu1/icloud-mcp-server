"""In-memory backend for tests. Recurring series are stored pre-expanded: no recurrence logic here."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..core.errors import CalendarError
from ..core.models import CalendarInfo, Event, NewEvent
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
        self._seq = 0

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

    # ---- writes: simple in-memory versions of what CalDavBackend does ------------------------

    def create_event(self, new: NewEvent) -> Event:
        cal = next((c for c in self.calendars if c.calendar_id == new.calendar_id), None)
        if cal is None:
            raise CalendarError("NOT_FOUND", f"Unknown calendar_id: {new.calendar_id!r}.")
        self._seq += 1
        event_id = f"{new.calendar_id}~created-{self._seq}"
        recurring = new.recurrence is not None
        event = Event(
            event_id=event_id,
            calendar_id=cal.calendar_id,
            calendar_name=cal.calendar_name,
            all_day=new.all_day,
            start=new.start,
            end=new.end,
            tzid=new.tzid,
            title=new.title,
            location=new.location,
            notes=new.notes,
            series_id=event_id if recurring else None,
            is_recurring=recurring,
            occurrence_start=new.start if recurring else None,
            recurrence=new.recurrence,
            alarms_minutes=new.alarms_minutes,
            version="1",
        )
        self.events.append(event)
        return event

    def _targets(self, event_id: str, scope: str, occurrence_start, expected_version: str | None) -> list[Event]:
        same = [e for e in self.events if e.event_id == event_id]
        if not same:
            raise CalendarError("NOT_FOUND", f"No event {event_id!r} was found.")
        if expected_version is not None and same[0].version != expected_version:
            raise CalendarError("CONFLICT", "The event changed since the preview.")
        if scope != "occurrence":
            return same
        hit = [e for e in same if _same(e.occurrence_start or e.start, occurrence_start)]
        if not hit:
            raise CalendarError("NOT_FOUND", "That occurrence was not found in the series.")
        return hit

    def update_event(self, event_id, changes, *, scope, occurrence_start, expected_version) -> Event:
        targets = self._targets(event_id, scope, occurrence_start, expected_version)
        first = targets[0]
        shift = changes["start"] - first.start if "start" in changes else None
        length = changes["end"] - changes["start"] if "start" in changes and "end" in changes else None
        for old in targets:
            fields = {}
            for key in ("title", "alarms_minutes", "recurrence", "tzid"):
                if key in changes:
                    fields[key] = changes[key]
            for key in ("location", "notes"):
                if key in changes:
                    fields[key] = changes[key] or None
            if "recurrence" in changes:
                fields["is_recurring"] = changes["recurrence"] is not None
            if shift is not None:
                fields["start"] = old.start + shift
                fields["end"] = fields["start"] + (length if length is not None else old.end - old.start)
            elif "end" in changes:
                fields["end"] = changes["end"]
            new = replace(old, **fields, version=str(int(old.version or "0") + 1))
            self.events[self.events.index(old)] = new
        if scope != "occurrence":  # the series shares one version
            for i, e in enumerate(self.events):
                if e.event_id == event_id:
                    self.events[i] = replace(e, version=str(int(first.version or "0") + 1))
        return self.get_event(event_id, occurrence_start if scope == "occurrence" else None)

    def delete_event(self, event_id, *, scope, occurrence_start, expected_version) -> None:
        for event in self._targets(event_id, scope, occurrence_start, expected_version):
            self.events.remove(event)


def _same(a: date | datetime, b: date | datetime) -> bool:
    if isinstance(a, datetime) and isinstance(b, datetime):
        if a.tzinfo is None or b.tzinfo is None:
            return a.replace(tzinfo=None) == b.replace(tzinfo=None)
        return a == b
    return a == b
