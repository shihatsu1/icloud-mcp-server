"""Pending-change tokens for the preview, then confirm write protocol (docs/TOOLS.md section 8).

A preview never writes. It stores the exact change here and hands back a random single-use token;
confirm_change redeems it. Both hosting modes run one instance, so memory is enough: a restart drops
pending tokens, which is the safe direction.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import CalendarError

TOKEN_TTL_SECONDS = 300
MAX_CONFIRMS = 10
CONFIRM_WINDOW_SECONDS = 600
MAX_PENDING = 50


@dataclass(frozen=True)
class PendingChange:
    action: str  # create | update | delete
    calendar_id: str
    event_id: str | None = None
    occurrence_start: str | None = None  # ISO text as given to the backend
    scope: str | None = None  # whole | occurrence
    payload: dict[str, Any] = field(default_factory=dict)
    version: str | None = None  # target's version at preview time
    digest: str = ""

    def with_digest(self) -> PendingChange:
        body = json.dumps(
            [self.action, self.calendar_id, self.event_id, self.occurrence_start, self.scope, self.payload],
            sort_keys=True,
            default=str,
        )
        return PendingChange(
            self.action,
            self.calendar_id,
            self.event_id,
            self.occurrence_start,
            self.scope,
            self.payload,
            self.version,
            hashlib.sha256(body.encode()).hexdigest(),
        )

    def intact(self) -> bool:
        return self.digest == self.with_digest().digest


class PendingStore:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[PendingChange, float]] = {}
        self._confirmed: deque[float] = deque()

    def issue(self, change: PendingChange) -> tuple[str, float]:
        """Store the change and return (token, expiry as epoch seconds)."""
        change = change.with_digest()
        with self._lock:
            now = self._clock()
            self._purge(now)
            if len(self._pending) >= MAX_PENDING:
                oldest = min(self._pending, key=lambda t: self._pending[t][1])
                del self._pending[oldest]
            token = secrets.token_urlsafe(16)  # 128 bits
            expires = now + TOKEN_TTL_SECONDS
            self._pending[token] = (change, expires)
            return token, expires

    def redeem(self, token: str) -> PendingChange:
        """Consume a token. It works once; an expired or unknown token raises."""
        with self._lock:
            now = self._clock()
            entry = self._pending.pop(token, None)  # popped first: a token is never reusable
            if entry is None:
                raise CalendarError("TOKEN_INVALID", "That confirm_token is unknown or was already used.")
            change, expires = entry
            if now > expires:
                raise CalendarError("TOKEN_EXPIRED", "That confirm_token has expired.")
            if not change.intact():
                raise CalendarError("TOKEN_INVALID", "That confirm_token no longer matches its change.")
            while self._confirmed and now - self._confirmed[0] > CONFIRM_WINDOW_SECONDS:
                self._confirmed.popleft()
            if len(self._confirmed) >= MAX_CONFIRMS:
                raise CalendarError(
                    "RATE_LIMITED", f"At most {MAX_CONFIRMS} changes can be confirmed per {CONFIRM_WINDOW_SECONDS // 60} minutes."
                )
            self._confirmed.append(now)
            return change

    def _purge(self, now: float) -> None:
        for token in [t for t, (_c, exp) in self._pending.items() if now > exp]:
            del self._pending[token]
