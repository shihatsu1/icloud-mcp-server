"""One log line per HTTP request: method, path, status, duration. Nothing else.

OAuth callbacks carry one-time `code=` and `state=` query values, and requests carry bearer tokens, so the
query string, headers and bodies are never read here. Paths that embed a client identifier or token are
collapsed to their first segment group. Cloud Run's own request log is separate (see docs/RUNBOOK-CLOUDRUN.md).
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger("icloud_mcp_server.http")

_KNOWN = ("/mcp", "/ping", "/authorize", "/token", "/register", "/auth/callback", "/consent", "/.well-known")


def _safe_path(path: str) -> str:
    for known in _KNOWN:
        if path == known or path.startswith(known + "/"):
            if known == "/.well-known":
                parts = path.split("/")
                return "/.well-known/" + (parts[2] if len(parts) > 2 else "")
            return known
    return "(other)"


class RequestTiming:
    """Pure ASGI middleware (no body or header access)."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started, status = time.monotonic(), 0

        async def wrapped(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, wrapped)
        finally:
            log.info(
                "http %s %s -> %s in %d ms",
                scope.get("method", "?"), _safe_path(scope.get("path", "")), status, (time.monotonic() - started) * 1000,
            )
