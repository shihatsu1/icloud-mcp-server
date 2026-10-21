"""Free-time search by plain interval arithmetic.

iCloud's server-side free/busy query returns HTTP 400 (python-caldav's iCloud profile), so free time
is computed here from already-expanded busy occurrences. No recurrence logic lives in this module.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .errors import CalendarError

Interval = tuple[datetime, datetime]

WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def parse_hhmm(value: str) -> time:
    try:
        hour, minute = value.split(":")
        parsed = time(int(hour), int(minute))
    except (ValueError, AttributeError) as exc:
        raise CalendarError("INVALID_INPUT", f"Working hours must look like '09:00', got {value!r}.") from exc
    return parsed


def parse_weekdays(values: list[str]) -> set[int]:
    try:
        return {WEEKDAYS[v.upper()] for v in values}
    except KeyError as exc:
        raise CalendarError("INVALID_INPUT", f"Unknown weekday {exc.args[0]!r}; use MO..SU.") from exc


def working_windows(
    range_start: datetime,
    range_end: datetime,
    zone: ZoneInfo,
    hours: tuple[time, time],
    weekdays: set[int],
) -> list[Interval]:
    """Working-hour windows (in `zone`) clipped to [range_start, range_end)."""
    start_t, end_t = hours
    if end_t <= start_t:
        raise CalendarError("INVALID_INPUT", "Working hours must end after they start.")
    first = range_start.astimezone(zone).date() - timedelta(days=1)
    last = range_end.astimezone(zone).date() + timedelta(days=1)
    windows: list[Interval] = []
    day = first
    while day <= last:
        if day.weekday() in weekdays:
            w_start = datetime.combine(day, start_t, tzinfo=zone)
            w_end = datetime.combine(day, end_t, tzinfo=zone)
            clipped = (max(w_start, range_start), min(w_end, range_end))
            if clipped[0] < clipped[1]:
                windows.append(clipped)
        day += timedelta(days=1)
    return windows


def merge(intervals: list[Interval]) -> list[Interval]:
    ordered = sorted(intervals, key=lambda iv: (iv[0], iv[1]))
    merged: list[Interval] = []
    for start, end in ordered:
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    a, b = merge(a), merge(b)
    out: list[Interval] = []
    i = j = 0
    while i < len(a) and j < len(b):
        start = max(a[i][0], b[j][0])
        end = min(a[i][1], b[j][1])
        if start < end:
            out.append((start, end))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def subtract(windows: list[Interval], busy: list[Interval]) -> list[Interval]:
    busy = merge(busy)
    result: list[Interval] = []
    for w_start, w_end in merge(windows):
        cursor = w_start
        for b_start, b_end in busy:
            if b_end <= cursor:
                continue
            if b_start >= w_end:
                break
            if b_start > cursor:
                result.append((cursor, min(b_start, w_end)))
            cursor = max(cursor, b_end)
            if cursor >= w_end:
                break
        if cursor < w_end:
            result.append((cursor, w_end))
    return result


def find_free(
    range_start: datetime,
    range_end: datetime,
    busy: list[Interval],
    duration: timedelta,
    zone: ZoneInfo,
    hours: tuple[time, time],
    weekdays: set[int],
    overlap_zone: ZoneInfo | None = None,
    overlap_hours: tuple[time, time] | None = None,
    max_results: int = 10,
) -> list[Interval]:
    """Maximal free windows of at least `duration` inside working hours (and, optionally, inside a
    second person's working hours as well)."""
    windows = working_windows(range_start, range_end, zone, hours, weekdays)
    if overlap_zone is not None:
        other_hours = overlap_hours or hours
        windows = intersect(windows, working_windows(range_start, range_end, overlap_zone, other_hours, weekdays))
    free = subtract(windows, busy)
    return [iv for iv in free if iv[1] - iv[0] >= duration][:max_results]


def all_day_busy(start: date, end_exclusive: date, zone: ZoneInfo) -> Interval:
    return (
        datetime.combine(start, time.min, tzinfo=zone),
        datetime.combine(end_exclusive, time.min, tzinfo=zone),
    )
