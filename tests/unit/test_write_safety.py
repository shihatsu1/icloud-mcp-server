"""Regressions for the 2026-11-03 assessment: repeat comparison, preview fidelity, fail-closed versions."""

from datetime import datetime

import pytest

from icloud_mcp_server.backends.fake import FakeBackend
from icloud_mcp_server.config import Settings
from icloud_mcp_server.core.changes import differences
from icloud_mcp_server.core.errors import CalendarError
from icloud_mcp_server.core.service import CalendarService
from tests.conftest import HOME, TOKYO, WORK, timed

START = datetime(2026, 11, 2, 15, 0, tzinfo=TOKYO)


def series(rec, **kw):
    return timed("s", START, START.replace(hour=16), "Asia/Tokyo", is_recurring=True, recurrence=rec, **kw)


def wrong(actual_rec, expected_rec):
    return differences(series(actual_rec), series(expected_rec), ["recurrence"], TOKYO)


def test_repeat_end_dates_that_differ_are_a_difference():
    a = {"frequency": "weekly", "interval": 1, "until": "2026-12-05"}
    b = {"frequency": "weekly", "interval": 1, "until": "2026-12-30"}
    assert wrong(a, b) == ["recurrence"]


def test_a_utc_until_equals_the_same_local_date():
    # 5 Dec 23:59:59 in Tokyo is 14:59:59 UTC the same day
    stored = {"frequency": "weekly", "interval": 1, "until": "2026-12-05T14:59:59+00:00"}
    wanted = {"frequency": "weekly", "interval": 1, "until": "2026-12-05"}
    assert wrong(stored, wanted) == []
    other_day = {"frequency": "weekly", "interval": 1, "until": "2026-12-06"}
    assert wrong(stored, other_day) == ["recurrence"]


def test_an_all_day_date_until_matches():
    stored = {"frequency": "daily", "interval": 1, "until": "2026-12-05"}
    assert wrong(stored, {"frequency": "daily", "until": "2026-12-05"}) == []


def test_unsupported_rule_parts_are_a_difference():
    stored = {"frequency": "weekly", "interval": 1, "other_parts": ["BYSETPOS"]}
    assert wrong(stored, {"frequency": "weekly", "interval": 1}) == ["recurrence"]


# ---- service level --------------------------------------------------------------------------------


@pytest.fixture
def backend():
    e = timed("e9", START, START.replace(hour=16), "Asia/Tokyo", title="Client sync", version="1")
    unversioned = timed("nov", START, START.replace(hour=16), "Asia/Tokyo", title="No tag", version=None)
    return FakeBackend(calendars=[WORK, HOME], events=[e, unversioned])


@pytest.fixture
def svc(backend):
    return CalendarService(backend, Settings(allow_writes=True, writable_calendars=frozenset({"Work"})))


def shown_notes(preview):
    fields = preview.preview["fields"]
    return fields.get("untrusted", fields)["notes"]


@pytest.mark.parametrize("text", ["x" * 4500, "a" * 3990 + "\x01\x02bcd", "line\x07bell"])
def test_what_the_preview_shows_is_what_gets_written(svc, backend, text):
    new = {"title": "Dentist", "start": "2026-11-02T15:00:00", "end": "2026-11-02T16:00:00", "timezone": "Asia/Tokyo"}
    try:
        preview = svc.preview_create("cal-work", **new, notes=text)
    except CalendarError as exc:  # refusing over-long input is an acceptable way to be faithful
        assert exc.code == "INVALID_INPUT"
        return
    svc.confirm_change(preview.confirm_token)
    written = backend.events[-1].notes
    assert shown_notes(preview) == written


def test_update_preview_shows_every_written_note_character(svc, backend):
    try:
        preview = svc.preview_update("e9", {"notes": "y" * 4500})
    except CalendarError as exc:
        assert exc.code == "INVALID_INPUT"
        return
    svc.confirm_change(preview.confirm_token)
    assert preview.preview["after"]["notes"] == backend.events[0].notes


def test_writes_are_refused_when_the_event_has_no_version(svc):
    with pytest.raises(CalendarError) as err:
        svc.preview_delete("nov")
    assert err.value.code == "VERSION_UNAVAILABLE"
    with pytest.raises(CalendarError) as err:
        svc.preview_update("nov", {"title": "x"})
    assert err.value.code == "VERSION_UNAVAILABLE"
