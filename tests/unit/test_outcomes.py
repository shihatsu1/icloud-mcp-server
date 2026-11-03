"""Lost answers, timeouts and repeated confirms: no duplicate writes, and an honest applied / not_applied / unknown."""

from datetime import datetime

import pytest

from icloud_mcp_server.backends.fake import FakeBackend
from icloud_mcp_server.config import Settings
from icloud_mcp_server.core.errors import CalendarError
from icloud_mcp_server.core.service import CalendarService
from tests.conftest import HOME, MANILA, WORK, timed

NEW = {"title": "Dentist", "start": "2026-11-02T15:00:00", "end": "2026-11-02T16:00:00", "timezone": "Asia/Tokyo"}


@pytest.fixture
def backend():
    e = timed("e9", datetime(2026, 10, 22, 10, 0, tzinfo=MANILA), datetime(2026, 10, 22, 11, 0, tzinfo=MANILA),
              "Asia/Manila", title="Client sync", version="1")
    return FakeBackend(calendars=[WORK, HOME], events=[e])


@pytest.fixture
def svc(backend):
    return CalendarService(backend, Settings(allow_writes=True, writable_calendars=frozenset({"Work"})))


class Counting:
    """Wrap a backend write: optionally apply it, then lose the answer. Counts how often it was called."""

    def __init__(self, backend, name, *, apply_first):
        self.calls, self.real = 0, getattr(backend, name)
        self.apply_first = apply_first
        setattr(backend, name, self)

    def __call__(self, *a, **k):
        self.calls += 1
        if self.apply_first:
            self.real(*a, **k)
        raise CalendarError("OUTCOME_UNKNOWN", "answer lost")


def titles(backend):
    return [e.title for e in backend.events]


def test_a_lost_answer_after_a_create_is_settled_by_reading_not_by_writing_again(svc, backend):
    write = Counting(backend, "create_event", apply_first=True)
    preview = svc.preview_create("cal-work", **NEW)
    assert preview.preview["planned_event_id"] == f"cal-work~{preview.preview['planned_event_id'].split('~')[1]}"
    result = svc.confirm_change(preview.confirm_token)
    assert result.result == "applied" and result.operation_id == preview.operation_id
    again = svc.confirm_change(preview.confirm_token)
    assert again.replayed and again.result == "applied"
    assert write.calls == 1 and titles(backend).count("Dentist") == 1
    assert backend.events[-1].event_id == preview.preview["planned_event_id"]  # the UID chosen at preview time


def test_a_lost_answer_for_a_create_that_never_arrived_is_unknown_and_is_not_retried(svc, backend):
    write = Counting(backend, "create_event", apply_first=False)
    preview = svc.preview_create("cal-work", **NEW)
    result = svc.confirm_change(preview.confirm_token)
    assert result.result == "unknown" and "do not retry" in result.message.lower()
    again = svc.confirm_change(preview.confirm_token)
    assert again.replayed and again.result == "unknown"
    assert write.calls == 1 and "Dentist" not in titles(backend)


def test_a_create_whose_readback_fails_after_it_succeeded_is_not_left_untracked(svc, backend, monkeypatch):
    real_get = backend.get_event
    calls = {"n": 0}

    def flaky(event_id, occurrence_start=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise CalendarError("ICLOUD_UNREACHABLE", "blip")
        return real_get(event_id, occurrence_start)

    preview = svc.preview_create("cal-work", **NEW)
    monkeypatch.setattr(backend, "get_event", flaky)
    result = svc.confirm_change(preview.confirm_token)
    assert result.result == "applied"  # reconciled by reading again
    assert titles(backend).count("Dentist") == 1


def test_an_update_whose_answer_was_lost_is_reconciled(svc, backend):
    Counting(backend, "update_event", apply_first=True)
    token = svc.preview_update("e9", {"title": "Moved"}).confirm_token
    assert svc.confirm_change(token).result == "applied"


def test_an_update_that_did_not_arrive_is_not_applied(svc, backend):
    Counting(backend, "update_event", apply_first=False)
    token = svc.preview_update("e9", {"title": "Moved"}).confirm_token
    assert svc.confirm_change(token).result == "not_applied"
    assert backend.get_event("e9").title == "Client sync"


def test_a_delete_whose_answer_was_lost_is_reconciled_both_ways(svc, backend):
    write = Counting(backend, "delete_event", apply_first=True)
    assert svc.confirm_change(svc.preview_delete("e9").confirm_token).result == "applied"
    assert write.calls == 1 and backend.events == []


def test_a_refusal_before_sending_is_not_applied_and_raises(svc, backend):
    token = svc.preview_update("e9", {"title": "Mine"}).confirm_token
    backend.update_event("e9", {"title": "Theirs"}, scope="whole", occurrence_start=None, expected_version=None)
    with pytest.raises(CalendarError) as err:
        svc.confirm_change(token)
    assert err.value.code == "CONFLICT"


def test_two_confirms_of_one_token_never_write_twice(svc, backend):
    token = svc.preview_create("cal-work", **NEW).confirm_token
    first, second = svc.confirm_change(token), svc.confirm_change(token)
    assert (first.replayed, second.replayed) == (False, True)
    assert titles(backend).count("Dentist") == 1


def test_a_token_cannot_be_spent_by_another_principal(svc, backend):
    token = svc.preview_create("cal-work", principal="alice", **NEW).confirm_token
    with pytest.raises(CalendarError) as err:
        svc.confirm_change(token, "mallory")
    assert err.value.code == "TOKEN_INVALID"
    assert "Dentist" not in titles(backend)
    assert svc.confirm_change(token, "alice").result == "applied"


def test_every_preview_gets_its_own_planned_id_so_repeating_a_request_is_visible(svc):
    a = svc.preview_create("cal-work", **NEW)
    b = svc.preview_create("cal-work", **NEW)
    assert a.operation_id != b.operation_id
    assert a.preview["planned_event_id"] != b.preview["planned_event_id"]


def test_a_second_preview_of_a_create_with_unknown_outcome_is_refused(svc, backend):
    Counting(backend, "create_event", apply_first=False)
    svc.confirm_change(svc.preview_create("cal-work", **NEW).confirm_token)
    with pytest.raises(CalendarError) as err:
        svc.preview_create("cal-work", **NEW)
    assert err.value.code == "OUTCOME_UNKNOWN"
    svc.preview_create("cal-work", **{**NEW, "title": "Something else"})  # other events are not blocked
