"""Credential loading. The Apple ID password is never used: only an app-specific password.

Order: environment (hosted: injected by the platform's secret store), then the macOS Keychain
(local development). Values are never logged and never placed in error messages.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field

KEYCHAIN_SERVICE = "icloud-mcp-server"


@dataclass(frozen=True)
class Credentials:
    apple_id: str
    password: str = field(repr=False)

    def __repr__(self) -> str:  # never show the password, even by accident
        return f"Credentials(apple_id={self.apple_id!r}, password=<hidden>)"


class MissingCredentials(Exception):
    pass


def _from_keychain(apple_id: str) -> str | None:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", apple_id, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return value if out.returncode == 0 and value else None


def load_credentials(env: Mapping[str, str] | None = None) -> Credentials:
    env = os.environ if env is None else env
    apple_id = env.get("ICLOUD_APPLE_ID", "").strip()
    if not apple_id:
        raise MissingCredentials("ICLOUD_APPLE_ID is not set.")
    password = env.get("ICLOUD_APP_PASSWORD", "").strip() or _from_keychain(apple_id)
    if not password:
        raise MissingCredentials(
            "No app-specific password found. Set ICLOUD_APP_PASSWORD (hosted secret store) or add it "
            f"to the macOS Keychain with: security add-generic-password -U -s {KEYCHAIN_SERVICE} "
            "-a <apple-id> -w"
        )
    return Credentials(apple_id=apple_id, password=password)
