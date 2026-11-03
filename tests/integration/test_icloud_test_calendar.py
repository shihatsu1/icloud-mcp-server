"""Opt-in: the write tools against the REAL iCloud calendar named exactly TEST. Never any other calendar.

    export ICLOUD_APPLE_ID=you@icloud.com     # the app-specific password comes from the Keychain
    uv run pytest -m integration tests/integration -q

It refuses to run unless a calendar named exactly TEST exists (create it with scripts/create_test_calendar.py),
only ever writes titles that start with "icmcp-test", and deletes only the events it created itself.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from icloud_mcp_server.backends.caldav_backend import CalDavBackend
from icloud_mcp_server.config import Settings
from icloud_mcp_server.core.errors import CalendarError
from icloud_mcp_server.core.service import CalendarService
from icloud_mcp_server.secrets import MissingCredentials, load_credentials

from .ledger import Ledger

pytestmark = pytest.mark.integration
PREFIX = "icmcp-test"
LEDGER_PATH = Path(__file__).with_name(".ledger.jsonl")  # gitignored; synthetic titles and event IDs only
OPT_IN = "ICLOUD_TEST_WRITES"  # must be "yes": real-account writes are never implicit


@pytest.fixture(scope="module")
def svc():
    if not os.environ.get("ICLOUD_APPLE_ID"):
        pytest.skip("set ICLOUD_APPLE_ID to run against real iCloud")
    if os.environ.get(OPT_IN) != "yes":
        pytest.skip(f"set {OPT_IN}=yes to allow writes to the calendar named exactly TEST")
    try:
        creds = load_credentials()
    except MissingCredentials as exc:
        pytest.skip(str(exc))
    backend = CalDavBackend(creds.apple_id, creds.password)
    test_ids = [c.calendar_id for c in backend.list_calendars() if c.calendar_name == "TEST"]
    if len(test_ids) != 1:
        pytest.fail(f"Expected exactly one calendar named TEST, found {len(test_ids)}. Nothing was written.")
    return CalendarService(backend, Settings(allow_writes=True, writable_calendar_ids=frozenset(test_ids)))


@pytest.fixture(scope="module")
def test_cal(svc):
    return next(c.calendar_id for c in svc.backend.list_calendars() if c.calendar_name == "TEST")


@pytest.fixture
def ledger(svc, test_cal):
    """Planned event IDs are written to disk before each confirm. Teardown removes them and FAILS if it cannot."""
    book = Ledger(LEDGER_PATH, svc, test_cal, PREFIX)
    book.assert_clean()  # leftovers from an earlier crashed run are cleaned (or reported) first
    yield book
    book.assert_clean()


def make(svc, ledger, test_cal, **fields):
    assert fields["title"].startswith(PREFIX)
    preview = svc.preview_create(test_cal, **fields)
    ledger.plan(preview.preview["planned_event_id"])  # recorded BEFORE the write, so a lost answer is still tracked
    result = svc.confirm_change(preview.confirm_token)
    assert result.result == "applied", result
    return result.event


def delete_whole(svc, ledger, event_id):
    out = svc.preview_delete(event_id)
    if out.needs_input:
        out = svc.preview_delete(event_id, scope="whole_series")
    svc.confirm_change(out.confirm_token)
    ledger.done(event_id)


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
