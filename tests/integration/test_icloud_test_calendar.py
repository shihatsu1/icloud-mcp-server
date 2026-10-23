"""Opt-in: the write tools against the REAL iCloud calendar named exactly TEST. Never any other calendar.

    export ICLOUD_APPLE_ID=you@icloud.com     # the app-specific password comes from the Keychain
    uv run pytest -m integration tests/integration -q

It refuses to run unless a calendar named exactly TEST exists (create it with scripts/create_test_calendar.py),
only ever writes titles that start with "icmcp-test", and deletes only the events it created itself.
"""

from __future__ import annotations

import os

import pytest

from icloud_mcp_server.backends.caldav_backend import CalDavBackend
from icloud_mcp_server.config import Settings
from icloud_mcp_server.core.errors import CalendarError
from icloud_mcp_server.core.service import CalendarService
from icloud_mcp_server.secrets import MissingCredentials, load_credentials

pytestmark = pytest.mark.integration
PREFIX = "icmcp-test"


@pytest.fixture(scope="module")
def svc():
    if not os.environ.get("ICLOUD_APPLE_ID"):
        pytest.skip("set ICLOUD_APPLE_ID to run against real iCloud")
    try:
        creds = load_credentials()
    except MissingCredentials as exc:
        pytest.skip(str(exc))
    backend = CalDavBackend(creds.apple_id, creds.password)
    names = [c.calendar_name for c in backend.list_calendars()]
    if "TEST" not in names:
        pytest.fail("No calendar named exactly TEST. Run scripts/create_test_calendar.py first. Nothing was written.")
    return CalendarService(backend, Settings(allow_writes=True, writable_calendars=frozenset({"TEST"})))


@pytest.fixture(scope="module")
def test_cal(svc):
    return next(c.calendar_id for c in svc.backend.list_calendars() if c.calendar_name == "TEST")


@pytest.fixture
def ledger(svc):
    """Event ids this test created. Anything left over is deleted, and only these."""
    created: list[str] = []
    yield created
    for event_id in created:
        try:
            token = svc.preview_delete(event_id, scope="whole_series").confirm_token or svc.preview_delete(event_id).confirm_token
            svc.confirm_change(token)
        except CalendarError:
            pass


def make(svc, ledger, test_cal, **fields):
    assert fields["title"].startswith(PREFIX)
    event = svc.confirm_change(svc.preview_create(test_cal, **fields).confirm_token).event
    ledger.append(event.event_id)
    return event


def delete_whole(svc, ledger, event_id):
    out = svc.preview_delete(event_id)
    if out.needs_input:
        out = svc.preview_delete(event_id, scope="whole_series")
    svc.confirm_change(out.confirm_token)
    ledger.remove(event_id)


def test_only_the_test_calendar_is_writable(svc):
    writable = {c.calendar_name for c in svc.list_calendars() if c.writable_by_server}
    assert writable == {"TEST"}


def test_other_calendars_refuse_writes(svc):
    other = next(c.calendar_id for c in svc.backend.list_calendars() if c.calendar_name != "TEST")
    with pytest.raises(CalendarError) as err:
        svc.preview_create(other, title=f"{PREFIX} must not exist", start="2026-12-01T10:00:00", end="2026-12-01T11:00:00")
    assert err.value.code == "CALENDAR_NOT_WRITABLE"


def test_create_update_delete_a_timed_event(svc, ledger, test_cal):
    ev = make(svc, ledger, test_cal, title=f"{PREFIX} dentist", start="2026-12-01T15:00:00", end="2026-12-01T16:00:00",
              timezone="Asia/Tokyo", location="Clinic", alarms_minutes=[10])
    assert ev.start == "2026-12-01T15:00:00+09:00" and ev.alarms_minutes == [10]
    upd = svc.confirm_change(svc.preview_update(ev.event_id, {"title": f"{PREFIX} dentist (moved)", "start": "2026-12-01T17:00:00"}).confirm_token)
    assert upd.event.start == "2026-12-01T17:00:00+09:00" and upd.event.end == "2026-12-01T18:00:00+09:00"
    delete_whole(svc, ledger, ev.event_id)
    with pytest.raises(CalendarError) as err:
        svc.get_event(ev.event_id)
    assert err.value.code == "NOT_FOUND"


def test_all_day_event_keeps_its_dates(svc, ledger, test_cal):
    ev = make(svc, ledger, test_cal, title=f"{PREFIX} retreat", all_day=True, start_date="2026-12-10", end_date="2026-12-13")
    assert (ev.start_date, ev.end_date) == ("2026-12-10", "2026-12-13")
    delete_whole(svc, ledger, ev.event_id)


def test_weekly_series_move_one_occurrence_rename_all_delete_one_then_all(svc, ledger, test_cal):
    ev = make(svc, ledger, test_cal, title=f"{PREFIX} standup", start="2026-12-01T09:00:00", end="2026-12-01T09:30:00",
              timezone="Asia/Manila", recurrence={"frequency": "weekly", "by_weekday": ["TU"], "count": 4})
    sid = ev.event_id
    seen = lambda: [e for e in svc.search_events("2026-11-30", "2027-01-05").events if e.event_id == sid]
    assert len(seen()) == 4

    moved = svc.confirm_change(svc.preview_update(sid, {"start": "2026-12-08T11:00:00"}, "2026-12-08T09:00:00+08:00", "this_occurrence").confirm_token)
    assert moved.event.start == "2026-12-08T11:00:00+08:00"
    assert sorted(e.start[11:16] for e in seen()) == ["09:00", "09:00", "09:00", "11:00"]

    svc.confirm_change(svc.preview_update(sid, {"alarms_minutes": [5]}, scope="whole_series").confirm_token)
    assert svc.get_event(sid).alarms_minutes == [5]

    svc.confirm_change(svc.preview_delete(sid, "2026-12-15T09:00:00+08:00", "this_occurrence").confirm_token)
    assert len(seen()) == 3

    delete_whole(svc, ledger, sid)
    assert seen() == []
