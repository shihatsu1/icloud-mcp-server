"""Time-zone handling: parsing ranges, converting events, and rendering display strings.

Rules (docs/DESIGN.md section 5):
- Timed events show their time in every configured display zone.
- All-day events are dates and are never shifted between zones.
- Floating times (no zone) are read in the default zone and labelled as floating.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import CalendarError
from .models import FLOATING, Event

MAX_RANGE = timedelta(days=366)

# tzdata abbreviations are ambiguous or missing for some zones (Manila's can read "PST"),
# so well-known zones get explicit labels and everything else falls back to a UTC offset.
ZONE_LABELS = {"Asia/Tokyo": "JST", "Asia/Manila": "PHT", "UTC": "UTC", "Etc/UTC": "UTC"}


def get_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise CalendarError("INVALID_TIMEZONE", f"Unknown time zone: {name!r}.") from exc


def zone_label(zone_name: str, moment: datetime) -> str:
    if zone_name in ZONE_LABELS:
        return ZONE_LABELS[zone_name]
    offset = moment.utcoffset() or timedelta(0)
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    hours, mins = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours:02d}:{mins:02d}"


def _parse_point(value: str, tz: ZoneInfo, *, is_end: bool) -> datetime:
    text = value.strip()
    try:
        if len(text) == 10:  # YYYY-MM-DD
            day = date.fromisoformat(text)
            if is_end:
                day = day + timedelta(days=1)  # a date-only end includes that whole day
            return datetime.combine(day, time.min, tzinfo=tz)
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CalendarError(
            "INVALID_RANGE", f"Cannot read {value!r} as a date or date-time (use ISO 8601)."
        ) from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)
    return moment


def parse_range(start: str, end: str, default_tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Turn two user strings into an aware, half-open [start, end) range."""
    s = _parse_point(start, default_tz, is_end=False)
    e = _parse_point(end, default_tz, is_end=True)
    if e <= s:
        raise CalendarError("INVALID_RANGE", "The end must be after the start.")
    if e - s > MAX_RANGE:
        raise CalendarError("INVALID_RANGE", "The range may span at most 366 days.")
    return s, e


def parse_instant(value: str, default_tz: ZoneInfo) -> datetime:
    return _parse_point(value, default_tz, is_end=False)


def event_zone(event: Event, default_tz: ZoneInfo) -> ZoneInfo:
    """Zone in which a timed event's wall-clock values should be read."""
    if event.tzid and event.tzid != FLOATING:
        return get_zone(event.tzid)
    return default_tz


def as_aware(event: Event, default_tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Start and end as aware datetimes. All-day events become midnight-to-midnight in default_tz."""
    if event.all_day:
        start = datetime.combine(_as_date(event.start), time.min, tzinfo=default_tz)
        end = datetime.combine(_as_date(event.end), time.min, tzinfo=default_tz)
        return start, end
    zone = event_zone(event, default_tz)
    start = _attach(event.start, zone)
    end = _attach(event.end, zone)
    return start, end


def _attach(value: date | datetime, zone: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=zone)
    return datetime.combine(value, time.min, tzinfo=zone)


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _day(moment: datetime) -> str:
    return f"{moment:%a} {moment.day} {moment:%b %Y}"


def _date_day(value: date) -> str:
    return f"{value:%a} {value.day} {value:%b %Y}"


def format_all_day(start: date, end_exclusive: date) -> str:
    last = end_exclusive - timedelta(days=1)
    if last <= start:
        return f"{_date_day(start)} (all day, date not shifted)"
    return f"{_date_day(start)} to {_date_day(last)} (all day, dates not shifted)"


def format_range(start: datetime, end: datetime, zone_name: str) -> str:
    zone = get_zone(zone_name)
    s, e = start.astimezone(zone), end.astimezone(zone)
    label = zone_label(zone_name, s)
    if s.date() == e.date():
        return f"{_day(s)} {s:%H:%M}-{e:%H:%M} {label}"
    return f"{_day(s)} {s:%H:%M} to {_day(e)} {e:%H:%M} {label}"


def display_for_event(event: Event, zones: list[str], default_tz: ZoneInfo) -> dict[str, str]:
    if event.all_day:
        text = format_all_day(_as_date(event.start), _as_date(event.end))
        return {name: text for name in zones}
    start, end = as_aware(event, default_tz)
    return {name: format_range(start, end, name) for name in zones}


def display_for_range(start: datetime, end: datetime, zones: list[str]) -> dict[str, str]:
    return {name: format_range(start, end, name) for name in zones}
