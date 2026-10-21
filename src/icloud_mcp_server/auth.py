"""OAuth for the hosted server: only the owner's GitHub account may use it.

FastMCP's OAuth proxy fronts GitHub. Claude and ChatGPT register with this server (CIMD or DCR) and
this server issues them tokens, but every request is re-validated against the owner's numeric GitHub
user ID, so a stranger who signs in with GitHub gets a token that is rejected everywhere. A second,
per-tool check repeats the test (defence in depth). FastMCP has no built-in single-user allow-list
(docs reviewed 2026-10-21; verified against the installed 4.1.3), so this is our code and our tests.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse

from fastmcp.server.auth import AccessToken, AuthCheck, AuthContext, TokenVerifier
from fastmcp.server.auth.providers.github import GitHubProvider

log = logging.getLogger("icloud_mcp_server.auth")

# Redirect URIs of the clients we expect. Claude: https://claude.ai/api/mcp/auth_callback
# (Anthropic connector docs). ChatGPT uses one of two forms depending on RFC 9207 issuer support
# (OpenAI Apps SDK auth page, 2026-10-21). Anything else is refused at registration.
DEFAULT_REDIRECT_URIS = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://chatgpt.com/connector/oauth/*",
)
GITHUB_SCOPES = ["read:user"]  # identity only; FastMCP's default would be the broader "user"


class AuthConfigError(ValueError):
    """Raised with the NAMES of bad settings, never their values."""


def _hidden(name: str) -> str:
    return f"{name}=<hidden>"


@dataclass(frozen=True)
class AuthSettings:
    base_url: str
    github_client_id: str
    github_client_secret: str = field(repr=False)
    owner_id: str = ""
    jwt_signing_key: str = field(default="", repr=False)
    extra_redirect_uris: tuple[str, ...] = ()

    def __repr__(self) -> str:
        return (
            f"AuthSettings(base_url={self.base_url!r}, github_client_id={self.github_client_id!r}, "
            f"owner_id={self.owner_id!r}, {_hidden('github_client_secret')}, {_hidden('jwt_signing_key')})"
        )

    @property
    def redirect_uris(self) -> list[str]:
        return [*DEFAULT_REDIRECT_URIS, *self.extra_redirect_uris]

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> AuthSettings:
        problems: list[str] = []

        def need(name: str) -> str:
            value = env.get(name, "").strip()
            if not value:
                problems.append(f"{name} is not set")
            return value

        base_url = need("PUBLIC_BASE_URL").rstrip("/")
        client_id = need("GITHUB_CLIENT_ID")
        client_secret = need("GITHUB_CLIENT_SECRET")
        owner_id = need("ALLOWED_GITHUB_USER_ID")
        jwt_key = need("JWT_SIGNING_KEY")

        if base_url:
            parsed = urlparse(base_url)
            loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
                problems.append("PUBLIC_BASE_URL must be an https URL")
        if owner_id and not owner_id.isdigit():
            problems.append("ALLOWED_GITHUB_USER_ID must be the numeric GitHub user ID")
        if jwt_key and len(jwt_key) < 32:
            problems.append("JWT_SIGNING_KEY must be at least 32 characters")
        extras = tuple(u.strip() for u in env.get("EXTRA_REDIRECT_URIS", "").split(",") if u.strip())
        for uri in extras:
            if not uri.startswith("https://"):
                problems.append("EXTRA_REDIRECT_URIS entries must be https URLs")
        if problems:
            raise AuthConfigError("; ".join(problems))
        return cls(base_url, client_id, client_secret, owner_id, jwt_key, extras)


class OwnerOnlyVerifier(TokenVerifier):
    """Wraps the upstream verifier and accepts only the owner's numeric GitHub user ID."""

    def __init__(self, inner: TokenVerifier, owner_id: str):
        super().__init__(required_scopes=inner.required_scopes)
        self._inner = inner
        self._owner_id = str(owner_id)

    async def verify_token(self, token: str) -> AccessToken | None:
        verified = await self._inner.verify_token(token)
        if verified is None:
            return None
        if str(verified.claims.get("sub", "")) != self._owner_id:
            log.warning("rejected a token for a GitHub account that is not the owner")
            return None
        return verified


class OwnerOnlyGitHubProvider(GitHubProvider):
    def __init__(self, *, owner_id: str, **kwargs):
        super().__init__(**kwargs)
        self._token_validator = OwnerOnlyVerifier(self._token_validator, owner_id)


def owner_check(owner_id: str) -> AuthCheck:
    """Per-tool check, repeated on top of the verifier."""
    expected = str(owner_id)

    def check(ctx: AuthContext) -> bool:
        token = ctx.token
        return token is not None and str(token.claims.get("sub", "")) == expected

    return check


def build_auth(settings: AuthSettings) -> OwnerOnlyGitHubProvider:
    return OwnerOnlyGitHubProvider(
        owner_id=settings.owner_id,
        client_id=settings.github_client_id,
        client_secret=settings.github_client_secret,
        base_url=settings.base_url,
        required_scopes=GITHUB_SCOPES,
        jwt_signing_key=settings.jwt_signing_key,
        allowed_client_redirect_uris=settings.redirect_uris,
    )
