"""The confirm token is the only way to write, so its rules are tested on their own."""

import pytest

from icloud_mcp_server.core.errors import CalendarError
from icloud_mcp_server.core.writes import (
    CONFIRM_WINDOW_SECONDS,
    MAX_CONFIRMS,
    MAX_PENDING,
    TOKEN_TTL_SECONDS,
    PendingChange,
    PendingStore,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def change(n=0, **kw):
    return PendingChange(action="create", calendar_id="cal-test", payload={"title": f"t{n}"}, **kw)


def test_token_is_random_urlsafe_and_unique():
    store = PendingStore()
    tokens = {store.issue(change(i))[0] for i in range(20)}
    assert len(tokens) == 20
    assert all(len(t) >= 22 for t in tokens)  # 16 random bytes encode to 22 url-safe characters


def test_redeem_returns_the_exact_change_once():
    store = PendingStore()
    token, _ = store.issue(change(1))
    assert store.redeem(token).payload == {"title": "t1"}
    with pytest.raises(CalendarError) as err:
        store.redeem(token)
    assert err.value.code == "TOKEN_INVALID"


def test_unknown_token_is_invalid():
    with pytest.raises(CalendarError) as err:
        PendingStore().redeem("not-a-token")
    assert err.value.code == "TOKEN_INVALID"


def test_token_expires_after_five_minutes_and_is_then_gone():
    clock = Clock()
    store = PendingStore(clock)
    token, expires = store.issue(change())
    assert expires == clock.now + TOKEN_TTL_SECONDS == 1300.0
    clock.now = 1300.1
    with pytest.raises(CalendarError) as err:
        store.redeem(token)
    assert err.value.code == "TOKEN_EXPIRED"
    with pytest.raises(CalendarError) as again:
        store.redeem(token)
    assert again.value.code == "TOKEN_INVALID"


def test_token_still_works_at_the_last_second():
    clock = Clock()
    store = PendingStore(clock)
    token, _ = store.issue(change())
    clock.now = 1000.0 + TOKEN_TTL_SECONDS
    assert store.redeem(token).action == "create"


def test_a_changed_payload_no_longer_matches_its_digest():
    store = PendingStore()
    token, _ = store.issue(change(1))
    stored = store._pending[token][0]
    store._pending[token] = (
        PendingChange(stored.action, stored.calendar_id, payload={"title": "tampered"}, digest=stored.digest),
        store._pending[token][1],
    )
    with pytest.raises(CalendarError) as err:
        store.redeem(token)
    assert err.value.code == "TOKEN_INVALID"


def test_at_most_ten_confirmations_per_ten_minutes():
    clock = Clock()
    store = PendingStore(clock)
    for i in range(MAX_CONFIRMS):
        store.redeem(store.issue(change(i))[0])
    token, _ = store.issue(change(99))
    with pytest.raises(CalendarError) as err:
        store.redeem(token)
    assert err.value.code == "RATE_LIMITED"
    clock.now += CONFIRM_WINDOW_SECONDS + 1
    token, _ = store.issue(change(100))
    assert store.redeem(token).payload == {"title": "t100"}


def test_pending_tokens_are_capped_and_expired_ones_purged():
    clock = Clock()
    store = PendingStore(clock)
    for i in range(MAX_PENDING + 5):
        store.issue(change(i))
    assert len(store._pending) == MAX_PENDING
    clock.now += TOKEN_TTL_SECONDS + 1
    store.issue(change(0))
    assert len(store._pending) == 1
