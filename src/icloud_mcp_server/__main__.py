"""Command line entry point: `icloud-mcp-server`.

Refuses to start an unauthenticated server unless it is bound to localhost for development.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Mapping, Sequence

from .auth import AuthConfigError, AuthSettings, build_auth, owner_check
from .backends.caldav_backend import CalDavBackend
from .config import Settings
from .core.service import CalendarService
from .secrets import MissingCredentials, load_credentials
from .server import build_server
from .state import build_client_storage

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _parser(env: Mapping[str, str] | None = None) -> argparse.ArgumentParser:
    env = os.environ if env is None else env
    p = argparse.ArgumentParser(
        prog="icloud-mcp-server", description="iCloud calendar MCP server (read-only in this version)."
    )
    p.add_argument("--host", default="127.0.0.1", help="bind address (the container uses 0.0.0.0)")
    p.add_argument("--port", type=int, default=int(env.get("PORT", "8080")), help="default: $PORT or 8080")
    p.add_argument("--dev-no-auth", action="store_true", help="NO authentication; localhost only")
    p.add_argument("--allow-writes", action="store_true", help="not available yet (milestone M4)")
    p.add_argument("--writable-calendars", default="", help="used together with --allow-writes")
    return p


def build(args: argparse.Namespace, env: Mapping[str, str]):
    """Build the server or raise SystemExit-worthy errors as (message, code) via ValueError."""
    if args.allow_writes:
        raise ValueError("Write tools are not available in this version (milestone M4).")

    settings = Settings.from_env(env)
    if args.dev_no_auth:
        if args.host not in LOOPBACK:
            raise ValueError("--dev-no-auth only works with --host 127.0.0.1 (or localhost).")
        if env.get("PUBLIC_BASE_URL"):
            raise ValueError(
                "--dev-no-auth refuses to start while PUBLIC_BASE_URL is set: that looks like a deployment."
            )
        auth = None
        tool_auth = None
    else:
        try:
            auth_settings = AuthSettings.from_env(env)
        except AuthConfigError as exc:
            raise ValueError(f"Authentication is not configured: {exc}.") from None
        try:
            storage = build_client_storage(env, auth_settings.jwt_signing_key)
        except AuthConfigError as exc:
            raise ValueError(f"Authentication is not configured: {exc}.") from None
        auth = build_auth(auth_settings, storage)
        tool_auth = owner_check(auth_settings.owner_id)

    try:
        creds = load_credentials(env)
    except MissingCredentials as exc:
        raise ValueError(str(exc)) from None

    backend = CalDavBackend(creds.apple_id, creds.password)
    service = CalendarService(backend, settings)
    return build_server(service, auth=auth, tool_auth=tool_auth)


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    args = _parser(env).parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        server = build(args, env)
    except ValueError as exc:
        print(f"icloud-mcp-server: {exc}", file=sys.stderr)
        return 2
    if args.dev_no_auth:
        print("WARNING: running WITHOUT authentication on localhost (development only).", file=sys.stderr)
    # access_log off: OAuth callback URLs carry one-time codes and must not be written to logs
    server.run(transport="http", host=args.host, port=args.port, uvicorn_config={"access_log": False})
    return 0


if __name__ == "__main__":
    sys.exit(main())
