"""The confirm token is the only way to write, so its rules are tested on their own."""

import pytest

from icloud_mcp_server.core.errors import CalendarError
from icloud_mcp_server.core.writes import (
    CONFIRM_WINDOW_SECONDS,
    JOURNAL_SIZE,
    MAX_CONFIRMS,
    MAX_PENDING,
    TOKEN_TTL_SECONDS,
    OperationRecord,
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
    assert store.redeem(token).change.payload == {"title": "t1"}
    second = store.redeem(token)  # a second call never hands the change out again
    assert second.change is None and second.record is not None


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
    assert store.redeem(token).change.action == "create"


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
    assert store.redeem(token).change.payload == {"title": "t100"}


def test_pending_tokens_are_capped_and_expired_ones_purged():
    clock = Clock()
    store = PendingStore(clock)
    for i in range(MAX_PENDING + 5):
        store.issue(change(i))
    assert len(store._pending) == MAX_PENDING
    clock.now += TOKEN_TTL_SECONDS + 1
    store.issue(change(0))
    assert len(store._pending) == 1


# ---- outcome journal, binding and immutability (2026-11 hardening) --------------------------------


def test_a_used_token_returns_the_recorded_outcome_and_never_the_change():
    store = PendingStore()
    token, _ = store.issue(change(1))
    first = store.redeem(token)
    assert first.change is not None and first.record is None
    again = store.redeem(token)  # a lost answer, asked for again
    assert again.change is None and again.record.outcome == "in_progress"
    store.settle(OperationRecord(first.change.operation_id, "create", "applied", "done", principal=None, at=1.0))
    assert store.redeem(token).record.outcome == "applied"


def test_a_token_is_bound_to_the_principal_that_previewed_it():
    store = PendingStore()
    token, _ = store.issue(change(1, principal="alice"))
    with pytest.raises(CalendarError) as err:
        store.redeem(token, "mallory")
    assert err.value.code == "TOKEN_INVALID"
    assert store.redeem(token, "alice").change is not None  # the wrong try did not burn it


def test_a_used_token_is_not_replayed_to_another_principal():
    store = PendingStore()
    token, _ = store.issue(change(1, principal="alice"))
    store.redeem(token, "alice")
    with pytest.raises(CalendarError):
        store.redeem(token, "mallory")


def test_changing_the_original_payload_after_issue_does_not_change_the_approval():
    store = PendingStore()
    payload = {"title": "t1", "list": [1]}
    token, _ = store.issue(PendingChange("create", "cal-test", payload=payload))
    payload["title"] = "changed afterwards"
    payload["list"].append(2)
    assert store.redeem(token).change.payload == {"title": "t1", "list": [1]}


def test_the_returned_change_is_a_copy_of_the_stored_one():
    store = PendingStore()
    token, _ = store.issue(change(1))
    got = store.redeem(token).change
    got.payload["title"] = "x"
    token2, _ = store.issue(change(1))
    assert store.redeem(token2).change.payload == {"title": "t1"}


def test_the_journal_is_bounded():
    store = PendingStore()
    for i in range(JOURNAL_SIZE + 20):
        store.settle(OperationRecord(f"op{i}", "create", "applied", at=1.0))
    assert len(store._records) == JOURNAL_SIZE
