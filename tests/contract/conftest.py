"""A throwaway local Radicale CalDAV server (test tool only; GPL, not a runtime dependency)."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from icloud_mcp_server.backends.caldav_backend import CalDavBackend


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def radicale_url(tmp_path_factory) -> str:
    port = _free_port()
    storage: Path = tmp_path_factory.mktemp("radicale")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "radicale",
            "--server-hosts", f"127.0.0.1:{port}",
            "--storage-filesystem-folder", str(storage),
            "--auth-type", "none",
            "--logging-level", "warning",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            if proc.poll() is not None:
                raise RuntimeError("Radicale exited early: " + (proc.stdout.read() or b"").decode())
            time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError("Radicale did not start")
    yield f"http://127.0.0.1:{port}/"
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture
def user(radicale_url):
    return "user-" + uuid.uuid4().hex[:8]


@pytest.fixture
def backend(radicale_url, user):
    return CalDavBackend(user, "unused", url=radicale_url, allow_insecure_http=True)
