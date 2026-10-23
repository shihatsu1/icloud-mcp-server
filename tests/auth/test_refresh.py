"""A refused upstream refresh must tell the client to sign in again, not crash with a 500."""

import pytest
from authlib.integrations.base_client.errors import OAuthError
from fastmcp.server.auth.oauth_proxy.proxy import OAuthProxy
from mcp.server.auth.provider import TokenError

from icloud_mcp_server.auth import build_auth


async def test_github_bad_refresh_token_becomes_invalid_grant(auth_settings, monkeypatch):
    async def refused(self, client, refresh_token, scopes):
        raise OAuthError(error="bad_refresh_token", description="The refresh token passed is incorrect or expired.")

    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", refused)
    with pytest.raises(TokenError) as caught:
        await build_auth(auth_settings).exchange_refresh_token(None, None, [])
    assert caught.value.error == "invalid_grant"


async def test_a_successful_refresh_passes_through(auth_settings, monkeypatch):
    sentinel = object()

    async def fine(self, client, refresh_token, scopes):
        return sentinel

    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", fine)
    assert await build_auth(auth_settings).exchange_refresh_token(None, None, []) is sentinel
