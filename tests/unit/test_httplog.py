"""The request log must never contain OAuth query values, tokens or event text."""

import logging

import httpx
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from icloud_mcp_server.httplog import RequestTiming


async def anything(request):
    return PlainTextResponse("ok")


def client():
    app = Starlette(routes=[Route("/auth/callback", anything), Route("/mcp", anything, methods=["GET", "POST"]), Route("/other/{x}", anything)])
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=RequestTiming(app)), base_url="https://example.test")


async def test_oauth_query_values_headers_and_odd_paths_never_reach_the_log(caplog):
    caplog.set_level(logging.INFO, logger="icloud_mcp_server.http")
    async with client() as c:
        await c.get("/auth/callback?code=SECRETCODE123&state=SECRETSTATE456")
        await c.post("/mcp", headers={"Authorization": "Bearer SECRETTOKEN789"}, content=b'{"title":"Private dentist"}')
        await c.get("/other/client-id-or-token-xyz?code=abc")
    text = "\n".join(r.getMessage() for r in caplog.records)
    for secret in ("SECRETCODE123", "SECRETSTATE456", "SECRETTOKEN789", "Private dentist", "code=", "state=", "client-id-or-token-xyz", "Bearer"):
        assert secret not in text
    assert "/auth/callback -> 200" in text and "/mcp -> 200" in text and "(other) -> 200" in text
