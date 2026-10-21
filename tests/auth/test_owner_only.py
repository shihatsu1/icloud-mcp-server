"""Only the owner's GitHub account may use the server, at every layer."""

import asyncio
import socket
import threading
import time

import pytest
import uvicorn
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, AuthContext, TokenVerifier
from mcp.shared.exceptions import MCPError

from icloud_mcp_server.auth import OwnerOnlyGitHubProvider, OwnerOnlyVerifier, owner_check
from icloud_mcp_server.backends.fake import FakeBackend
from icloud_mcp_server.config import Settings
from icloud_mcp_server.core.models import CalendarInfo
from icloud_mcp_server.core.service import CalendarService
from icloud_mcp_server.server import build_server
from tests.auth.conftest import OWNER

OTHER = "999999"


def token(sub: str) -> AccessToken:
    return AccessToken(token="t", client_id=sub, scopes=["read:user"], claims={"sub": sub, "login": "someone"})


class StubVerifier(TokenVerifier):
    """Maps bearer strings to tokens, standing in for GitHub in tests."""

    def __init__(self, known: dict[str, AccessToken]):
        super().__init__(required_scopes=["read:user"])
        self.known = known

    async def verify_token(self, tok: str):
        return self.known.get(tok)


# ---- unit level -------------------------------------------------------------------------------


async def test_owner_verifier_accepts_owner_and_rejects_everyone_else():
    inner = StubVerifier({"owner-tok": token(OWNER), "other-tok": token(OTHER)})
    verifier = OwnerOnlyVerifier(inner, OWNER)
    assert (await verifier.verify_token("owner-tok")).claims["sub"] == OWNER
    assert await verifier.verify_token("other-tok") is None
    assert await verifier.verify_token("unknown") is None


def test_owner_check_function():
    check = owner_check(OWNER)
    component = object()
    assert check(AuthContext(token=token(OWNER), component=component)) is True
    assert check(AuthContext(token=token(OTHER), component=component)) is False
    assert check(AuthContext(token=None, component=component)) is False
    assert check(AuthContext(token=AccessToken(token="t", client_id="x", scopes=[]), component=component)) is False


def test_provider_wraps_the_github_verifier_with_the_owner_check(secured_app):
    server, _ = secured_app
    assert isinstance(server.auth, OwnerOnlyGitHubProvider)
    assert isinstance(server.auth._token_validator, OwnerOnlyVerifier)


async def test_without_a_token_no_tools_are_listed(secured_app):
    """Tools carry the owner check, so an unauthenticated caller sees none (also proven over HTTP below)."""
    server, _ = secured_app
    assert await server.list_tools(run_middleware=False) == []


# ---- over real HTTP ---------------------------------------------------------------------------


@pytest.fixture
def live_url():
    started = []

    def make(verifier: TokenVerifier):
        backend = FakeBackend(calendars=[CalendarInfo("c1", "Work")])
        service = CalendarService(backend, Settings())
        mcp = build_server(service, auth=verifier, tool_auth=owner_check(OWNER))
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        config = uvicorn.Config(mcp.http_app(), host="127.0.0.1", port=port, log_level="warning", access_log=False)
        server = uvicorn.Server(config)
        thread = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
        thread.start()
        deadline = time.time() + 15
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started, "test server did not start"
        started.append((server, thread))
        return f"http://127.0.0.1:{port}/mcp"

    yield make
    for server, thread in started:
        server.should_exit = True
        thread.join(timeout=10)


async def test_owner_can_list_and_call_tools_over_http(live_url):
    url = live_url(StubVerifier({"owner-tok": token(OWNER)}))
    async with Client(url, auth="owner-tok") as client:
        tools = await client.list_tools()
        assert len(tools) == 4
        result = await client.call_tool("list_calendars", {})
    assert result.structured_content["calendars"][0]["calendar_name"] == "Work"


async def test_another_account_cannot_use_any_tool_over_http(live_url):
    url = live_url(StubVerifier({"other-tok": token(OTHER)}))
    async with Client(url, auth="other-tok") as client:
        visible = await client.list_tools()
        assert visible == [], "a non-owner must not even see the tools"
        with pytest.raises(ToolError, match="Unknown tool"):
            await client.call_tool("list_calendars", {})


async def test_missing_or_bad_token_is_rejected_over_http(live_url):
    url = live_url(StubVerifier({"owner-tok": token(OWNER)}))
    with pytest.raises(MCPError):
        async with Client(url, auth="wrong-token") as client:
            await client.list_tools()
