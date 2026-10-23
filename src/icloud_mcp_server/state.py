"""Where the OAuth proxy keeps client registrations and tokens.

- file (default): FastMCP's own encrypted file store, kept under FASTMCP_HOME. Right for a machine
  with a disk (local, Fly.io with a volume).
- firestore: Google Firestore, for Cloud Run, whose filesystem is wiped when an instance recycles.
  Without durable state you would have to sign in again after every idle period.

Keys are hashed before they reach Firestore (client IDs are URLs, which Firestore rejects as
document names). Everything written to Firestore is encrypted first with the library's Fernet wrapper, keyed from
JWT_SIGNING_KEY, so Google only ever stores ciphertext. Decryption failures (for example after you
rotate the key) read as "not found", which simply makes clients register again.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any, SupportsFloat

from key_value.aio.wrappers.base import BaseWrapper

from .auth import AuthConfigError

STATE_SALT = "icloud-mcp-server-oauth-state"


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class HashedKeys(BaseWrapper):
    """Store every key as its SHA-256 hex digest.

    Claude's client ID is a URL (https://claude.ai/oauth/...). Firestore treats each "/" in a
    document name as a path separator and rejects it, so URL keys cannot be used as they are. A hex
    digest is safe for any backend and reveals nothing about the key.
    """

    def __init__(self, key_value: Any) -> None:
        self.key_value = key_value

    async def get(self, key: str, *, collection: str | None = None) -> dict[str, Any] | None:
        return await super().get(_hash_key(key), collection=collection)

    async def get_many(self, keys: Sequence[str], *, collection: str | None = None) -> list[dict[str, Any] | None]:
        return await super().get_many([_hash_key(k) for k in keys], collection=collection)

    async def ttl(self, key: str, *, collection: str | None = None) -> tuple[dict[str, Any] | None, float | None]:
        return await super().ttl(_hash_key(key), collection=collection)

    async def ttl_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[tuple[dict[str, Any] | None, float | None]]:
        return await super().ttl_many([_hash_key(k) for k in keys], collection=collection)

    async def put(
        self, key: str, value: Mapping[str, Any], *, collection: str | None = None, ttl: SupportsFloat | None = None
    ) -> None:
        return await super().put(_hash_key(key), value, collection=collection, ttl=ttl)

    async def put_many(
        self,
        keys: Sequence[str],
        values: Sequence[Mapping[str, Any]],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        return await super().put_many([_hash_key(k) for k in keys], values, collection=collection, ttl=ttl)

    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        return await super().delete(_hash_key(key), collection=collection)

    async def delete_many(self, keys: Sequence[str], *, collection: str | None = None) -> int:
        return await super().delete_many([_hash_key(k) for k in keys], collection=collection)


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

    store = HashedKeys((store_factory or _firestore_store)(env))
    return FernetEncryptionWrapper(
        key_value=store,
        source_material=signing_key,
        salt=STATE_SALT,
        raise_on_decryption_error=False,
    )
