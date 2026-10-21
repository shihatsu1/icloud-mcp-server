"""Where the OAuth proxy keeps client registrations and tokens.

- file (default): FastMCP's own encrypted file store, kept under FASTMCP_HOME. Right for a machine
  with a disk (local, Fly.io with a volume).
- firestore: Google Firestore, for Cloud Run, whose filesystem is wiped when an instance recycles.
  Without durable state you would have to sign in again after every idle period.

Everything written to Firestore is encrypted first with the library's Fernet wrapper, keyed from
JWT_SIGNING_KEY, so Google only ever stores ciphertext. Decryption failures (for example after you
rotate the key) read as "not found", which simply makes clients register again.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .auth import AuthConfigError

STATE_SALT = "icloud-mcp-server-oauth-state"


def _firestore_store(env: Mapping[str, str]) -> Any:
    # Imported lazily: the Google client libraries add about a second to start-up, and only the
    # Firestore deployment needs them.
    from key_value.aio.stores.firestore import FirestoreStore

    return FirestoreStore(
        project=env.get("GOOGLE_CLOUD_PROJECT") or None,
        database=env.get("FIRESTORE_DATABASE") or None,
    )


def build_client_storage(
    env: Mapping[str, str],
    signing_key: str,
    store_factory: Callable[[Mapping[str, str]], Any] | None = None,
) -> Any | None:
    """Return the storage object for the OAuth proxy, or None to use FastMCP's default file store."""
    backend = env.get("STATE_BACKEND", "file").strip().lower() or "file"
    if backend == "file":
        return None
    if backend != "firestore":
        raise AuthConfigError("STATE_BACKEND must be 'file' or 'firestore'")

    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    store = (store_factory or _firestore_store)(env)
    return FernetEncryptionWrapper(
        key_value=store,
        source_material=signing_key,
        salt=STATE_SALT,
        raise_on_decryption_error=False,
    )
