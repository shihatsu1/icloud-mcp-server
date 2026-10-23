"""The preview, then confirm write protocol (docs/TOOLS.md sections 5-8) against the fake backend.

Recurrence semantics on a real CalDAV server are covered in tests/contract; these tests pin the
protocol: gating, scope questions, tokens, conflicts and the re-read check.
"""

from datetime import date, datetime

import pytest

from icloud_mcp_server.backends.fake import FakeBackend
from icloud_mcp_server.config import Settings
from icloud_mcp_server.core.errors import CalendarError
from icloud_mcp_server.core.service import CalendarService
from tests.conftest import HOME, MANILA, TOKYO, WORK, all_day, timed

SERIES_2026 = [datetime(2026, 10, 20, 15, 0, tzinfo=MANILA), datetime(2026, 10, 27, 15, 0, tzinfo=MANILA)]


def weekly(event_id, starts, exceptions=0):
    return [
        timed(
            event_id,
            s,
            s.replace(hour=16),
            "Asia/Manila",
            title="Weekly review",
            series_id=event_id,
            is_recurring=True,
            occurrence_start=s,
            recurrence={"frequency": "weekly", "interval": 1, "by_weekday": ["TU"]},
            exceptions_count=exceptions,
            version="1",
        )
        for s in starts
    ]


@pytest.fixture
def backend():
    events = [
        timed(
            "e9",
            datetime(2026, 10, 22, 10, 0, tzinfo=MANILA),
            datetime(2026, 10, 22, 11, 0, tzinfo=MANILA),
            "Asia/Manila",
            title="Client sync",
            location="Room 1",
            version="1",
        ),
        all_day("trip", date(2026, 10, 24), date(2026, 10, 27), title="Trip", cal=HOME, version="1"),
        all_day("holiday", date(2026, 11, 3), date(2026, 11, 4), title="Culture Day", cal=WORK, version="1"),
        *weekly("wk", SERIES_2026),
        *weekly("wk-edited", SERIES_2026, exceptions=2),
    ]
    return FakeBackend(calendars=[WORK, HOME], events=events)


@pytest.fixture
def svc(backend):
    return CalendarService(backend, Settings(allow_writes=True, writable_calendars=frozenset({"Work"})))


def code(call, *args, **kwargs):
    with pytest.raises(CalendarError) as err:
        call(*args, **kwargs)
    return err.value.code


NEW = {"title": "Dentist", "start": "2026-11-02T15:00:00", "end": "2026-11-02T16:00:00", "timezone": "Asia/Tokyo"}


# ---- gating -----------------------------------------------------------------------------------


def test_everything_is_refused_when_writes_are_off(backend):
    off = CalendarService(backend, Settings())
    assert code(off.preview_create, "cal-work", **NEW) == "WRITES_DISABLED"
    assert code(off.preview_update, "e9", {"title": "x"}) == "WRITES_DISABLED"
    assert code(off.preview_delete, "e9") == "WRITES_DISABLED"
    assert code(off.confirm_change, "anything") == "WRITES_DISABLED"


def test_only_calendars_on_the_allow_list_are_writable(svc):
    assert code(svc.preview_create, "cal-home", **NEW) == "CALENDAR_NOT_WRITABLE"
    assert code(svc.preview_update, "trip", {"title": "x"}) == "CALENDAR_NOT_WRITABLE"
    assert code(svc.preview_delete, "trip") == "CALENDAR_NOT_WRITABLE"
    assert code(svc.preview_create, "cal-nope", **NEW) == "NOT_FOUND"
    listed = {c.calendar_name: c.writable_by_server for c in svc.list_calendars()}
    assert listed == {"Work": True, "Home": False}


def test_the_allow_list_is_checked_again_at_confirm_time(svc):
    token = svc.preview_create("cal-work", **NEW).confirm_token
    svc.settings = Settings(allow_writes=True, writable_calendars=frozenset())
    assert code(svc.confirm_change, token) == "CALENDAR_NOT_WRITABLE"


# ---- create -----------------------------------------------------------------------------------


def test_create_preview_writes_nothing_and_shows_both_zones(svc, backend):
    before = len(backend.events)
    out = svc.preview_create("cal-work", **NEW, location="Clinic", alarms_minutes=[10, 60])
    assert len(backend.events) == before  # a preview never writes
    p = out.preview
    assert p["action"] == "create" and p["calendar"] == "Work"
    assert p["fields"]["title"] == "Dentist"
    assert p["fields"]["start"] == "2026-11-02T15:00:00+09:00"
    assert p["fields"]["timezone"] == "Asia/Tokyo"
    assert p["fields"]["alarms_minutes"] == [10, 60]
    assert p["fields"]["display"] == {
        "Asia/Tokyo": "Mon 2 Nov 2026 15:00-16:00 JST",
        "Asia/Manila": "Mon 2 Nov 2026 14:00-15:00 PHT",
    }
    assert out.confirm_token and out.expires_at and "Nothing has been written yet" in out.notice


def test_confirm_creates_the_event_rereads_it_and_the_token_works_once(svc, backend):
    token = svc.preview_create("cal-work", **NEW).confirm_token
    result = svc.confirm_change(token)
    assert result.result == "applied" and result.action == "create"
    assert result.event.untrusted.title == "Dentist"
    found = svc.search_events("2026-11-02", "2026-11-02", query="dentist")
    assert [e.event_id for e in found.events] == [result.event.event_id]
    assert code(svc.confirm_change, token) == "TOKEN_INVALID"
    assert sum(1 for e in backend.events if e.title == "Dentist") == 1  # not created twice


def test_create_all_day_event_keeps_dates_and_has_no_zone(svc):
    out = svc.preview_create("cal-work", title="Retreat", all_day=True, start_date="2026-12-01", end_date="2026-12-04")
    f = out.preview["fields"]
    assert (f["start_date"], f["end_date"], f["timezone"], f["start"]) == ("2026-12-01", "2026-12-04", None, None)
    one_day = svc.preview_create("cal-work", title="Retreat", all_day=True, start_date="2026-12-01")
    assert one_day.preview["fields"]["end_date"] == "2026-12-02"


def test_create_with_a_weekly_rule_is_summarised(svc):
    out = svc.preview_create(
        "cal-work", **NEW, recurrence={"frequency": "weekly", "by_weekday": ["MO", "TH"], "count": 6}
    )
    assert out.preview["recurrence_summary"] == "every week on MO, TH, 6 times"


def test_aware_start_is_converted_to_the_requested_zone(svc):
    out = svc.preview_create(
        "cal-work", title="Call", start="2026-11-02T14:00:00+08:00", end="2026-11-02T15:00:00+08:00", timezone="Asia/Tokyo"
    )
    assert out.preview["fields"]["start"] == "2026-11-02T15:00:00+09:00"


@pytest.mark.parametrize(
    "fields",
    [
        {"title": "", "start": "2026-11-02T15:00:00", "end": "2026-11-02T16:00:00"},
        {"title": "x", "start": "2026-11-02T15:00:00"},  # no end
        {"title": "x", "start": "2026-11-02T16:00:00", "end": "2026-11-02T15:00:00"},
        {"title": "x", "start": "2026-11-02", "end": "2026-11-03"},  # date without time on a timed event
        {"title": "x", "start": "not a time", "end": "2026-11-02T16:00:00"},
        {"title": "x", "start": "2026-11-02T15:00:00", "end": "2026-11-02T16:00:00", "timezone": "Mars/Base"},
        {"title": "x", "all_day": True},  # no start_date
        {"title": "x", "all_day": True, "start_date": "2026-11-02", "start": "2026-11-02T15:00:00"},
        {"title": "x", "start_date": "2026-11-02"},  # all-day dates without all_day
        {"title": "x", "all_day": True, "start_date": "2026-11-05", "end_date": "2026-11-05"},
        {"title": "x", "start": "2026-11-02T15:00:00", "end": "2026-11-02T16:00:00", "alarms_minutes": [-5]},
        {"title": "x", "start": "2026-11-02T15:00:00", "end": "2026-11-02T16:00:00", "alarms_minutes": [1, 2, 3, 4, 5, 6]},
        {"title": "x", "start": "2026-11-02T15:00:00", "end": "2026-11-02T16:00:00", "recurrence": {"frequency": "hourly"}},
        {"title": "x", "start": "2026-11-02T15:00:00", "end": "2026-11-02T16:00:00", "recurrence": {"frequency": "daily", "count": 3, "until": "2026-12-01"}},
        {"title": "x", "start": "2026-11-02T15:00:00", "end": "2026-11-02T16:00:00", "recurrence": {"frequency": "weekly", "by_weekday": ["XX"]}},
        {"title": "x" * 501, "start": "2026-11-02T15:00:00", "end": "2026-11-02T16:00:00"},
    ],
)
def test_bad_create_input_gets_no_token(svc, fields):
    fields = {k: v for k, v in fields.items() if k != "title"} | {"title": fields["title"]}
    with pytest.raises(CalendarError) as err:
        svc.preview_create("cal-work", **fields)
    assert err.value.code in {"INVALID_INPUT", "INVALID_TIMEZONE"}


# ---- update -----------------------------------------------------------------------------------


def test_update_preview_shows_before_after_and_changed_fields(svc, backend):
    out = svc.preview_update("e9", {"title": "Client sync (moved)", "start": "2026-10-22T14:00:00"})
    p = out.preview
    assert p["action"] == "update" and p["scope"] is None
    assert p["before"]["title"] == "Client sync" and p["after"]["title"] == "Client sync (moved)"
    assert p["before"]["start"] == "2026-10-22T10:00:00+08:00"
    assert p["after"]["start"] == "2026-10-22T14:00:00+08:00"
    assert p["after"]["end"] == "2026-10-22T15:00:00+08:00"  # the one-hour length is kept
    assert p["changed_fields"] == ["title", "start", "end"] or p["changed_fields"] == ["title", "start"]
    assert backend.get_event("e9").title == "Client sync"  # still unchanged


def test_confirm_update_applies_and_returns_the_reread_state(svc):
    token = svc.preview_update("e9", {"location": "Room 2", "alarms_minutes": [15]}).confirm_token
    result = svc.confirm_change(token)
    assert result.event.untrusted.location == "Room 2" and result.event.alarms_minutes == [15]
    assert svc.get_event("e9").version == "2"


def test_changing_only_the_time_zone_keeps_the_wall_clock_time(svc):
    p = svc.preview_update("e9", {"timezone": "Asia/Tokyo"}).preview
    assert p["after"]["start"] == "2026-10-22T10:00:00+09:00" and p["after"]["timezone"] == "Asia/Tokyo"


def test_clearing_text_fields_with_an_empty_string(svc):
    token = svc.preview_update("e9", {"location": ""}).confirm_token
    assert svc.confirm_change(token).event.untrusted.location is None


def test_all_day_update_uses_dates(svc):
    out = svc.preview_update("holiday", {"start": "2026-11-04", "end": "2026-11-06"})
    assert (out.preview["after"]["start_date"], out.preview["after"]["end_date"]) == ("2026-11-04", "2026-11-06")
    assert code(svc.preview_update, "holiday", {"timezone": "Asia/Tokyo"}) == "INVALID_INPUT"


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"colour": "red"},
        {"title": ""},
        {"title": "Client sync"},  # unchanged
        {"start": "2026-10-22T12:00:00", "end": "2026-10-22T11:00:00"},
        {"timezone": "Mars/Base"},
        {"alarms_minutes": ["soon"]},
        {"recurrence": {"frequency": "nope"}},
    ],
)
def test_bad_or_empty_updates_get_no_token(svc, changes):
    with pytest.raises(CalendarError) as err:
        svc.preview_update("e9", changes)
    assert err.value.code in {"INVALID_INPUT", "INVALID_TIMEZONE"}


def test_unknown_event_is_not_found(svc):
    assert code(svc.preview_update, "nope", {"title": "x"}) == "NOT_FOUND"
    assert code(svc.preview_delete, "nope") == "NOT_FOUND"


# ---- recurring scope --------------------------------------------------------------------------


def test_recurring_update_without_scope_asks_a_question_and_issues_no_token(svc):
    out = svc.preview_update("wk", {"title": "New name"})
    assert out.needs_input == "AMBIGUOUS_SCOPE" and "Weekly review" in out.question
    assert out.confirm_token is None and out.preview is None
    out = svc.preview_delete("wk")
    assert out.needs_input == "AMBIGUOUS_SCOPE" and out.confirm_token is None


def test_scope_this_occurrence_needs_an_occurrence_start(svc):
    assert code(svc.preview_update, "wk", {"title": "x"}, scope="this_occurrence") == "INVALID_INPUT"
    assert code(svc.preview_update, "wk", {"title": "x"}, scope="sometimes") == "INVALID_INPUT"
    assert code(svc.preview_update, "wk", {"title": "x"}, "2026-10-21T15:00:00+08:00", "this_occurrence") == "NOT_FOUND"


def test_update_this_occurrence_changes_only_that_one(svc):
    occ = "2026-10-27T15:00:00+08:00"
    token = svc.preview_update("wk", {"title": "Weekly review (moved)"}, occ, "this_occurrence").confirm_token
    svc.confirm_change(token)
    titles = {e.occurrence_start: e.title for e in svc.backend.events if e.event_id == "wk"}
    assert titles[SERIES_2026[0]] == "Weekly review" and titles[SERIES_2026[1]] == "Weekly review (moved)"


def test_update_whole_series_changes_every_occurrence_and_shifts_times(svc):
    token = svc.preview_update("wk", {"start": "2026-10-20T17:00:00"}, scope="whole_series").confirm_token
    svc.confirm_change(token)
    starts = sorted(e.start.hour for e in svc.backend.events if e.event_id == "wk")
    assert starts == [17, 17]


def test_repeat_rule_can_only_change_for_the_whole_series(svc):
    rule = {"recurrence": {"frequency": "daily"}}
    assert code(svc.preview_update, "wk", rule, "2026-10-27T15:00:00+08:00", "this_occurrence") == "INVALID_INPUT"
    ok = svc.preview_update("wk", rule, scope="whole_series")
    assert ok.preview["changed_fields"] == ["recurrence"]


def test_a_series_with_edited_occurrences_cannot_have_its_start_or_rule_changed(svc):
    assert code(svc.preview_update, "wk-edited", {"start": "2026-10-20T17:00:00"}, scope="whole_series") == "INVALID_INPUT"
    assert code(svc.preview_update, "wk-edited", {"recurrence": {"frequency": "daily"}}, scope="whole_series") == "INVALID_INPUT"
    ok = svc.preview_update("wk-edited", {"title": "Renamed"}, scope="whole_series")
    assert ok.preview["warnings"] and "individually" in ok.preview["warnings"][0]


# ---- delete -----------------------------------------------------------------------------------


def test_delete_a_single_event(svc):
    out = svc.preview_delete("e9")
    assert out.preview["action"] == "delete" and out.preview["warning"] == "This deletes the event."
    assert out.preview["event"]["title"] == "Client sync"
    result = svc.confirm_change(out.confirm_token)
    assert result.event is None and result.message == "The event was deleted."
    assert code(svc.get_event, "e9") == "NOT_FOUND"


def test_delete_one_occurrence_leaves_the_rest(svc):
    out = svc.preview_delete("wk", "2026-10-20T15:00:00+08:00", "this_occurrence")
    assert "only this one occurrence" in out.preview["warning"]
    svc.confirm_change(out.confirm_token)
    left = [e.occurrence_start for e in svc.backend.events if e.event_id == "wk"]
    assert left == [SERIES_2026[1]]


def test_delete_whole_series_says_so_before_and_after(svc):
    out = svc.preview_delete("wk", scope="whole_series")
    assert "whole series" in out.preview["warning"]
    result = svc.confirm_change(out.confirm_token)
    assert result.message == "The whole series was deleted."
    assert not any(e.event_id == "wk" for e in svc.backend.events)


# ---- safety checks ----------------------------------------------------------------------------


def test_a_change_made_after_the_preview_is_a_conflict_and_nothing_is_written(svc, backend):
    token = svc.preview_update("e9", {"title": "Mine"}).confirm_token
    backend.update_event("e9", {"title": "Theirs"}, scope="whole", occurrence_start=None, expected_version=None)
    assert code(svc.confirm_change, token) == "CONFLICT"
    assert backend.get_event("e9").title == "Theirs"
    assert code(svc.confirm_change, token) == "TOKEN_INVALID"  # the token is spent; run the preview again


def test_a_write_that_does_not_stick_is_reported(svc, backend, monkeypatch):
    monkeypatch.setattr(backend, "update_event", lambda *a, **k: None)  # claims success, changes nothing
    token = svc.preview_update("e9", {"title": "Wanted"}).confirm_token
    with pytest.raises(CalendarError) as err:
        svc.confirm_change(token)
    assert err.value.code == "WRITE_VERIFICATION_FAILED" and "title" in err.value.message


def test_a_delete_that_does_not_stick_is_reported(svc, backend, monkeypatch):
    monkeypatch.setattr(backend, "delete_event", lambda *a, **k: None)
    token = svc.preview_delete("e9").confirm_token
    assert code(svc.confirm_change, token) == "WRITE_VERIFICATION_FAILED"


def test_a_create_that_comes_back_different_is_reported(svc, backend, monkeypatch):
    real = backend.create_event

    def wrong_title(new):
        created = real(new)
        backend.events = [e if e is not created else type(e)(**{**e.__dict__, "title": "Other"}) for e in backend.events]
        return created

    monkeypatch.setattr(backend, "create_event", wrong_title)
    assert code(svc.confirm_change, svc.preview_create("cal-work", **NEW).confirm_token) == "WRITE_VERIFICATION_FAILED"


def test_preview_text_from_the_calendar_is_cleaned(svc, backend):
    backend.events[0] = type(backend.events[0])(**{**backend.events[0].__dict__, "title": "A\x00B" + "z" * 600})
    shown = svc.preview_delete("e9").preview["event"]["title"]
    assert "\x00" not in shown and len(shown) <= 500


def test_tokens_from_different_previews_are_independent(svc):
    a = svc.preview_update("e9", {"title": "One"}).confirm_token
    b = svc.preview_delete("holiday").confirm_token
    assert a != b
    assert svc.confirm_change(b).action == "delete"
    assert svc.confirm_change(a).action == "update"


def test_tokyo_zone_constant_is_available_for_fixtures():
    assert TOKYO.key == "Asia/Tokyo"


def test_the_service_checks_the_version_itself_even_if_the_backend_does_not(svc, backend, monkeypatch):
    real = backend.update_event
    monkeypatch.setattr(backend, "update_event", lambda *a, **k: real(*a, **{**k, "expected_version": None}))
    token = svc.preview_update("e9", {"title": "Mine"}).confirm_token
    real("e9", {"title": "Theirs"}, scope="whole", occurrence_start=None, expected_version=None)
    assert code(svc.confirm_change, token) == "CONFLICT"
    assert backend.get_event("e9").title == "Theirs"
