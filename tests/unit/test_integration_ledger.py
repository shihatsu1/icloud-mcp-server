"""The real-account test ledger, tested offline against the fake backend (no network)."""

from datetime import datetime

import pytest

from icloud_mcp_server.backends.fake import FakeBackend
from icloud_mcp_server.config import Settings
from icloud_mcp_server.core.errors import CalendarError
from icloud_mcp_server.core.service import CalendarService
from tests.conftest import HOME, WORK, timed
from tests.integration.ledger import CleanupFailed, Ledger

NEW = {"title": "icmcp-test dentist", "start": "2026-12-01T15:00:00", "end": "2026-12-01T16:00:00", "timezone": "Asia/Tokyo"}


@pytest.fixture
def parts(tmp_path):
    backend = FakeBackend(calendars=[WORK, HOME], events=[])
    svc = CalendarService(backend, Settings(allow_writes=True, writable_calendar_ids=frozenset({"cal-work"})))
    return backend, svc, Ledger(tmp_path / "ledger.jsonl", svc, "cal-work", "icmcp-test")


def test_a_create_followed_by_a_failed_readback_is_still_cleaned_up(parts, monkeypatch):
    backend, svc, ledger = parts
    preview = svc.preview_create("cal-work", **NEW)
    ledger.plan(preview.preview["planned_event_id"])  # before confirm, as the real tests do
    real_get, calls = backend.get_event, {"n": 0}

    def flaky(event_id, occurrence_start=None):
        calls["n"] += 1
        if calls["n"] <= 2:  # the read-back inside confirm fails: the create itself succeeded
            raise CalendarError("ICLOUD_UNREACHABLE", "blip")
        return real_get(event_id, occurrence_start)

    monkeypatch.setattr(backend, "get_event", flaky)
    try:
        svc.confirm_change(preview.confirm_token)
    except CalendarError:
        pass
    assert len(backend.events) == 1  # the event exists even though the answer was lost
    ledger.assert_clean()
    assert backend.events == [] and ledger.outstanding() == []


def test_cleanup_failures_are_reported_not_swallowed(parts, monkeypatch):
    backend, svc, ledger = parts
    preview = svc.preview_create("cal-work", **NEW)
    ledger.plan(preview.preview["planned_event_id"])
    svc.confirm_change(preview.confirm_token)
    monkeypatch.setattr(backend, "delete_event", lambda *a, **k: (_ for _ in ()).throw(CalendarError("ICLOUD_UNREACHABLE", "down")))
    with pytest.raises(CleanupFailed) as err:
        ledger.assert_clean()
    assert preview.preview["planned_event_id"] in str(err.value)
    assert ledger.outstanding() == [preview.preview["planned_event_id"]]  # still tracked for the next run


def test_cleanup_refuses_anything_that_is_not_a_prefixed_event_on_the_test_calendar(parts):
    backend, _svc, ledger = parts
    backend.events.append(timed("real-1", datetime(2026, 12, 1, 9, 0), datetime(2026, 12, 1, 10, 0), "floating", title="Dentist (real)", version="1"))
    ledger.plan("real-1")
    with pytest.raises(CleanupFailed):
        ledger.assert_clean()
    assert [e.title for e in backend.events] == ["Dentist (real)"]  # untouched


def test_a_ledger_entry_for_an_event_that_never_landed_is_closed_quietly(parts):
    _backend, _svc, ledger = parts
    ledger.plan("cal-work~never-created")
    ledger.assert_clean()
    assert ledger.outstanding() == []
