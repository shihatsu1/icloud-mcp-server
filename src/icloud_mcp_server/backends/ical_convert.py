"""Convert icalendar VEVENT components into domain Events.

Pure functions, no network. Recurrence expansion is done by python-caldav / recurring-ical-events
before components reach this module; nothing here interprets RRULE semantics. The structured
`recurrence` value is a display-only summary of the series rule.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from icalendar import Component

from ..core.models import FLOATING, Event

_WEEKDAYS = {"MO", "TU", "WE", "TH", "FR", "SA", "SU"}


def _text(component: Component, name: str) -> str | None:
    value = component.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _valid_zone(name: str | None) -> str | None:
    if not name:
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None
    return name


def split_tz(value: datetime, params_tzid: str | None) -> tuple[datetime, str]:
    """Return (value, tzid). tzid is an IANA name, 'UTC', or 'floating'.

    Non-IANA zones (e.g. Windows names) are converted to UTC so the instant stays correct.
    """
    if value.tzinfo is None:
        return value, FLOATING
    key = getattr(value.tzinfo, "key", None)
    if _valid_zone(key):
        return value, key
    if _valid_zone(params_tzid):
        return value.astimezone(ZoneInfo(params_tzid)), params_tzid  # type: ignore[arg-type]
    if value.utcoffset() == timedelta(0) and (
        value.tzinfo is UTC or str(value.tzname()) in {"UTC", "Z"}
    ):
        return value, "UTC"
    return value.astimezone(UTC), "UTC"


def _is_all_day(value: date | datetime) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def _end_of(component: Component, start: date | datetime) -> date | datetime:
    dtend = component.get("DTEND")
    if dtend is not None:
        return dtend.dt
    duration = component.get("DURATION")
    if duration is not None:
        return start + duration.dt
    if _is_all_day(start):
        return start + timedelta(days=1)
    return start


def _alarms_minutes(component: Component) -> tuple[int, ...]:
    out: list[int] = []
    for sub in component.subcomponents:
        if sub.name != "VALARM":
            continue
        trigger = sub.get("TRIGGER")
        delta = getattr(trigger, "dt", None)
        if isinstance(delta, timedelta) and delta <= timedelta(0):
            out.append(int(abs(delta.total_seconds()) // 60))
    return tuple(sorted(set(out)))


def _attendees(component: Component) -> tuple[str, ...]:
    raw = component.get("ATTENDEE")
    if raw is None:
        return ()
    items = raw if isinstance(raw, list) else [raw]
    return tuple(str(a).replace("mailto:", "").replace("MAILTO:", "") for a in items)


def _count_exceptions(component: Component, siblings: int = 0) -> int:
    exdates = component.get("EXDATE")
    if exdates is None:
        total = 0
    else:
        groups = exdates if isinstance(exdates, list) else [exdates]
        total = sum(len(getattr(g, "dts", [])) for g in groups)
    return total + siblings


def summarize_rrule(rrule) -> tuple[dict | None, str | None]:
    """Display-only structured summary plus the raw rule text."""
    if rrule is None:
        return None, None
    raw = rrule.to_ical().decode() if hasattr(rrule, "to_ical") else str(rrule)
    freq = (rrule.get("FREQ") or [None])[0]
    summary: dict = {"frequency": str(freq).lower() if freq else None}
    if rrule.get("INTERVAL"):
        summary["interval"] = int(rrule["INTERVAL"][0])
    byday = [str(d)[-2:] for d in rrule.get("BYDAY", [])]
    if byday and all(d in _WEEKDAYS for d in byday):
        summary["by_weekday"] = byday
    if rrule.get("BYMONTHDAY"):
        summary["by_month_day"] = [int(d) for d in rrule["BYMONTHDAY"]]
    if rrule.get("COUNT"):
        summary["count"] = int(rrule["COUNT"][0])
    if rrule.get("UNTIL"):
        until = rrule["UNTIL"][0]
        summary["until"] = until.isoformat() if hasattr(until, "isoformat") else str(until)
    known = {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "COUNT", "UNTIL"}
    extra = sorted(k for k in rrule if k not in known)
    if extra:
        summary["other_parts"] = extra
    return summary, raw


def component_to_event(
    component: Component,
    *,
    calendar_id: str,
    calendar_name: str,
    event_id: str,
    version: str | None = None,
    override_siblings: int = 0,
) -> Event:
    """Build an Event from one VEVENT (an expanded occurrence or a single event)."""
    dtstart = component["DTSTART"]
    start = dtstart.dt
    end = _end_of(component, start)
    all_day = _is_all_day(start)

    tzid: str | None
    if all_day:
        tzid = None
        if isinstance(end, datetime):  # mixed types are malformed; treat the end as a date
            end = end.date()
    else:
        params_tzid = dtstart.params.get("TZID") if hasattr(dtstart, "params") else None
        start, tzid = split_tz(start, params_tzid)
        if isinstance(end, datetime):
            if tzid != FLOATING and end.tzinfo is not None:
                end = end.astimezone(start.tzinfo)
            elif tzid == FLOATING and end.tzinfo is not None:
                end = end.replace(tzinfo=None)
        else:  # DTEND as a date on a timed event: malformed; end where it starts
            end = start

    recurrence_id = component.get("RECURRENCE-ID")
    rrule = component.get("RRULE")
    is_recurring = recurrence_id is not None or rrule is not None
    occurrence_start = None
    if recurrence_id is not None:
        occurrence_start = recurrence_id.dt
    elif is_recurring:
        occurrence_start = start

    recurrence, rrule_text = summarize_rrule(rrule)
    transparency = (_text(component, "TRANSP") or "OPAQUE").upper()
    status = (_text(component, "STATUS") or "CONFIRMED").lower()
    last_modified = component.get("LAST-MODIFIED")
    uid = _text(component, "UID") or event_id

    return Event(
        event_id=event_id,
        calendar_id=calendar_id,
        calendar_name=calendar_name,
        all_day=all_day,
        start=start,
        end=end,
        tzid=tzid,
        title=_text(component, "SUMMARY"),
        location=_text(component, "LOCATION"),
        notes=_text(component, "DESCRIPTION"),
        url=_text(component, "URL"),
        availability="free" if transparency == "TRANSPARENT" else "busy",
        status=status if status in {"confirmed", "tentative", "cancelled"} else "confirmed",
        series_id=uid if is_recurring else None,
        is_recurring=is_recurring,
        occurrence_start=occurrence_start,
        recurrence=recurrence,
        rrule_text=rrule_text,
        exceptions_count=_count_exceptions(component, override_siblings),
        attendees=_attendees(component),
        alarms_minutes=_alarms_minutes(component),
        last_modified=getattr(last_modified, "dt", None),
        version=version,
    )
