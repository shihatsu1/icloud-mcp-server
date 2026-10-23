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


# ---- write tools: present only when writes are on ------------------------------------------------

WRITE_TOOLS = {"preview_create_event", "preview_update_event", "preview_delete_event", "confirm_change"}


@pytest.fixture
def write_mcp(events):
    from icloud_mcp_server.backends.fake import FakeBackend
    from icloud_mcp_server.config import Settings
    from icloud_mcp_server.core.service import CalendarService
    from tests.conftest import HOME, WORK

    backend = FakeBackend(calendars=[WORK, HOME], events=events)
    return build_server(CalendarService(backend, Settings(allow_writes=True, writable_calendars=frozenset({"Work"}))))


async def test_write_tools_exist_only_when_writes_are_on(mcp, write_mcp):
    async with Client(mcp) as client:
        assert {t.name for t in await client.list_tools()} == EXPECTED
        assert not (WRITE_TOOLS & {t.name for t in await client.list_tools()})
    async with Client(write_mcp) as client:
        assert {t.name for t in await client.list_tools()} == EXPECTED | WRITE_TOOLS


async def test_write_tool_annotations_tell_the_truth(write_mcp):
    async with Client(write_mcp) as client:
        tools = {t.name: t.annotations for t in await client.list_tools()}
    for name in ("preview_create_event", "preview_update_event", "preview_delete_event"):
        assert tools[name].read_only_hint is True and tools[name].destructive_hint is False, name
    assert tools["confirm_change"].read_only_hint is False and tools["confirm_change"].destructive_hint is True
    assert all(a.title for a in tools.values())


def test_instructions_describe_the_two_step_rule_only_when_writes_are_on(mcp, write_mcp):
    assert "Read-only" in (mcp.instructions or "") and "confirm_change" not in (mcp.instructions or "")
    text = write_mcp.instructions or ""
    assert "confirm_change" in text and "writable_by_server" in text and "never" in text.lower()


async def test_preview_then_confirm_through_the_protocol(write_mcp):
    def data(result):
        return result.data if isinstance(result.data, dict) else json.loads(result.content[0].text)

    async with Client(write_mcp) as client:
        preview = data(
            await client.call_tool(
                "preview_create_event",
                {"calendar_id": "cal-work", "title": "Dentist", "start": "2026-11-02T15:00:00", "end": "2026-11-02T16:00:00"},
            )
        )
        assert preview["confirm_token"] and preview["preview"]["action"] == "create"
        applied = data(await client.call_tool("confirm_change", {"confirm_token": preview["confirm_token"]}))
        assert applied["result"] == "applied" and applied["event"]["untrusted"]["title"] == "Dentist"
        with pytest.raises(Exception, match="TOKEN_INVALID"):
            await client.call_tool("confirm_change", {"confirm_token": preview["confirm_token"]})
        with pytest.raises(Exception, match="CALENDAR_NOT_WRITABLE"):
            await client.call_tool(
                "preview_create_event",
                {"calendar_id": "cal-home", "title": "x", "start": "2026-11-02T15:00:00", "end": "2026-11-02T16:00:00"},
            )
