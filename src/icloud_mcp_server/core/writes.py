"""Pending-change tokens and the outcome journal for the preview, then confirm write protocol.

A preview never writes. It stores the exact change here and hands back a random single-use token;
confirm_change redeems it. State is deliberately in memory (see docs/DESIGN.md "Confirmation state"):
Cloud Run runs one instance, tokens live five minutes, and a restart only drops pending approvals,
which is the safe direction. What a restart must not lose is the truth about the CALENDAR, so every
operation has an ID and (for creates) a UID fixed at preview time, and an uncertain outcome is
settled by reading the calendar, never by writing again.

Stored changes are deep-copied in and out and carry a digest, so nothing a caller still holds can alter
what was approved. A token is bound to the authenticated principal that previewed it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from .errors import CalendarError

TOKEN_TTL_SECONDS = 300
MAX_CONFIRMS = 10
CONFIRM_WINDOW_SECONDS = 600
MAX_PENDING = 50
JOURNAL_SIZE = 200
JOURNAL_SECONDS = 3600

Outcome = Literal["in_progress", "applied", "not_applied", "unknown"]


@dataclass(frozen=True)
class PendingChange:
    action: str  # create | update | delete
    calendar_id: str
    event_id: str | None = None  # for a create: the event_id it WILL have (UID fixed at preview time)
    occurrence_start: str | None = None  # ISO text as given to the backend
    scope: str | None = None  # whole | occurrence
    payload: dict[str, Any] = field(default_factory=dict)
    version: str | None = None  # target's version at preview time
    operation_id: str = ""
    principal: str | None = None  # who previewed it (hash of subject and client); None without auth
    digest: str = ""

    def _body(self) -> str:
        return json.dumps(
            [
                self.action,
                self.calendar_id,
                self.event_id,
                self.occurrence_start,
                self.scope,
                self.payload,
                self.version,
                self.operation_id,
                self.principal,
            ],
            sort_keys=True,
            default=repr,
        )

    def _digest(self) -> str:
        return hashlib.sha256(self._body().encode()).hexdigest()

    def with_digest(self) -> PendingChange:
        """A sealed private copy: the caller's payload can no longer change what was approved."""
        copy_ = replace(self, payload=copy.deepcopy(self.payload), operation_id=self.operation_id or secrets.token_hex(8), digest="")
        return replace(copy_, digest=copy_._digest())

    def intact(self) -> bool:
        return self.digest == self._digest()


@dataclass(frozen=True)
class OperationRecord:
    """What is known about one approved operation. Kept briefly so a lost response can be answered."""

    operation_id: str
    action: str
    outcome: Outcome
    message: str = ""
    event_id: str | None = None
    detail: tuple[str, ...] = ()
    change: PendingChange | None = None
    principal: str | None = None
    at: float = 0.0


@dataclass(frozen=True)
class Redeemed:
    change: PendingChange | None = None
    record: OperationRecord | None = None  # set when the token was already used: answer from this, never re-run


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class PendingStore:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[PendingChange, float]] = {}
        self._confirmed: deque[float] = deque()
        self._used: OrderedDict[str, str] = OrderedDict()  # token hash -> operation_id
        self._records: OrderedDict[str, OperationRecord] = OrderedDict()

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

    def redeem(self, token: str, principal: str | None = None) -> Redeemed:
        """Consume a token. It works once. A second call returns the recorded outcome and never the change."""
        with self._lock:
            now = self._clock()
            entry = self._pending.get(token)  # not purged first: an expired token must say "expired"
            if entry is None:
                used = self._used.get(_token_key(token))
                record = self._records.get(used) if used else None
                if record is None:
                    raise CalendarError("TOKEN_INVALID", "That confirm_token is unknown or was already used.")
                if record.principal != principal:
                    raise CalendarError("TOKEN_INVALID", "That confirm_token is unknown or was already used.")
                return Redeemed(record=record)
            change, expires = entry
            if change.principal != principal:  # not consumed: the rightful principal can still use it
                raise CalendarError("TOKEN_INVALID", "That confirm_token was issued to a different sign-in.")
            del self._pending[token]  # removed first: a token is never reusable
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
            self._used[_token_key(token)] = change.operation_id
            self._set(
                OperationRecord(
                    change.operation_id, change.action, "in_progress", event_id=change.event_id,
                    change=change, principal=change.principal, at=now,
                )
            )
            return Redeemed(change=copy.deepcopy(change))

    def settle(self, record: OperationRecord) -> None:
        """Record how an operation ended (or that it is unknown)."""
        with self._lock:
            self._set(record)

    def unknown(self, action: str) -> list[OperationRecord]:
        """Operations of this kind whose outcome is still unknown (newest last)."""
        with self._lock:
            return [r for r in self._records.values() if r.action == action and r.outcome == "unknown"]

    def _set(self, record: OperationRecord) -> None:
        self._records[record.operation_id] = record
        self._records.move_to_end(record.operation_id)
        while len(self._records) > JOURNAL_SIZE:
            self._records.popitem(last=False)
        while len(self._used) > JOURNAL_SIZE:
            self._used.popitem(last=False)

    def _purge(self, now: float) -> None:
        for token in [t for t, (_c, exp) in self._pending.items() if now > exp]:
            del self._pending[token]
        for op in [o for o, r in self._records.items() if now - r.at > JOURNAL_SECONDS and r.outcome != "in_progress"]:
            del self._records[op]
