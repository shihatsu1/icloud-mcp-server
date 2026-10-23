"""CalDAV backend (iCloud): reads (M2) and the writes behind confirm_change (M4).

iCloud quirks come from python-caldav's iCloud profile, a third-party probe from Oct 2026
(https://github.com/python-caldav/caldav/issues/730), and are confirmed by the opt-in integration
test and scripts/probe_icloud.py, not assumed:
- Basic auth with an app-specific password.
- Server-side free/busy returns 400, so free time is computed in core.freebusy.
- Server-side text search is unreliable, so text filtering happens in CalendarService.
- Recurring events are expanded client-side by python-caldav (recurring-ical-events).
"""

from __future__ import annotations

import hashlib
import re
import threading
import time as _time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Any, TypeVar
from urllib.parse import quote
from uuid import uuid4

import caldav
import recurring_ical_events
from caldav.lib import error as dav_error
from icalendar import Alarm
from icalendar import Calendar as ICalendar
from icalendar import Event as IEvent

from ..core.errors import CalendarError
from ..core.models import CalendarInfo, Event, NewEvent
from ..core.timezones import get_zone
from .ical_convert import component_to_event

T = TypeVar("T")

ICLOUD_URL = "https://caldav.icloud.com/"
CALENDAR_CACHE_SECONDS = 300
REQUEST_TIMEOUT = 15


def calendar_id_for(url: Any) -> str:
    return "cal-" + hashlib.sha256(str(url).encode()).hexdigest()[:16]


def make_event_id(calendar_id: str, uid: str) -> str:
    return f"{calendar_id}~{uid}"


def split_event_id(event_id: str) -> tuple[str, str]:
    calendar_id, sep, uid = event_id.partition("~")
    if not sep or not uid:
        raise CalendarError("NOT_FOUND", "That event_id is not valid.")
    return calendar_id, uid


def _classify(exc: Exception) -> CalendarError:
    """Map library errors to stable codes. Exception text is never passed on: it can contain URLs."""
    if isinstance(exc, CalendarError):
        return exc
    if isinstance(exc, dav_error.AuthorizationError):
        return CalendarError("CREDENTIALS_REJECTED", "iCloud rejected the app-specific password.")
    if isinstance(exc, dav_error.NotFoundError):
        return CalendarError("NOT_FOUND", "The calendar or event was not found.")
    if re.search(r"\b412\b|[Pp]recondition", str(exc)):  # If-Match failed: someone else changed it
        return CalendarError("CONFLICT", "The event was changed by someone else, so nothing was written.")
    return CalendarError("ICLOUD_UNREACHABLE", "Could not get a valid answer from the calendar server.")


class CalDavBackend:
    def __init__(
        self,
        username: str,
        password: str,
        url: str = ICLOUD_URL,
        *,
        source_name: str = "iCloud",
        allow_insecure_http: bool = False,  # tests against a local server only
        client: caldav.DAVClient | None = None,
    ):
        self._source = source_name
        self._lock = threading.RLock()
        self._client = client or caldav.DAVClient(
            url=url,
            username=username,
            password=password,
            timeout=REQUEST_TIMEOUT,
            require_tls=not allow_insecure_http,
        )
        self._calendars: dict[str, tuple[caldav.Calendar, str]] = {}
        self._loaded_at = 0.0
        self._urls: dict[str, str] = {}  # event_id -> address, learned from searches

    # ---- plumbing ---------------------------------------------------------------------------

    def _run(self, fn: Callable[[], T]) -> T:
        with self._lock:
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 - classified, text discarded
                raise _classify(exc) from None

    def _load_calendars(self, force: bool = False) -> dict[str, tuple[caldav.Calendar, str]]:
        if not force and self._calendars and _time.monotonic() - self._loaded_at < CALENDAR_CACHE_SECONDS:
            return self._calendars
        found: dict[str, tuple[caldav.Calendar, str]] = {}
        for cal in self._client.principal().calendars():
            try:
                components = cal.get_supported_components()
            except Exception:  # noqa: BLE001 - assume events if the server will not say
                components = ["VEVENT"]
            if components and "VEVENT" not in components:
                continue  # reminder lists (VTODO only) are not calendars of events
            name = cal.get_display_name() or "(unnamed)"
            found[calendar_id_for(cal.url)] = (cal, str(name))
        self._calendars = found
        self._loaded_at = _time.monotonic()
        return found

    def _calendar(self, calendar_id: str) -> tuple[caldav.Calendar, str]:
        calendars = self._load_calendars()
        if calendar_id not in calendars:
            calendars = self._load_calendars(force=True)
        if calendar_id not in calendars:
            raise CalendarError("NOT_FOUND", f"Unknown calendar_id: {calendar_id!r}.")
        return calendars[calendar_id]

    def _event_by_uid(self, calendar_id: str, cal: caldav.Calendar, uid: str) -> Any:
        """Find one event object by UID.

        iCloud answers python-caldav's UID query (REPORT) with "412 Precondition Failed" for every event
        (found against the real server on 2026-10-23), so event_by_uid cannot be used. Fetch by address
        instead: the address seen in an earlier search, then the usual <UID>.ics name, and only then scan
        the whole calendar for the UID.
        """
        key = make_event_id(calendar_id, uid)
        base = str(cal.url).rstrip("/") + "/"
        guesses = [u for u in (self._urls.get(key), f"{base}{quote(uid, safe='')}.ics") if u]
        for url in dict.fromkeys(guesses):
            try:
                obj = cal.event_by_url(url)
                obj.load()
            except dav_error.AuthorizationError:
                raise
            except dav_error.DAVError:
                continue
            if str(obj.icalendar_component.get("UID", "")) == uid:
                self._urls[key] = str(obj.url)
                return obj
        for obj in cal.events():
            if str(obj.icalendar_component.get("UID", "")) == uid:
                self._urls[key] = str(obj.url)
                return obj
        raise dav_error.NotFoundError("no event with that UID")

    # ---- reads ------------------------------------------------------------------------------

    def list_calendars(self) -> list[CalendarInfo]:
        def go() -> list[CalendarInfo]:
            return [
                CalendarInfo(calendar_id=cid, calendar_name=name, source=self._source)
                for cid, (_cal, name) in self._load_calendars().items()
            ]

        return self._run(go)

    def search_events(
        self, start: datetime, end: datetime, calendar_ids: list[str] | None = None
    ) -> list[Event]:
        def go() -> list[Event]:
            calendars = self._load_calendars()
            wanted = calendar_ids or list(calendars)
            events: list[Event] = []
            for cid in wanted:
                cal, name = self._calendar(cid)
                for obj in cal.search(event=True, start=start, end=end, expand=True):
                    comp = obj.icalendar_component
                    uid = str(comp.get("UID", ""))
                    if not uid or "DTSTART" not in comp:
                        continue
                    if getattr(obj, "url", None):
                        self._urls[make_event_id(cid, uid)] = str(obj.url)
                    events.append(
                        component_to_event(
                            comp,
                            calendar_id=cid,
                            calendar_name=name,
                            event_id=make_event_id(cid, uid),
                        )
                    )
            return events

        return self._run(go)

    def get_event(self, event_id: str, occurrence_start: datetime | date | None = None) -> Event:
        calendar_id, uid = split_event_id(event_id)

        def go() -> Event:
            cal, name = self._calendar(calendar_id)
            try:
                obj = self._event_by_uid(calendar_id, cal, uid)
            except dav_error.NotFoundError:
                raise CalendarError("NOT_FOUND", f"No event {event_id!r} was found.") from None
            version = _etag(obj)
            ical = obj.icalendar_instance
            components = [c for c in ical.walk() if c.name == "VEVENT"]
            if not components:
                raise CalendarError("NOT_FOUND", f"No event {event_id!r} was found.")
            masters = [c for c in components if "RECURRENCE-ID" not in c]
            overrides_list = [c for c in components if "RECURRENCE-ID" in c]
            overrides = len(overrides_list)
            master = masters[0] if masters else components[0]
            recurring = "RRULE" in master or "RDATE" in master or overrides > 0

            if occurrence_start is None or not recurring:
                return component_to_event(
                    master,
                    calendar_id=calendar_id,
                    calendar_name=name,
                    event_id=event_id,
                    version=version,
                    override_siblings=overrides,
                )

            # A moved occurrence no longer sits at its original time, so match overrides directly
            # by RECURRENCE-ID before expanding the series around the requested time.
            for comp in overrides_list:
                rid = comp["RECURRENCE-ID"].dt
                if _same_start(rid, occurrence_start):
                    return component_to_event(
                        comp,
                        calendar_id=calendar_id,
                        calendar_name=name,
                        event_id=event_id,
                        version=version,
                        override_siblings=overrides,
                    )

            probe = _occurrence_window(occurrence_start)
            for comp in recurring_ical_events.of(ical).between(*probe):
                ev = component_to_event(
                    comp,
                    calendar_id=calendar_id,
                    calendar_name=name,
                    event_id=event_id,
                    version=version,
                    override_siblings=overrides,
                )
                if _same_start(ev.occurrence_start or ev.start, occurrence_start):
                    return ev
            raise CalendarError("NOT_FOUND", "That occurrence was not found in the series.")

        return self._run(go)

    # ---- writes (milestone M4) ---------------------------------------------------------------
    # Reached only through CalendarService.confirm_change, which has already checked the allow-list,
    # the confirm token and the target's version. The version is checked again here against the
    # server's ETag, and the service re-reads the event afterwards and compares it with the preview.

    def create_event(self, new: NewEvent) -> Event:
        def go() -> Event:
            cal, _name = self._calendar(new.calendar_id)
            uid = str(uuid4()).upper()
            ical = ICalendar()
            ical.add("prodid", "-//icloud-mcp-server//EN")
            ical.add("version", "2.0")
            comp = IEvent()
            comp.add("uid", uid)
            comp.add("dtstamp", datetime.now(UTC))
            comp.add("summary", new.title)
            _set_times(comp, new.start, new.end)
            _set_text(comp, "LOCATION", new.location)
            _set_text(comp, "DESCRIPTION", new.notes)
            _set_alarms(comp, new.alarms_minutes, new.title)
            if new.recurrence:
                comp.add("rrule", _rrule(new.recurrence, new.all_day, new.tzid))
            ical.add_component(comp)
            if not new.all_day:
                ical.add_missing_timezones()  # TZID needs a VTIMEZONE for strict clients
            cal.save_event(ical.to_ical().decode())
            return self.get_event(make_event_id(new.calendar_id, uid))

        return self._run(go)

    def _open(self, event_id: str, expected_version: str | None):
        calendar_id, uid = split_event_id(event_id)
        cal, _name = self._calendar(calendar_id)
        try:
            obj = self._event_by_uid(calendar_id, cal, uid)
        except dav_error.NotFoundError:
            raise CalendarError("NOT_FOUND", f"No event {event_id!r} was found.") from None
        if expected_version is not None and _etag(obj) != expected_version:
            raise CalendarError("CONFLICT", "The event was changed after the preview, so nothing was written.")
        ical = obj.icalendar_instance
        components = [c for c in ical.walk() if c.name == "VEVENT"]
        if not components:
            raise CalendarError("NOT_FOUND", f"No event {event_id!r} was found.")
        masters = [c for c in components if "RECURRENCE-ID" not in c]
        return obj, ical, (masters[0] if masters else components[0]), components

    def update_event(
        self,
        event_id: str,
        changes: dict,
        *,
        scope: str,
        occurrence_start: datetime | date | None,
        expected_version: str | None,
    ) -> Event:
        def go() -> Event:
            obj, ical, master, components = self._open(event_id, expected_version)
            if scope == "occurrence":
                if occurrence_start is None:
                    raise CalendarError("INVALID_INPUT", "occurrence_start is required for one occurrence.")
                target = _find_override(components, occurrence_start)
                if target is None:
                    target = _new_override(master, occurrence_start)
                    ical.add_component(target)
            else:
                target = master
            _apply(target, changes, master)
            obj.save()
            return self.get_event(event_id, occurrence_start if scope == "occurrence" else None)

        return self._run(go)

    def delete_event(
        self,
        event_id: str,
        *,
        scope: str,
        occurrence_start: datetime | date | None,
        expected_version: str | None,
    ) -> None:
        def go() -> None:
            obj, ical, master, components = self._open(event_id, expected_version)
            if scope != "occurrence":
                obj.delete()
                return
            if occurrence_start is None:
                raise CalendarError("INVALID_INPUT", "occurrence_start is required for one occurrence.")
            override = _find_override(components, occurrence_start)
            if override is not None:
                ical.subcomponents = [c for c in ical.subcomponents if c is not override]
            master.add("exdate", _in_master_kind(master, occurrence_start))
            obj.save()

        self._run(go)


def _in_master_kind(master: Any, value: datetime | date) -> datetime | date:
    """Express an occurrence time the way the master's DTSTART is written (zone, floating or date)."""
    start = master["DTSTART"].dt
    if not isinstance(start, datetime):
        return value.date() if isinstance(value, datetime) else value
    if not isinstance(value, datetime):
        return datetime.combine(value, dtime.min, tzinfo=start.tzinfo)
    if start.tzinfo is None:
        return value.replace(tzinfo=None)
    return value.astimezone(start.tzinfo)


def _find_override(components: list[Any], occurrence_start: datetime | date) -> Any | None:
    for comp in components:
        if "RECURRENCE-ID" in comp and _same_start(comp["RECURRENCE-ID"].dt, occurrence_start):
            return comp
    return None


def _length(master: Any) -> timedelta:
    start = master["DTSTART"].dt
    if "DTEND" in master:
        return master["DTEND"].dt - start
    if "DURATION" in master:
        return master["DURATION"].dt
    return timedelta(days=1) if not isinstance(start, datetime) else timedelta(0)


def _new_override(master: Any, occurrence_start: datetime | date) -> Any:
    """A detached copy of the master for one occurrence (a VEVENT with RECURRENCE-ID)."""
    comp = IEvent.from_ical(master.to_ical())
    for name in ("RRULE", "RDATE", "EXDATE", "EXRULE", "DTSTART", "DTEND", "DURATION", "DTSTAMP"):
        comp.pop(name, None)
    when = _in_master_kind(master, occurrence_start)
    comp.add("recurrence-id", when)
    comp.add("dtstart", when)
    comp.add("dtend", when + _length(master))
    comp.add("dtstamp", datetime.now(UTC))
    return comp


def _set_times(comp: Any, start: date | datetime, end: date | datetime) -> None:
    for name in ("DTSTART", "DTEND", "DURATION"):
        comp.pop(name, None)
    comp.add("dtstart", start)
    comp.add("dtend", end)


def _set_text(comp: Any, name: str, value: str | None) -> None:
    comp.pop(name, None)
    if value:
        comp.add(name, value)


def _set_alarms(comp: Any, minutes: tuple[int, ...], title: str | None) -> None:
    comp.subcomponents = [c for c in comp.subcomponents if c.name != "VALARM"]
    for m in minutes:
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", title or "Reminder")
        alarm.add("trigger", timedelta(minutes=-m))
        comp.add_component(alarm)


def _rrule(rec: dict, all_day: bool, tzid: str | None) -> dict:
    out: dict[str, Any] = {"FREQ": rec["frequency"].upper()}
    if (rec.get("interval") or 1) != 1:
        out["INTERVAL"] = rec["interval"]
    if rec.get("by_weekday"):
        out["BYDAY"] = list(rec["by_weekday"])
    if rec.get("by_month_day"):
        out["BYMONTHDAY"] = list(rec["by_month_day"])
    if rec.get("count"):
        out["COUNT"] = rec["count"]
    if rec.get("until"):
        last = date.fromisoformat(rec["until"])
        # RFC 5545: UNTIL is a date for all-day events and a UTC time for timed ones; the last day counts.
        out["UNTIL"] = last if all_day else datetime.combine(last, dtime(23, 59, 59), tzinfo=get_zone(tzid or "UTC")).astimezone(UTC)
    return out


def _apply(target: Any, changes: dict, master: Any) -> None:
    """Write already-validated changes onto one VEVENT."""
    if "title" in changes:
        target.pop("SUMMARY", None)
        target.add("summary", changes["title"])
    if "location" in changes:
        _set_text(target, "LOCATION", changes["location"])
    if "notes" in changes:
        _set_text(target, "DESCRIPTION", changes["notes"])
    if "start" in changes:
        _set_times(target, changes["start"], changes["end"])
    if "alarms_minutes" in changes:
        _set_alarms(target, changes["alarms_minutes"], str(target.get("SUMMARY", "")) or None)
    if "recurrence" in changes:
        target.pop("RRULE", None)
        if changes["recurrence"]:
            start = target["DTSTART"].dt
            zone = getattr(start, "tzinfo", None)
            all_day = not isinstance(start, datetime)
            target.add("rrule", _rrule(changes["recurrence"], all_day, getattr(zone, "key", None)))
    target.pop("DTSTAMP", None)
    target.add("dtstamp", datetime.now(UTC))


def _etag(obj: Any) -> str | None:
    value = getattr(obj, "etag", None)
    if value:
        return str(value)
    try:
        from caldav.elements import dav

        got = obj.get_property(dav.GetEtag())
        return str(got) if got else None
    except Exception:  # noqa: BLE001 - version is best effort in read-only milestone
        return None


def _occurrence_window(value: datetime | date) -> tuple[Any, Any]:
    if isinstance(value, datetime):
        return value - timedelta(minutes=1), value + timedelta(minutes=1)
    return value, value + timedelta(days=1)


def _same_start(a: datetime | date, b: datetime | date) -> bool:
    if isinstance(a, datetime) and isinstance(b, datetime):
        if a.tzinfo is None or b.tzinfo is None:
            return a.replace(tzinfo=None) == b.replace(tzinfo=None)
        return a == b
    return a == b
