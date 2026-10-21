from datetime import datetime, time, timedelta

from icloud_mcp_server.core import freebusy as fb
from tests.conftest import MANILA, TOKYO


def dt(day, h, m=0, tz=TOKYO):
    return datetime(2026, 10, day, h, m, tzinfo=tz)


HOURS = (time(9, 0), time(18, 0))
WEEK = {0, 1, 2, 3, 4}


def test_free_windows_exclude_busy_time():
    busy = [(dt(23, 10), dt(23, 12)), (dt(23, 15), dt(23, 16))]
    free = fb.find_free(dt(23, 0), dt(24, 0), busy, timedelta(minutes=60), TOKYO, HOURS, WEEK)
    assert free == [(dt(23, 9), dt(23, 10)), (dt(23, 12), dt(23, 15)), (dt(23, 16), dt(23, 18))]


def test_windows_shorter_than_the_duration_are_dropped():
    busy = [(dt(23, 9, 30), dt(23, 17, 30))]
    assert fb.find_free(dt(23, 0), dt(24, 0), busy, timedelta(minutes=60), TOKYO, HOURS, WEEK) == []
    short = fb.find_free(dt(23, 0), dt(24, 0), busy, timedelta(minutes=30), TOKYO, HOURS, WEEK)
    assert short == [(dt(23, 9), dt(23, 9, 30)), (dt(23, 17, 30), dt(23, 18))]


def test_weekends_are_skipped():
    # 24-25 Oct 2026 is Sat-Sun
    assert fb.find_free(dt(24, 0), dt(26, 0), [], timedelta(minutes=30), TOKYO, HOURS, WEEK) == []


def test_overlapping_and_adjacent_busy_blocks_merge():
    busy = [(dt(23, 10), dt(23, 11)), (dt(23, 11), dt(23, 12)), (dt(23, 10, 30), dt(23, 11, 30))]
    assert fb.merge(busy) == [(dt(23, 10), dt(23, 12))]


def test_overlap_mode_requires_both_zones_working_hours():
    # Tokyo 09:00-18:00 and Manila 09:00-18:00 overlap at 10:00-18:00 Tokyo (09:00-17:00 Manila)
    free = fb.find_free(
        dt(23, 0), dt(24, 0), [], timedelta(minutes=60), TOKYO, HOURS, WEEK, overlap_zone=MANILA
    )
    assert free == [(dt(23, 10), dt(23, 18))]


def test_events_are_clipped_to_the_search_range():
    free = fb.find_free(dt(23, 12), dt(23, 14), [], timedelta(minutes=30), TOKYO, HOURS, WEEK)
    assert free == [(dt(23, 12), dt(23, 14))]


def test_max_results_caps_the_output():
    free = fb.find_free(dt(19, 0), dt(24, 0), [], timedelta(minutes=30), TOKYO, HOURS, WEEK, max_results=2)
    assert len(free) == 2
