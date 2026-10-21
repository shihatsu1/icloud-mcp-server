import pytest

from icloud_mcp_server.__main__ import main
from icloud_mcp_server.auth import AuthConfigError, AuthSettings

GOOD = {
    "PUBLIC_BASE_URL": "https://mcp.example.test",
    "GITHUB_CLIENT_ID": "id",
    "GITHUB_CLIENT_SECRET": "super-secret-value",
    "ALLOWED_GITHUB_USER_ID": "424242",
    "JWT_SIGNING_KEY": "k" * 40,
    "ICLOUD_APPLE_ID": "me@example.com",
    "ICLOUD_APP_PASSWORD": "-".join(["abcd", "efgh", "ijkl", "mnop"]),  # noqa: FLY002
}


def test_good_settings_load_and_hide_secrets_in_repr():
    s = AuthSettings.from_env(GOOD)
    assert s.owner_id == "424242"
    text = repr(s)
    assert "super-secret-value" not in text and "k" * 40 not in text


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"PUBLIC_BASE_URL": "http://mcp.example.test"}, "https"),
        ({"ALLOWED_GITHUB_USER_ID": "shihatsu1"}, "numeric"),
        ({"JWT_SIGNING_KEY": "short"}, "32 characters"),
        ({"GITHUB_CLIENT_SECRET": ""}, "GITHUB_CLIENT_SECRET is not set"),
        ({"EXTRA_REDIRECT_URIS": "http://evil.example/cb"}, "https"),
    ],
)
def test_bad_settings_are_rejected_naming_the_setting_not_the_value(change, expected):
    env = {**GOOD, **change}
    with pytest.raises(AuthConfigError) as info:
        AuthSettings.from_env(env)
    assert expected in str(info.value)
    assert "super-secret-value" not in str(info.value)


def test_localhost_http_is_allowed_for_development():
    s = AuthSettings.from_env({**GOOD, "PUBLIC_BASE_URL": "http://localhost:8080"})
    assert s.base_url == "http://localhost:8080"


def run(argv, env):
    return main(argv, env)


def test_server_refuses_to_start_without_auth_settings(capsys):
    assert run([], {"ICLOUD_APPLE_ID": "me@example.com", "ICLOUD_APP_PASSWORD": "x"}) == 2
    err = capsys.readouterr().err
    assert "Authentication is not configured" in err and "GITHUB_CLIENT_ID" in err


def test_dev_no_auth_refuses_when_public_base_url_is_set(capsys):
    assert run(["--dev-no-auth"], {**GOOD}) == 2
    assert "PUBLIC_BASE_URL" in capsys.readouterr().err


def test_dev_no_auth_refuses_non_loopback_host(capsys):
    assert run(["--dev-no-auth", "--host", "0.0.0.0"], {"ICLOUD_APPLE_ID": "a", "ICLOUD_APP_PASSWORD": "b"}) == 2
    assert "127.0.0.1" in capsys.readouterr().err


def test_allow_writes_is_refused_in_this_version(capsys):
    assert run(["--allow-writes"], GOOD) == 2
    assert "not available" in capsys.readouterr().err


def test_missing_icloud_password_is_a_clear_error_without_leaking(capsys, monkeypatch):
    monkeypatch.setattr("icloud_mcp_server.secrets._from_keychain", lambda _a: None)
    env = {k: v for k, v in GOOD.items() if k != "ICLOUD_APP_PASSWORD"}
    assert run([], env) == 2
    err = capsys.readouterr().err
    assert "app-specific password" in err and "super-secret-value" not in err


def test_port_defaults_to_cloud_runs_port_variable():
    from icloud_mcp_server.__main__ import _parser

    assert _parser({"PORT": "9123"}).parse_args([]).port == 9123
    assert _parser({}).parse_args([]).port == 8080
    assert _parser({"PORT": "9123"}).parse_args(["--port", "7000"]).port == 7000


def test_bad_state_backend_stops_the_server_with_a_clear_error(capsys):
    assert run([], {**GOOD, "STATE_BACKEND": "nope"}) == 2
    assert "STATE_BACKEND" in capsys.readouterr().err
