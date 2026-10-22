"""What Claude and ChatGPT need from the OAuth layer (docs/DESIGN.md section 4.2b)."""

import httpx
import pytest

from tests.auth.conftest import BASE


@pytest.fixture
async def client(secured_app):
    _server, app = secured_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c


async def test_unauthenticated_mcp_call_gets_401_with_resource_metadata_pointer(client):
    resp = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert resp.status_code == 401
    challenge = resp.headers["www-authenticate"]
    assert challenge.startswith("Bearer") and "resource_metadata=" in challenge
    assert f"{BASE}/.well-known/oauth-protected-resource" in challenge


async def test_protected_resource_metadata_names_this_server_and_its_issuer(client):
    resp = await client.get("/.well-known/oauth-protected-resource/mcp")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["resource"] == f"{BASE}/mcp"  # must equal the URL users enter, including the path
    assert doc["authorization_servers"][0].rstrip("/") == BASE


async def test_authorization_server_metadata_supports_pkce_dcr_and_cimd(client):
    resp = await client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["code_challenge_methods_supported"] == ["S256"]
    assert doc["registration_endpoint"].endswith("/register")
    assert doc.get("client_id_metadata_document_supported") is True
    assert "none" in doc["token_endpoint_auth_methods_supported"]
    assert doc["issuer"].rstrip("/") == BASE


@pytest.mark.parametrize(
    "redirect",
    [
        "https://claude.ai/api/mcp/auth_callback",
        "https://chatgpt.com/connector_platform_oauth_redirect",
        "https://chatgpt.com/connector/oauth/abc123",
    ],
)
async def test_known_clients_can_register(client, redirect):
    resp = await client.post(
        "/register",
        json={
            "client_name": "test client",
            "redirect_uris": [redirect],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert resp.status_code in (200, 201), resp.text


@pytest.mark.parametrize("redirect", ["https://evil.example/cb", "http://claude.ai/api/mcp/auth_callback"])
async def test_unknown_redirect_uris_are_refused_at_registration(client, redirect):
    resp = await client.post(
        "/register",
        json={
            "client_name": "attacker",
            "redirect_uris": [redirect],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert resp.status_code == 400, resp.text


async def test_token_endpoint_accepts_form_encoding_and_reports_oauth_errors(client):
    resp = await client.post(
        "/token",
        data={"grant_type": "refresh_token", "refresh_token": "nope", "client_id": "unknown"},
    )
    assert resp.status_code != 415, "token endpoint must accept application/x-www-form-urlencoded"
    assert resp.status_code in (400, 401)
    assert "error" in resp.json()


async def test_health_endpoint_is_open_and_reveals_nothing(client):
    resp = await client.get("/ping")
    assert resp.status_code == 200 and resp.text == "ok"
