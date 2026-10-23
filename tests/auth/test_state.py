"""OAuth state must survive a restart (Cloud Run recycles instances) and be encrypted at rest."""

import httpx
import pytest
from key_value.aio.stores.memory import MemoryStore

from icloud_mcp_server.auth import AuthConfigError, AuthSettings, build_auth, owner_check
from icloud_mcp_server.backends.fake import FakeBackend
from icloud_mcp_server.config import Settings
from icloud_mcp_server.core.service import CalendarService
from icloud_mcp_server.server import build_server
from icloud_mcp_server.state import build_client_storage
from tests.auth.conftest import BASE, KEY, OWNER

REGISTRATION = {
    "client_name": "Persistent Client 12345",
    "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "none",
}


def app_for(settings: AuthSettings, storage):
    provider = build_auth(settings, storage)
    server = build_server(CalendarService(FakeBackend(), Settings()), auth=provider, tool_auth=owner_check(OWNER))
    return provider, server.http_app()


async def register(app) -> str:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        resp = await client.post("/register", json=REGISTRATION)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["client_id"]


def test_default_backend_uses_fastmcps_file_store():
    assert build_client_storage({}, KEY) is None
    assert build_client_storage({"STATE_BACKEND": "file"}, KEY) is None


def test_unknown_backend_is_rejected():
    with pytest.raises(AuthConfigError):
        build_client_storage({"STATE_BACKEND": "carrier-pigeon"}, KEY)


async def test_firestore_path_stores_only_ciphertext_and_wrong_key_reads_as_missing():
    raw = MemoryStore()
    wrapped = build_client_storage({"STATE_BACKEND": "firestore"}, KEY, lambda _env: raw)
    await wrapped.put("client-1", {"client_name": "Plain Visible Name", "token": "secret-token-value"}, collection="c")

    assert (await wrapped.get("client-1", collection="c"))["client_name"] == "Plain Visible Name"
    stored = await raw.get("client-1", collection="c")
    assert "Plain Visible Name" not in str(stored) and "secret-token-value" not in str(stored)

    wrong = build_client_storage({"STATE_BACKEND": "firestore"}, "x" * 48, lambda _env: raw)
    assert await wrong.get("client-1", collection="c") is None  # a rotated key just means re-register


class SlashRejectingStore(MemoryStore):
    """Firestore reads each '/' in a document name as a path separator and rejects it."""

    async def get(self, key, *, collection=None):
        assert "/" not in key, f"Firestore would reject this document name: {key}"
        return await super().get(key, collection=collection)

    async def put(self, key, value, *, collection=None, ttl=None):
        assert "/" not in key, f"Firestore would reject this document name: {key}"
        return await super().put(key, value, collection=collection, ttl=ttl)

    async def delete(self, key, *, collection=None):
        assert "/" not in key, f"Firestore would reject this document name: {key}"
        return await super().delete(key, collection=collection)


async def test_url_client_ids_work_because_keys_are_hashed_before_firestore():
    # Claude's client ID is a URL; the real deploy failed with "Document name ... lacks a collection id".
    url_id = "https://claude.ai/oauth/mcp-oauth-client-metadata"
    wrapped = build_client_storage({"STATE_BACKEND": "firestore"}, KEY, lambda _env: SlashRejectingStore())
    assert await wrapped.get(url_id, collection="clients") is None
    await wrapped.put(url_id, {"client_name": "Claude"}, collection="clients")
    assert (await wrapped.get(url_id, collection="clients"))["client_name"] == "Claude"
    assert await wrapped.delete(url_id, collection="clients") is True
    assert await wrapped.get(url_id, collection="clients") is None


async def test_registered_client_survives_a_restart_with_firestore_style_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMCP_HOME", str(tmp_path))
    settings = AuthSettings(BASE, "fake-client-id", "fake-client-secret", OWNER, KEY)
    shared = MemoryStore()  # stands in for Firestore: outlives the 'instance'
    make = lambda: build_client_storage({"STATE_BACKEND": "firestore"}, KEY, lambda _env: shared)

    first, app1 = app_for(settings, make())
    client_id = await register(app1)
    assert await first.get_client(client_id) is not None

    second, _ = app_for(settings, make())  # a new instance after scale-to-zero
    known = await second.get_client(client_id)
    assert known is not None and str(known.client_name) == REGISTRATION["client_name"]


async def test_registered_client_survives_a_restart_with_the_file_store(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMCP_HOME", str(tmp_path))
    settings = AuthSettings(BASE, "fake-client-id", "fake-client-secret", OWNER, KEY)
    _first, app1 = app_for(settings, None)
    client_id = await register(app1)
    second, _ = app_for(settings, None)
    assert await second.get_client(client_id) is not None
