"""CalDAV backend (iCloud). Read operations only in milestone M2.

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
import threading
import time as _time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any, TypeVar

import caldav
import recurring_ical_events
from caldav.lib import error as dav_error

from ..core.errors import CalendarError
from ..core.models import CalendarInfo, Event
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

    # ---- reads ------------------------------------------------------------------------------

    def list_calendars(self) -> list[CalendarInfo]:
        def go() -> list[CalendarInfo]:
            return [
                CalendarInfo(calendar_id=cid, calendar_name=name, source=self._source)
                for cid, (_cal, name) in self._load_calendars(force=True).items()
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
                obj = cal.event_by_uid(uid)
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
