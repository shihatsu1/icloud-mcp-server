"""Tool logic shared by every backend. Read-only in M1; writes arrive in M4."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..backends.base import CalendarBackend
from ..config import Settings
from . import freebusy
from .errors import CalendarError
from .models import (
    FLOATING,
    CalendarOut,
    Event,
    EventDetail,
    EventSummary,
    FreeSlot,
    FreeTimeResult,
    SearchResult,
    UntrustedDetail,
    UntrustedSummary,
)
from .timezones import (
    as_aware,
    display_for_event,
    display_for_range,
    get_zone,
    parse_instant,
    parse_range,
)
from .untrusted import MAX_LOCATION, MAX_NOTES, MAX_TITLE, MAX_URL, NOTICE, clean

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _iso(value: date | datetime | None) -> str | None:
    return None if value is None else value.isoformat()


class CalendarService:
    def __init__(self, backend: CalendarBackend, settings: Settings):
        self.backend = backend
        self.settings = settings
        self.default_tz: ZoneInfo = get_zone(settings.default_timezone)

    # ---- conversion -------------------------------------------------------------------------

    def _times(self, event: Event) -> dict:
        """start/end fields in the event's own zone (all-day: plain dates)."""
        if event.all_day:
            return {
                "start": None,
                "end": None,
                "start_date": _iso(event.start.date() if isinstance(event.start, datetime) else event.start),
                "end_date": _iso(event.end.date() if isinstance(event.end, datetime) else event.end),
                "timezone": None,
                "interpreted_in": None,
            }
        start, end = as_aware(event, self.default_tz)
        floating = event.tzid in (None, FLOATING)
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "start_date": None,
            "end_date": None,
            "timezone": FLOATING if floating else event.tzid,
            "interpreted_in": self.settings.default_timezone if floating else None,
        }

    def _common(self, event: Event) -> dict:
        return {
            "event_id": event.event_id,
            "occurrence_start": _iso(event.occurrence_start),
            "series_id": event.series_id,
            "is_recurring": event.is_recurring,
            "calendar_id": event.calendar_id,
            "calendar_name": event.calendar_name,
            "all_day": event.all_day,
            "availability": event.availability,
            "display": display_for_event(event, list(self.settings.display_timezones), self.default_tz),
            **self._times(event),
        }

    def summary(self, event: Event) -> EventSummary:
        return EventSummary(
            **self._common(event),
            untrusted=UntrustedSummary(
                title=clean(event.title, MAX_TITLE), location=clean(event.location, MAX_LOCATION)
            ),
        )

    def detail(self, event: Event) -> EventDetail:
        return EventDetail(
            **self._common(event),
            untrusted=UntrustedDetail(
                title=clean(event.title, MAX_TITLE),
                location=clean(event.location, MAX_LOCATION),
                notes=clean(event.notes, MAX_NOTES),
                url=clean(event.url, MAX_URL),
            ),
            recurrence=event.recurrence,
            rrule_text=event.rrule_text,
            exceptions_count=event.exceptions_count,
            attendees=list(event.attendees),
            alarms_minutes=list(event.alarms_minutes),
            last_modified=_iso(event.last_modified),
            version=event.version,
            notice=NOTICE,
        )

    # ---- tools ------------------------------------------------------------------------------

    def list_calendars(self) -> list[CalendarOut]:
        out = []
        for cal in self.backend.list_calendars():
            writable = (
                self.settings.allow_writes
                and not cal.read_only_in_source
                and cal.calendar_name in self.settings.writable_calendars
            )
            out.append(
                CalendarOut(
                    calendar_id=cal.calendar_id,
                    calendar_name=cal.calendar_name,
                    source=cal.source,
                    color=cal.color,
                    writable_by_server=writable,
                    read_only_in_source=cal.read_only_in_source,
                    default_timezone=cal.default_timezone,
                )
            )
        return out

    def _check_calendar_ids(self, calendar_ids: list[str] | None) -> list[str] | None:
        if not calendar_ids:
            return None
        known = {c.calendar_id for c in self.backend.list_calendars()}
        unknown = [c for c in calendar_ids if c not in known]
        if unknown:
            raise CalendarError("NOT_FOUND", f"Unknown calendar_id: {unknown[0]!r}.")
        return list(calendar_ids)

    def search_events(
        self,
        start: str,
        end: str,
        query: str | None = None,
        calendar_ids: list[str] | None = None,
        include_all_day: bool = True,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> SearchResult:
        r_start, r_end = parse_range(start, end, self.default_tz)
        limit = max(1, min(limit, MAX_LIMIT))
        offset = _decode_cursor(cursor)
        ids = self._check_calendar_ids(calendar_ids)

        events = [
            e
            for e in self.backend.search_events(r_start, r_end, ids)
            if e.status != "cancelled" and (include_all_day or not e.all_day)
        ]
        events = [e for e in events if _overlaps(e, r_start, r_end, self.default_tz)]
        if query and query.strip():
            terms = [t.lower() for t in query.split()]
            events = [e for e in events if all(t in _haystack(e) for t in terms)]
        events.sort(key=lambda e: (as_aware(e, self.default_tz)[0], (e.title or "").lower()))

        page = events[offset : offset + limit]
        more = offset + limit < len(events)
        return SearchResult(
            events=[self.summary(e) for e in page],
            next_cursor=str(offset + limit) if more else None,
            notice=NOTICE,
        )

    def get_event(self, event_id: str, occurrence_start: str | None = None) -> EventDetail:
        occ = None
        if occurrence_start:
            occ = (
                date.fromisoformat(occurrence_start)
                if len(occurrence_start.strip()) == 10
                else parse_instant(occurrence_start, self.default_tz)
            )
        return self.detail(self.backend.get_event(event_id, occ))

    def find_free_time(
        self,
        start: str,
        end: str,
        duration_minutes: int,
        working_hours: dict[str, str] | None = None,
        working_hours_timezone: str | None = None,
        weekdays: list[str] | None = None,
        calendar_ids: list[str] | None = None,
        count_all_day_as_busy: bool = False,
        ignore_free_availability: bool = True,
        overlap_timezone: str | None = None,
        overlap_working_hours: dict[str, str] | None = None,
        max_results: int = 10,
    ) -> FreeTimeResult:
        if not 15 <= duration_minutes <= 480:
            raise CalendarError("INVALID_INPUT", "duration_minutes must be between 15 and 480.")
        r_start, r_end = parse_range(start, end, self.default_tz)
        wh_zone_name = working_hours_timezone or self.settings.default_timezone
        wh_zone = get_zone(wh_zone_name)
        hours = _hours(working_hours)
        days = freebusy.parse_weekdays(weekdays or ["MO", "TU", "WE", "TH", "FR"])
        ids = self._check_calendar_ids(calendar_ids)

        busy: list[freebusy.Interval] = []
        for event in self.backend.search_events(r_start, r_end, ids):
            if event.status == "cancelled":
                continue
            if ignore_free_availability and event.availability == "free":
                continue
            if event.all_day:
                if not count_all_day_as_busy:
                    continue
                busy.append(
                    freebusy.all_day_busy(_as_day(event.start), _as_day(event.end), wh_zone)
                )
            else:
                busy.append(as_aware(event, self.default_tz))

        overlap_zone = get_zone(overlap_timezone) if overlap_timezone else None
        slots = freebusy.find_free(
            r_start,
            r_end,
            busy,
            timedelta(minutes=duration_minutes),
            wh_zone,
            hours,
            days,
            overlap_zone,
            _hours(overlap_working_hours) if overlap_working_hours else None,
            max(1, min(max_results, 50)),
        )
        zones = list(self.settings.display_timezones)
        result = [
            FreeSlot(
                start=s.astimezone(wh_zone).isoformat(),
                end=e.astimezone(wh_zone).isoformat(),
                minutes=int((e - s).total_seconds() // 60),
                display=display_for_range(s, e, zones),
            )
            for s, e in slots
        ]
        note = (
            f"Free windows of at least {duration_minutes} minutes inside {hours[0]:%H:%M}-{hours[1]:%H:%M} "
            f"{wh_zone_name} on {', '.join(weekdays or ['MO', 'TU', 'WE', 'TH', 'FR'])}"
            + (f", also inside the overlap hours in {overlap_timezone}" if overlap_zone else "")
            + ". "
            + ("All-day events count as busy. " if count_all_day_as_busy else "All-day events were ignored. ")
            + "Each window may be longer than the requested duration."
        )
        return FreeTimeResult(slots=result, note=note)


# ---- helpers ----------------------------------------------------------------------------------


def _as_day(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _hours(value: dict[str, str] | None):
    value = value or {"start": "09:00", "end": "18:00"}
    return freebusy.parse_hhmm(value.get("start", "09:00")), freebusy.parse_hhmm(value.get("end", "18:00"))


def _overlaps(event: Event, start: datetime, end: datetime, default_tz: ZoneInfo) -> bool:
    e_start, e_end = as_aware(event, default_tz)
    return e_end > start and e_start < end


def _haystack(event: Event) -> str:
    return " ".join(filter(None, [event.title, event.notes, event.location])).lower()


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        value = int(cursor)
    except ValueError as exc:
        raise CalendarError("INVALID_INPUT", "cursor is not valid; use the next_cursor from the last result.") from exc
    if value < 0:
        raise CalendarError("INVALID_INPUT", "cursor is not valid.")
    return value

