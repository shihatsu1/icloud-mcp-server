"""Exercise the MCP layer through an in-process client."""

import json

import pytest
from fastmcp import Client

from icloud_mcp_server.server import build_server

EXPECTED = {"list_calendars", "search_events", "get_event", "find_free_time"}


@pytest.fixture
def mcp(service):
    return build_server(service)


async def test_exactly_the_four_read_tools_are_exposed(mcp):
    async with Client(mcp) as client:
        tools = await client.list_tools()
    assert {t.name for t in tools} == EXPECTED


async def test_every_tool_is_annotated_read_only_with_a_title(mcp):
    async with Client(mcp) as client:
        tools = await client.list_tools()
    for tool in tools:
        ann = tool.annotations
        assert ann is not None, tool.name
        assert ann.read_only_hint is True and ann.destructive_hint is False, tool.name
        wire = tool.model_dump(by_alias=True, exclude_none=True)["annotations"]
        assert wire["readOnlyHint"] is True and wire["destructiveHint"] is False, tool.name
        assert ann.title, tool.name


async def test_search_through_the_protocol(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("search_events", {"start": "2026-10-22", "end": "2026-10-22"})
    data = result.data if isinstance(result.data, dict) else json.loads(result.content[0].text)
    assert data["events"][0]["event_id"] == "e9"


async def test_domain_errors_reach_the_client_with_code_and_next_step(mcp):
    async with Client(mcp) as client:
        with pytest.raises(Exception) as info:
            await client.call_tool("search_events", {"start": "2026-10-22", "end": "2026-10-01"})
    text = str(info.value)
    assert "INVALID_RANGE" in text and "Next step" in text


async def test_unexpected_errors_are_masked(mcp, service):
    def boom(*_a, **_k):
        raise RuntimeError("secret-internal-detail https://user:pw@host")

    service.backend.list_calendars = boom
    async with Client(mcp) as client:
        with pytest.raises(Exception) as info:
            await client.call_tool("list_calendars", {})
    assert "secret-internal-detail" not in str(info.value)
