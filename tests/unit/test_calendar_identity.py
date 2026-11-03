"""Write allow-lists by stable calendar ID (names still work), and honest source-permission reporting."""

import pytest

from icloud_mcp_server.backends.fake import FakeBackend
from icloud_mcp_server.config import Settings
from icloud_mcp_server.core.errors import CalendarError
from icloud_mcp_server.core.models import CalendarInfo
from icloud_mcp_server.core.service import CalendarService

A = CalendarInfo("cal-a", "Work")
B = CalendarInfo("cal-b", "Work")  # same name as A
C = CalendarInfo("cal-c", "Home", read_only_in_source=True)
D = CalendarInfo("cal-d", "Shared", read_only_in_source=None)  # the server did not say


def svc(settings, *cals):
    return CalendarService(FakeBackend(calendars=list(cals), events=[]), settings)


def listed(service):
    return {c.calendar_id: c for c in service.list_calendars()}


def test_ids_win_over_names_and_distinguish_same_named_calendars():
    s = svc(Settings(allow_writes=True, writable_calendars=frozenset({"Work"}), writable_calendar_ids=frozenset({"cal-b"})), A, B)
    out = listed(s)
    assert (out["cal-a"].writable_by_server, out["cal-b"].writable_by_server) == (False, True)


def test_a_name_shared_by_two_calendars_is_not_writable_by_name():
    s = svc(Settings(allow_writes=True, writable_calendars=frozenset({"Work"})), A, B)
    out = listed(s)
    assert not out["cal-a"].writable_by_server and "share this name" in out["cal-a"].write_blocked_reason
    with pytest.raises(CalendarError) as err:
        s.preview_create("cal-a", title="x", start="2026-11-02T15:00:00", end="2026-11-02T16:00:00")
    assert err.value.code == "CALENDAR_NOT_WRITABLE"


def test_names_keep_working_when_unique():
    s = svc(Settings(allow_writes=True, writable_calendars=frozenset({"Home", "Shared"})), A, C, D)
    assert listed(s)["cal-d"].writable_by_server is True


def test_source_read_only_blocks_even_when_listed_and_unknown_is_reported_as_null():
    s = svc(Settings(allow_writes=True, writable_calendar_ids=frozenset({"cal-c", "cal-d"})), C, D)
    out = listed(s)
    assert out["cal-c"].writable_by_server is False and out["cal-c"].read_only_in_source is True
    assert out["cal-d"].writable_by_server is True and out["cal-d"].read_only_in_source is None


def test_settings_read_ids_from_the_environment():
    st = Settings.from_env({"ALLOW_WRITES": "true", "WRITABLE_CALENDAR_IDS": "cal-1, cal-2"})
    assert st.writable_calendar_ids == frozenset({"cal-1", "cal-2"})
