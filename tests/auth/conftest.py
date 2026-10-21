import pytest

from icloud_mcp_server.auth import AuthSettings, build_auth, owner_check
from icloud_mcp_server.backends.fake import FakeBackend
from icloud_mcp_server.config import Settings
from icloud_mcp_server.core.service import CalendarService
from icloud_mcp_server.server import build_server

BASE = "https://mcp.example.test"
OWNER = "424242"
KEY = "k" * 48  # synthetic


@pytest.fixture
def auth_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMCP_HOME", str(tmp_path))  # keep OAuth state out of the user's home
    return AuthSettings(BASE, "fake-client-id", "fake-client-secret", OWNER, KEY)


@pytest.fixture
def secured_app(auth_settings):
    service = CalendarService(FakeBackend(), Settings())
    server = build_server(service, auth=build_auth(auth_settings), tool_auth=owner_check(OWNER))
    return server, server.http_app()
