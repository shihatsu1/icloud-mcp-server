"""CalDavBackend against a local Radicale server. Radicale passing does NOT prove iCloud works:
see docs/TESTING.md; the opt-in real-iCloud integration run is a launch requirement."""

from zoneinfo import ZoneInfo

import pytest

from icloud_mcp_server.backends.caldav_backend import CalDavBackend
from icloud_mcp_server.config import Settings
from icloud_mcp_server.core.errors import CalendarError
from icloud_mcp_server.core.service import CalendarService

from . import fixtures as fx

MANILA = ZoneInfo("Asia/Manila")
TOKYO = ZoneInfo("Asia/Tokyo")


@pytest.fixture
def populated(backend):
    principal = backend._client.principal()
    work = principal.make_calendar(name="Work")
    home = principal.make_calendar(name="Home")
    work.save_event(fx.wrap(fx.WEEKLY_MASTER, fx.WEEKLY_OVERRIDE))
    work.save_event(fx.wrap(fx.CLIENT_SYNC))
    work.save_event(fx.wrap(fx.FLOATING))
    home.save_event(fx.wrap(fx.TRIP))
    return backend


@pytest.fixture
def service(populated):
    return CalendarService(populated, Settings())


def test_calendars_are_listed_with_stable_ids(populated):
    first = {c.calendar_name: c.calendar_id for c in populated.list_calendars()}
    second = {c.calendar_name: c.calendar_id for c in populated.list_calendars()}
    assert set(first) == {"Work", "Home"} and first == second


def test_recurring_series_expands_with_exdate_and_override(service):
    result = service.search_events("2026-10-19", "2026-11-12", query="weekly")
    starts = [e.start for e in result.events]
    assert starts == [
        "2026-10-20T15:00:00+08:00",
        "2026-10-27T17:00:00+08:00",  # moved occurrence
        "2026-11-10T15:00:00+08:00",  # 3 Nov removed by EXDATE
    ]
    assert all(e.is_recurring and e.series_id == "weekly-series" for e in result.events)
    # 15:00 Manila is 16:00 in Tokyo
    assert result.events[0].display["Asia/Tokyo"] == "Tue 20 Oct 2026 16:00-17:00 JST"


def test_all_day_multi_day_is_dates_not_shifted(service):
    result = service.search_events("2026-10-25", "2026-10-25")
    trip = next(e for e in result.events if e.untrusted.title == "Trip")
    assert trip.all_day and (trip.start_date, trip.end_date) == ("2026-10-24", "2026-10-27")
    assert trip.display["Asia/Tokyo"] == trip.display["Asia/Manila"]


def test_floating_time_is_labelled(service):
    result = service.search_events("2026-10-23", "2026-10-23", query="floating")
    ev = result.events[0]
    assert ev.timezone == "floating" and ev.start == "2026-10-23T09:00:00+09:00"


def test_manila_event_shows_in_both_zones(service):
    ev = service.search_events("2026-10-22", "2026-10-22", query="client").events[0]
    assert ev.timezone == "Asia/Manila"
    assert ev.display["Asia/Tokyo"] == "Thu 22 Oct 2026 11:00-12:00 JST"
    assert ev.display["Asia/Manila"] == "Thu 22 Oct 2026 10:00-11:00 PHT"


def test_get_event_master_summarises_the_series(service):
    found = service.search_events("2026-10-19", "2026-10-21", query="weekly").events[0]
    detail = service.get_event(found.event_id)
    assert detail.recurrence["frequency"] == "weekly" and detail.recurrence["count"] == 4
    assert detail.recurrence["by_weekday"] == ["TU"]
    assert detail.exceptions_count == 2  # one EXDATE plus one moved occurrence


def test_get_event_for_the_moved_occurrence(service):
    found = service.search_events("2026-10-26", "2026-10-28", query="weekly").events[0]
    detail = service.get_event(found.event_id, found.occurrence_start)
    assert detail.start == "2026-10-27T17:00:00+08:00"
    assert detail.untrusted.title == "Weekly review (moved)"


def test_get_event_not_found(service):
    cal_id = service.list_calendars()[0].calendar_id
    with pytest.raises(CalendarError) as info:
        service.get_event(f"{cal_id}~no-such-uid")
    assert info.value.code == "NOT_FOUND"
    with pytest.raises(CalendarError):
        service.get_event("garbage")


def test_free_time_uses_expanded_busy_slots(service):
    # Tue 27 Oct 2026: moved review 17:00-18:00 Manila == 18:00-19:00 Tokyo (outside 09-18 hours)
    result = service.find_free_time("2026-10-27", "2026-10-27", 60)
    assert result.slots[0].start == "2026-10-27T09:00:00+09:00"
    # Tue 20 Oct: 15:00-16:00 Manila == 16:00-17:00 Tokyo is busy
    busy_day = service.find_free_time("2026-10-20", "2026-10-20", 60)
    assert [(s.start[11:16], s.end[11:16]) for s in busy_day.slots] == [("09:00", "16:00"), ("17:00", "18:00")]


def test_unreachable_server_is_a_clear_error_without_leaking_the_url():
    backend = CalDavBackend("u", "p", url="http://127.0.0.1:9/", allow_insecure_http=True)
    with pytest.raises(CalendarError) as info:
        backend.list_calendars()
    assert info.value.code == "ICLOUD_UNREACHABLE"
    assert "127.0.0.1" not in info.value.render()
