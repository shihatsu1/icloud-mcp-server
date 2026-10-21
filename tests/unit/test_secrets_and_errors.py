import pytest
from caldav.lib import error as dav_error

from icloud_mcp_server.backends.caldav_backend import _classify, split_event_id
from icloud_mcp_server.core.errors import CalendarError
from icloud_mcp_server.secrets import Credentials, MissingCredentials, load_credentials

# Synthetic, not a real credential. Built at runtime so the source never contains a string shaped
# like an Apple app-specific password (the repo's secret scan would rightly flag it).
PASSWORD = "-".join(["abcd", "efgh", "ijkl", "mnop"])  # noqa: FLY002 - deliberately not a literal


def test_credentials_repr_and_str_never_show_the_password():
    creds = Credentials("me@example.com", PASSWORD)
    assert PASSWORD not in repr(creds) and PASSWORD not in str(creds)


def test_load_from_environment():
    creds = load_credentials({"ICLOUD_APPLE_ID": "me@example.com", "ICLOUD_APP_PASSWORD": PASSWORD})
    assert creds.apple_id == "me@example.com" and creds.password == PASSWORD


def test_missing_apple_id_or_password_is_a_clear_error_without_secrets(monkeypatch):
    monkeypatch.setattr("icloud_mcp_server.secrets._from_keychain", lambda _a: None)
    with pytest.raises(MissingCredentials):
        load_credentials({})
    with pytest.raises(MissingCredentials) as info:
        load_credentials({"ICLOUD_APPLE_ID": "me@example.com"})
    assert "security add-generic-password" in str(info.value)


def test_keychain_is_used_when_env_password_is_absent(monkeypatch):
    monkeypatch.setattr("icloud_mcp_server.secrets._from_keychain", lambda _a: PASSWORD)
    assert load_credentials({"ICLOUD_APPLE_ID": "me@example.com"}).password == PASSWORD


def test_authorization_error_maps_to_credentials_rejected_without_details():
    err = _classify(dav_error.AuthorizationError(f"401 for user me@example.com password {PASSWORD}"))
    assert err.code == "CREDENTIALS_REJECTED"
    assert PASSWORD not in err.render() and "me@example.com" not in err.render()


def test_unknown_library_error_maps_to_unreachable_without_details():
    err = _classify(RuntimeError("https://user:" + PASSWORD + "@caldav.icloud.com failed"))
    assert err.code == "ICLOUD_UNREACHABLE" and PASSWORD not in err.render()


def test_domain_errors_pass_through_unchanged():
    original = CalendarError("NOT_FOUND", "x")
    assert _classify(original) is original


def test_event_id_must_have_calendar_and_uid():
    assert split_event_id("cal-1~uid-2") == ("cal-1", "uid-2")
    with pytest.raises(CalendarError):
        split_event_id("no-separator")
