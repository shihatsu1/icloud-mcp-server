"""Validation and comparison helpers for the write tools. Pure functions, no network.

Everything a tool accepts is checked here before a preview exists, so a bad request never gets a
confirm token. Attendees are deliberately not supported: inviting people sends real email.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .errors import CalendarError
from .models import FLOATING, Event, NewEvent
from .timezones import as_aware, get_zone

MAX_TITLE_IN = 500
MAX_LOCATION_IN = 500
MAX_NOTES_IN = 5000
MAX_ALARMS = 5
MAX_ALARM_MINUTES = 40320  # four weeks
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
FREQUENCIES = ("daily", "weekly", "monthly", "yearly")
CHANGE_KEYS = ("title", "start", "end", "timezone", "location", "notes", "alarms_minutes", "recurrence")
RECURRENCE_KEYS = {"frequency", "interval", "by_weekday", "by_month_day", "count", "until"}


def _bad(message: str) -> CalendarError:
    return CalendarError("INVALID_INPUT", message)


def _text(value: Any, name: str, limit: int, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise _bad(f"{name} is required.")
        return None
    if not isinstance(value, str):
        raise _bad(f"{name} must be text.")
    text = value.replace("\x00", "").strip()
    if required and not text:
        raise _bad(f"{name} must not be empty.")
    if len(text) > limit:
        raise _bad(f"{name} is too long (at most {limit} characters).")
    return text


def check_alarms(value: Any) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise _bad("alarms_minutes must be a list of whole minutes before the start.")
    minutes: set[int] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= MAX_ALARM_MINUTES:
            raise _bad(f"Each alarm must be a whole number of minutes from 0 to {MAX_ALARM_MINUTES}.")
        minutes.add(item)
    if len(minutes) > MAX_ALARMS:
        raise _bad(f"At most {MAX_ALARMS} alarms are allowed.")
    return tuple(sorted(minutes))


def check_recurrence(value: Any) -> dict:
    if not isinstance(value, dict):
        raise _bad("recurrence must be an object such as {\"frequency\": \"weekly\", \"by_weekday\": [\"TU\"]}.")
    unknown = sorted(set(value) - RECURRENCE_KEYS)
    if unknown:
        raise _bad(f"Unknown recurrence field(s): {', '.join(unknown)}.")
    frequency = value.get("frequency")
    if frequency not in FREQUENCIES:
        raise _bad(f"recurrence.frequency must be one of {', '.join(FREQUENCIES)}.")
    out: dict[str, Any] = {"frequency": frequency}
    interval = value.get("interval", 1)
    if isinstance(interval, bool) or not isinstance(interval, int) or not 1 <= interval <= 99:
        raise _bad("recurrence.interval must be a whole number from 1 to 99.")
    out["interval"] = interval
    weekdays = value.get("by_weekday")
    if weekdays is not None:
        if not isinstance(weekdays, list) or not weekdays or any(d not in WEEKDAYS for d in weekdays):
            raise _bad(f"recurrence.by_weekday must be a list of {', '.join(WEEKDAYS)}.")
        out["by_weekday"] = sorted(set(weekdays), key=WEEKDAYS.index)
    month_days = value.get("by_month_day")
    if month_days is not None:
        if (
            not isinstance(month_days, list)
            or not month_days
            or any(isinstance(d, bool) or not isinstance(d, int) or not 1 <= d <= 31 for d in month_days)
        ):
            raise _bad("recurrence.by_month_day must be a list of days from 1 to 31.")
        out["by_month_day"] = sorted(set(month_days))
    count, until = value.get("count"), value.get("until")
    if count is not None and until is not None:
        raise _bad("Use either recurrence.count or recurrence.until, not both.")
    if count is not None:
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1000:
            raise _bad("recurrence.count must be a whole number from 1 to 1000.")
        out["count"] = count
    if until is not None:
        try:
            out["until"] = date.fromisoformat(str(until)).isoformat()
        except ValueError:
            raise _bad("recurrence.until must be a date like 2026-12-31.") from None
    return out


def summarize_recurrence(rec: dict | None) -> str | None:
    if not rec:
        return None
    unit = {"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}.get(rec.get("frequency"), "?")
    interval = rec.get("interval") or 1
    text = f"every {unit}" if interval == 1 else f"every {interval} {unit}s"
    if rec.get("by_weekday"):
        text += " on " + ", ".join(rec["by_weekday"])
    if rec.get("by_month_day"):
        text += " on day " + ", ".join(str(d) for d in rec["by_month_day"])
    if rec.get("count"):
        text += f", {rec['count']} times"
    if rec.get("until"):
        text += f", until {rec['until']}"
    return text


def _parse_day(value: Any, name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise _bad(f"{name} must be a date like 2026-11-02.") from None


def _parse_moment(value: Any, zone: ZoneInfo, name: str) -> datetime:
    text = str(value).strip()
    if len(text) <= 10:
        raise _bad(f"{name} needs a time, like 2026-11-02T15:00:00 (use start_date for all-day events).")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise _bad(f"{name} must be an ISO 8601 date-time like 2026-11-02T15:00:00+09:00.") from None
    return moment.replace(tzinfo=zone) if moment.tzinfo is None else moment.astimezone(zone)


def build_new_event(
    *,
    calendar_id: str,
    title: Any,
    start: Any = None,
    end: Any = None,
    timezone: Any = None,
    all_day: bool = False,
    start_date: Any = None,
    end_date: Any = None,
    location: Any = None,
    notes: Any = None,
    alarms_minutes: Any = None,
    recurrence: Any = None,
    default_timezone: str,
) -> NewEvent:
    clean_title = _text(title, "title", MAX_TITLE_IN, required=True)
    assert clean_title is not None
    if all_day:
        if start or end or timezone:
            raise _bad("For an all-day event use start_date and end_date, not start, end or timezone.")
        if not start_date:
            raise _bad("start_date is required for an all-day event.")
        first = _parse_day(start_date, "start_date")
        last = _parse_day(end_date, "end_date") if end_date else first + timedelta(days=1)
        if last <= first:
            raise _bad("end_date is exclusive and must be after start_date (one day: the next date).")
        begin, finish, zone_name = first, last, None
    else:
        if start_date or end_date:
            raise _bad("start_date and end_date are only for all-day events; set all_day to true.")
        if not start or not end:
            raise _bad("start and end are required for a timed event.")
        zone_name = str(timezone).strip() if timezone else default_timezone
        zone = get_zone(zone_name)
        begin, finish = _parse_moment(start, zone, "start"), _parse_moment(end, zone, "end")
        if finish <= begin:
            raise _bad("The end must be after the start.")
    return NewEvent(
        calendar_id=calendar_id,
        title=clean_title,
        all_day=bool(all_day),
        start=begin,
        end=finish,
        tzid=zone_name,
        location=_text(location, "location", MAX_LOCATION_IN) or None,
        notes=_text(notes, "notes", MAX_NOTES_IN) or None,
        alarms_minutes=check_alarms(alarms_minutes),
        recurrence=check_recurrence(recurrence) if recurrence is not None else None,
    )


def normalize_changes(target: Event, changes: Any, default_tz: ZoneInfo, *, allow_recurrence: bool) -> dict[str, Any]:
    """Validate an update request against the event it targets and return backend-ready values."""
    if not isinstance(changes, dict) or not changes:
        raise _bad(f"changes must name at least one field: {', '.join(CHANGE_KEYS)}.")
    unknown = sorted(set(changes) - set(CHANGE_KEYS))
    if unknown:
        raise _bad(f"Unknown field(s) in changes: {', '.join(unknown)}. Allowed: {', '.join(CHANGE_KEYS)}.")
    out: dict[str, Any] = {}
    if "title" in changes:
        out["title"] = _text(changes["title"], "title", MAX_TITLE_IN, required=True)
    if "location" in changes:
        out["location"] = _text(changes["location"], "location", MAX_LOCATION_IN) or ""
    if "notes" in changes:
        out["notes"] = _text(changes["notes"], "notes", MAX_NOTES_IN) or ""
    if "alarms_minutes" in changes:
        out["alarms_minutes"] = check_alarms(changes["alarms_minutes"])
    if "recurrence" in changes:
        if not allow_recurrence:
            raise _bad("The recurrence can only be changed for the whole series (scope whole_series).")
        out["recurrence"] = None if changes["recurrence"] is None else check_recurrence(changes["recurrence"])

    timing = [k for k in ("start", "end", "timezone") if k in changes]
    if timing:
        old_start, old_end = as_aware(target, default_tz)
        if target.all_day:
            if "timezone" in changes:
                raise _bad("An all-day event has no time zone.")
            first = _parse_day(changes["start"], "start") if "start" in changes else _as_day(target.start)
            length = _as_day(target.end) - _as_day(target.start)
            last = _parse_day(changes["end"], "end") if "end" in changes else first + length
            if last <= first:
                raise _bad("The end is exclusive and must be after the start.")
            out["start"], out["end"] = first, last
        else:
            if "timezone" in changes:
                zone_name = str(changes["timezone"]).strip()
                zone = get_zone(zone_name)
            else:
                zone_name = target.tzid if target.tzid and target.tzid != FLOATING else default_tz.key
                zone = get_zone(zone_name)
            if "start" in changes:
                begin = _parse_moment(changes["start"], zone, "start")
            elif "timezone" in changes:  # same wall-clock time, now in the new zone
                begin = old_start.replace(tzinfo=None).replace(tzinfo=zone)
            else:
                begin = old_start.astimezone(zone)
            if "end" in changes:
                finish = _parse_moment(changes["end"], zone, "end")
            elif "start" in changes or "timezone" in changes:
                finish = begin + (old_end - old_start)  # keep the length
            else:
                finish = old_end.astimezone(zone)
            if finish <= begin:
                raise _bad("The end must be after the start.")
            out["start"], out["end"], out["tzid"] = begin, finish, zone_name
    return out


def _as_day(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def apply_changes(event: Event, changes: dict[str, Any]) -> Event:
    """The event as it should look after the change (also what the re-read is compared with)."""
    fields: dict[str, Any] = {}
    for key in ("title", "start", "end", "tzid", "alarms_minutes"):
        if key in changes:
            fields[key] = changes[key]
    for key in ("location", "notes"):
        if key in changes:
            fields[key] = changes[key] or None
    if "recurrence" in changes:
        fields["recurrence"] = changes["recurrence"]
        fields["is_recurring"] = changes["recurrence"] is not None
        fields["rrule_text"] = None
    return replace(event, **fields)


_COMPARE = {
    "title": "title",
    "location": "location",
    "notes": "notes",
    "start": "start",
    "end": "end",
    "tzid": "timezone",
    "alarms_minutes": "alarms_minutes",
    "recurrence": "recurrence",
}


def _same_text(a: str | None, b: str | None) -> bool:
    return (a or "").strip() == (b or "").strip()


def _recurrence_key(rec: dict | None):
    if not rec:
        return None
    return (
        rec.get("frequency"),
        rec.get("interval") or 1,
        tuple(sorted(rec.get("by_weekday") or [])),
        tuple(sorted(rec.get("by_month_day") or [])),
        rec.get("count"),
    )


def differences(actual: Event, expected: Event, keys: list[str], default_tz: ZoneInfo) -> list[str]:
    """Names of the compared fields whose re-read value differs from what was previewed."""
    wrong: list[str] = []
    a_start, a_end = as_aware(actual, default_tz)
    e_start, e_end = as_aware(expected, default_tz)
    for key in keys:
        if key in ("title", "location", "notes"):
            ok = _same_text(getattr(actual, key), getattr(expected, key))
        elif key == "start":
            ok = a_start == e_start
        elif key == "end":
            ok = a_end == e_end
        elif key == "tzid":
            ok = expected.all_day or actual.tzid == expected.tzid
        elif key == "alarms_minutes":
            ok = tuple(sorted(actual.alarms_minutes)) == tuple(sorted(expected.alarms_minutes))
        elif key == "recurrence":
            ok = _recurrence_key(actual.recurrence) == _recurrence_key(expected.recurrence)
        else:
            ok = True
        if not ok:
            wrong.append(_COMPARE.get(key, key))
    return wrong
