"""Writes through the whole stack (service, tokens, CalDavBackend) against a local Radicale server.

Radicale passing does NOT prove iCloud accepts the same data; the opt-in integration run against the
real TEST calendar is the launch requirement (docs/TESTING.md).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from icloud_mcp_server.config import Settings
from icloud_mcp_server.core.errors import CalendarError
from icloud_mcp_server.core.service import CalendarService

from . import fixtures as fx

MANILA = ZoneInfo("Asia/Manila")


@pytest.fixture
def parts(backend):
    principal = backend._client.principal()
    work = principal.make_calendar(name="Work")
    principal.make_calendar(name="Home")
    work.save_event(fx.wrap(fx.WEEKLY_MASTER, fx.WEEKLY_OVERRIDE))
    work.save_event(fx.wrap(fx.CLIENT_SYNC))
    return backend, work


@pytest.fixture
def svc(parts):
    backend, _work = parts
    return CalendarService(backend, Settings(allow_writes=True, writable_calendars=frozenset({"Work"})))


@pytest.fixture
def work_id(svc):
    return next(c.calendar_id for c in svc.backend.list_calendars() if c.calendar_name == "Work")


@pytest.fixture
def home_id(svc):
    return next(c.calendar_id for c in svc.backend.list_calendars() if c.calendar_name == "Home")


def apply(svc, preview):
    assert preview.confirm_token, preview
    return svc.confirm_change(preview.confirm_token)


def raw(svc, event_id):
    cid, uid = event_id.split("~", 1)
    cal, _ = svc.backend._calendar(cid)
    return cal.event_by_uid(uid).data


def titles_on(svc, start, end, query=None):
    return [(e.title, e.start) for e in (svc.backend.search_events(*_range(start, end)))
            if query is None or query in (e.title or "")]


def _range(start, end):
    z = ZoneInfo("Asia/Tokyo")
    return datetime.fromisoformat(start).replace(tzinfo=z), datetime.fromisoformat(end).replace(tzinfo=z)


TIMED = {"title": "Dentist", "start": "2026-11-02T15:00:00", "end": "2026-11-02T16:00:00", "timezone": "Asia/Tokyo"}


# ---- create -------------------------------------------------------------------------------------


def test_create_a_timed_event_in_tokyo_and_read_it_back(svc, work_id):
    out = apply(svc, svc.preview_create(work_id, **TIMED, location="Clinic", notes="Bring forms", alarms_minutes=[10, 60]))
    ev = out.event
    assert ev.untrusted.title == "Dentist" and ev.untrusted.location == "Clinic" and ev.untrusted.notes == "Bring forms"
    assert ev.start == "2026-11-02T15:00:00+09:00" and ev.timezone == "Asia/Tokyo"
    assert ev.alarms_minutes == [10, 60]
    assert ev.display["Asia/Manila"] == "Mon 2 Nov 2026 14:00-15:00 PHT"
    data = raw(svc, ev.event_id)
    assert "TZID=Asia/Tokyo" in data and "BEGIN:VTIMEZONE" in data  # strict clients need the VTIMEZONE


def test_create_an_all_day_event_keeps_the_dates(svc, work_id):
    ev = apply(svc, svc.preview_create(work_id, title="Retreat", all_day=True, start_date="2026-12-01", end_date="2026-12-04")).event
    assert ev.all_day and (ev.start_date, ev.end_date) == ("2026-12-01", "2026-12-04") and ev.timezone is None
    assert "DTSTART;VALUE=DATE:20261201" in raw(svc, ev.event_id)


def test_create_a_weekly_series_in_manila_and_see_every_occurrence(svc, work_id):
    out = apply(
        svc,
        svc.preview_create(
            work_id,
            title="Standup",
            start="2026-11-03T09:00:00",
            end="2026-11-03T09:30:00",
            timezone="Asia/Manila",
            recurrence={"frequency": "weekly", "by_weekday": ["TU"], "count": 4},
        ),
    )
    assert out.event.is_recurring and out.event.recurrence["frequency"] == "weekly" and out.event.recurrence["count"] == 4
    found = svc.search_events("2026-11-01", "2026-12-15", query="standup")
    assert [e.start_date or e.start[:10] for e in found.events] == ["2026-11-03", "2026-11-10", "2026-11-17", "2026-11-24"]
    assert found.events[0].display == {
        "Asia/Tokyo": "Tue 3 Nov 2026 10:00-10:30 JST",
        "Asia/Manila": "Tue 3 Nov 2026 09:00-09:30 PHT",
    }


def test_a_repeat_rule_with_an_end_date_includes_the_last_day(svc, work_id):
    apply(
        svc,
        svc.preview_create(
            work_id, title="Until", start="2026-11-03T09:00:00", end="2026-11-03T10:00:00", timezone="Asia/Tokyo",
            recurrence={"frequency": "weekly", "by_weekday": ["TU"], "until": "2026-11-17"},
        ),
    )
    days = [e.start[:10] for e in svc.search_events("2026-11-01", "2026-12-31", query="until").events]
    assert days == ["2026-11-03", "2026-11-10", "2026-11-17"]


def test_a_created_event_is_found_by_search_and_is_in_the_right_calendar(svc, work_id):
    apply(svc, svc.preview_create(work_id, **TIMED))
    hit = svc.search_events("2026-11-02", "2026-11-02", query="dentist").events[0]
    assert hit.calendar_name == "Work"


# ---- update -------------------------------------------------------------------------------------


def test_update_a_single_event(svc):
    out = apply(svc, svc.preview_update(f"{_cid(svc)}~sync-1", {"title": "Client sync (v2)", "start": "2026-10-22T13:00:00"}))
    assert out.event.untrusted.title == "Client sync (v2)"
    assert out.event.start == "2026-10-22T13:00:00+08:00" and out.event.end == "2026-10-22T14:00:00+08:00"


def _cid(svc):
    return next(c.calendar_id for c in svc.backend.list_calendars() if c.calendar_name == "Work")


def _make_series(svc, work_id, title="Series", count=4):
    ev = apply(
        svc,
        svc.preview_create(
            work_id, title=title, start="2026-11-03T15:00:00", end="2026-11-03T16:00:00", timezone="Asia/Manila",
            recurrence={"frequency": "weekly", "by_weekday": ["TU"], "count": count},
        ),
    ).event
    return ev.event_id


def test_move_one_occurrence_of_a_series(svc, work_id):
    sid = _make_series(svc, work_id)
    occ = "2026-11-10T15:00:00+08:00"
    out = apply(svc, svc.preview_update(sid, {"start": "2026-11-10T17:00:00"}, occ, "this_occurrence"))
    assert out.event.start == "2026-11-10T17:00:00+08:00" and out.event.end == "2026-11-10T18:00:00+08:00"
    hours = [e.start[11:16] for e in svc.search_events("2026-11-01", "2026-12-15", query="series").events]
    assert hours == ["15:00", "17:00", "15:00", "15:00"]  # only the second one moved
    assert "RECURRENCE-ID" in raw(svc, sid)


def test_editing_the_same_occurrence_twice_edits_the_override_not_a_new_one(svc, work_id):
    sid = _make_series(svc, work_id)
    occ = "2026-11-10T15:00:00+08:00"
    apply(svc, svc.preview_update(sid, {"title": "Once"}, occ, "this_occurrence"))
    apply(svc, svc.preview_update(sid, {"title": "Twice", "location": "Room 9"}, occ, "this_occurrence"))
    assert raw(svc, sid).count("RECURRENCE-ID") == 1
    hit = next(e for e in svc.search_events("2026-11-01", "2026-12-15").events if e.event_id == sid and e.occurrence_start.startswith("2026-11-10"))
    assert hit.untrusted.title == "Twice" and hit.untrusted.location == "Room 9"


def test_rename_the_whole_series(svc, work_id):
    sid = _make_series(svc, work_id)
    apply(svc, svc.preview_update(sid, {"title": "Renamed series", "alarms_minutes": [15]}, scope="whole_series"))
    names = {e.untrusted.title for e in svc.search_events("2026-11-01", "2026-12-15").events if e.event_id == sid}
    assert names == {"Renamed series"}
    assert svc.get_event(sid).alarms_minutes == [15]


def test_shift_the_whole_series_to_a_later_time(svc, work_id):
    sid = _make_series(svc, work_id)
    apply(svc, svc.preview_update(sid, {"start": "2026-11-03T18:00:00"}, scope="whole_series"))
    hours = {e.start[11:16] for e in svc.search_events("2026-11-01", "2026-12-15").events if e.event_id == sid}
    assert hours == {"18:00"}


def test_change_the_repeat_rule_of_the_whole_series(svc, work_id):
    sid = _make_series(svc, work_id, count=2)
    apply(svc, svc.preview_update(sid, {"recurrence": {"frequency": "weekly", "by_weekday": ["TU"], "count": 4}}, scope="whole_series"))
    assert len([e for e in svc.search_events("2026-11-01", "2026-12-15").events if e.event_id == sid]) == 4
    apply(svc, svc.preview_update(sid, {"recurrence": None}, scope="whole_series"))
    assert len([e for e in svc.search_events("2026-11-01", "2026-12-15").events if e.event_id == sid]) == 1


def test_an_existing_moved_occurrence_can_be_edited_again(svc):
    sid = f"{_cid(svc)}~weekly-series"
    occ = "2026-10-27T15:00:00+08:00"  # the occurrence that was moved to 17:00 by an existing override
    out = apply(svc, svc.preview_update(sid, {"title": "Moved and renamed"}, occ, "this_occurrence"))
    assert out.event.untrusted.title == "Moved and renamed" and out.event.start == "2026-10-27T17:00:00+08:00"
    assert raw(svc, sid).count("RECURRENCE-ID") == 1


def test_a_series_with_a_deleted_and_a_moved_occurrence_refuses_a_time_change(svc):
    sid = f"{_cid(svc)}~weekly-series"
    with pytest.raises(CalendarError) as err:
        svc.preview_update(sid, {"start": "2026-10-20T18:00:00"}, scope="whole_series")
    assert err.value.code == "INVALID_INPUT" and "detach" in err.value.message
    out = svc.preview_update(sid, {"title": "Weekly review v2"}, scope="whole_series")
    assert out.preview["warnings"]


# ---- delete -------------------------------------------------------------------------------------


def test_delete_one_event(svc, work_id):
    eid = apply(svc, svc.preview_create(work_id, **TIMED)).event.event_id
    out = apply(svc, svc.preview_delete(eid))
    assert out.message == "The event was deleted."
    with pytest.raises(CalendarError) as err:
        svc.get_event(eid)
    assert err.value.code == "NOT_FOUND"


def test_delete_one_occurrence_leaves_the_rest_of_the_series(svc, work_id):
    sid = _make_series(svc, work_id)
    out = apply(svc, svc.preview_delete(sid, "2026-11-10T15:00:00+08:00", "this_occurrence"))
    assert out.message == "That occurrence was deleted."
    days = [e.start[:10] for e in svc.search_events("2026-11-01", "2026-12-15", query="series").events]
    assert days == ["2026-11-03", "2026-11-17", "2026-11-24"]
    assert "EXDATE" in raw(svc, sid)


def test_delete_a_moved_occurrence_removes_the_override_and_the_original_slot(svc):
    sid = f"{_cid(svc)}~weekly-series"
    apply(svc, svc.preview_delete(sid, "2026-10-27T15:00:00+08:00", "this_occurrence"))
    days = [e.start[:10] for e in svc.search_events("2026-10-19", "2026-11-15", query="weekly").events]
    assert days == ["2026-10-20", "2026-11-10"]  # 27 Oct (moved) gone, 3 Nov was already deleted
    assert raw(svc, sid).count("RECURRENCE-ID") == 0


def test_delete_the_whole_series(svc, work_id):
    sid = _make_series(svc, work_id)
    out = apply(svc, svc.preview_delete(sid, scope="whole_series"))
    assert out.message == "The whole series was deleted."
    assert not [e for e in svc.search_events("2026-11-01", "2026-12-15").events if e.event_id == sid]


# ---- safety -------------------------------------------------------------------------------------


def test_other_calendars_stay_untouched_and_unwritable(svc, home_id):
    with pytest.raises(CalendarError) as err:
        svc.preview_create(home_id, **TIMED)
    assert err.value.code == "CALENDAR_NOT_WRITABLE"
    assert svc.search_events("2026-11-02", "2026-11-02", query="dentist").events == []


def test_a_change_made_elsewhere_after_the_preview_is_a_conflict(svc, parts, work_id):
    eid = apply(svc, svc.preview_create(work_id, **TIMED)).event.event_id
    token = svc.preview_update(eid, {"title": "Mine"}).confirm_token
    _cal_id, uid = eid.split("~", 1)
    cal, _ = svc.backend._calendar(_cal_id)
    obj = cal.event_by_uid(uid)
    obj.icalendar_instance.walk("VEVENT")[0]["SUMMARY"] = "Theirs"
    obj.save()
    with pytest.raises(CalendarError) as err:
        svc.confirm_change(token)
    assert err.value.code == "CONFLICT"
    assert svc.get_event(eid).untrusted.title == "Theirs"


def test_an_unknown_event_cannot_be_updated_or_deleted(svc, work_id):
    for call in (lambda: svc.preview_update(f"{work_id}~nope", {"title": "x"}), lambda: svc.preview_delete(f"{work_id}~nope")):
        with pytest.raises(CalendarError) as err:
            call()
        assert err.value.code == "NOT_FOUND"
