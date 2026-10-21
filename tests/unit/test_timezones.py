from datetime import date, datetime

import pytest

from icloud_mcp_server.core.errors import CalendarError
from icloud_mcp_server.core.timezones import (
    display_for_event,
    format_all_day,
    get_zone,
    parse_range,
    zone_label,
)
from tests.conftest import MANILA, TOKYO, all_day, timed

ZONES = ["Asia/Tokyo", "Asia/Manila"]


def test_tokyo_is_one_hour_ahead_of_manila():
    ev = timed("x", datetime(2026, 10, 22, 10, 0, tzinfo=MANILA), datetime(2026, 10, 22, 11, 0, tzinfo=MANILA), "Asia/Manila")
    shown = display_for_event(ev, ZONES, TOKYO)
    assert shown["Asia/Tokyo"] == "Thu 22 Oct 2026 11:00-12:00 JST"
    assert shown["Asia/Manila"] == "Thu 22 Oct 2026 10:00-11:00 PHT"


def test_crossing_midnight_in_other_zone_shows_both_dates():
    ev = timed("x", datetime(2026, 10, 22, 23, 30, tzinfo=MANILA), datetime(2026, 10, 23, 0, 30, tzinfo=MANILA), "Asia/Manila")
    shown = display_for_event(ev, ZONES, TOKYO)
    assert shown["Asia/Manila"] == "Thu 22 Oct 2026 23:30 to Fri 23 Oct 2026 00:30 PHT"
    assert shown["Asia/Tokyo"] == "Fri 23 Oct 2026 00:30-01:30 JST"


def test_all_day_is_never_shifted_between_zones():
    ev = all_day("x", date(2026, 10, 24), date(2026, 10, 25))
    shown = display_for_event(ev, ZONES, TOKYO)
    assert shown["Asia/Tokyo"] == shown["Asia/Manila"] == "Sat 24 Oct 2026 (all day, date not shifted)"


def test_multi_day_all_day_end_is_exclusive():
    assert format_all_day(date(2026, 10, 24), date(2026, 10, 27)) == (
        "Sat 24 Oct 2026 to Mon 26 Oct 2026 (all day, dates not shifted)"
    )


def test_floating_time_is_read_in_default_zone():
    ev = timed("x", datetime(2026, 10, 23, 9, 0), datetime(2026, 10, 23, 10, 0), "floating")
    shown = display_for_event(ev, ZONES, TOKYO)
    assert shown["Asia/Tokyo"] == "Fri 23 Oct 2026 09:00-10:00 JST"
    assert shown["Asia/Manila"] == "Fri 23 Oct 2026 08:00-09:00 PHT"


def test_unknown_zone_labels_use_utc_offset():
    moment = datetime(2026, 10, 22, 10, 0, tzinfo=get_zone("America/New_York"))
    assert zone_label("America/New_York", moment) == "UTC-04:00"


def test_parse_range_date_only_end_includes_the_whole_day():
    start, end = parse_range("2026-10-22", "2026-10-22", TOKYO)
    assert start == datetime(2026, 10, 22, 0, 0, tzinfo=TOKYO)
    assert end == datetime(2026, 10, 23, 0, 0, tzinfo=TOKYO)


def test_parse_range_naive_datetime_uses_default_zone():
    start, _ = parse_range("2026-10-22T09:00:00", "2026-10-22T10:00:00", TOKYO)
    assert start.utcoffset().total_seconds() == 9 * 3600


@pytest.mark.parametrize(
    "start,end",
    [("2026-10-22", "2026-10-21"), ("2026-01-01", "2027-06-01"), ("nonsense", "2026-10-22")],
)
def test_bad_ranges_are_rejected(start, end):
    with pytest.raises(CalendarError) as info:
        parse_range(start, end, TOKYO)
    assert info.value.code == "INVALID_RANGE"


def test_unknown_zone_name_is_a_clear_error():
    with pytest.raises(CalendarError) as info:
        get_zone("Mars/Olympus")
    assert info.value.code == "INVALID_TIMEZONE"
