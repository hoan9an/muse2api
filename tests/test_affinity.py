from __future__ import annotations

import time

import pytest

from muse2api.accounts import Account, AccountPool, JsonAccountStore
from muse2api.accounts.pool import AffinityStrategy


def _acc(acc_id: str, last_used: float = 0.0, used_pct: float | None = None) -> Account:
    acc = Account(id=acc_id, cookies={"hatch_sess": "x"}, last_used_at=last_used)
    if used_pct is not None:
        acc.meta["quota"] = {"weekly_used_pct": used_pct}
    return acc


def test_a_bound_conversation_stays_on_its_account():
    strategy = AffinityStrategy(low_quota_pct=0)
    a0, a1 = _acc("a0"), _acc("a1", last_used=10)
    assert strategy.choose([a0, a1], "conv") is a0
    a0.last_used_at = 100  # a1 would now win on LRU alone
    assert strategy.choose([a0, a1], "conv") is a0


def test_a_hit_renews_the_binding():
    strategy = AffinityStrategy(ttl=60, low_quota_pct=0)
    a0, a1 = _acc("a0"), _acc("a1", last_used=10)
    assert strategy.choose([a0, a1], "conv") is a0
    strategy._map["conv"] = ("a0", time.time() - 59)
    renewed_from = strategy._map["conv"][1]
    assert strategy.choose([a0, a1], "conv") is a0
    assert strategy._map["conv"][1] > renewed_from


def test_an_expired_binding_falls_back_to_lru():
    strategy = AffinityStrategy(ttl=60, low_quota_pct=0)
    a0, a1 = _acc("a0"), _acc("a1", last_used=5)
    assert strategy.choose([a0, a1], "conv") is a0
    a0.last_used_at = 50  # a1 is the least recently used now
    strategy._map["conv"] = ("a0", time.time() - 61)
    assert strategy.choose([a0, a1], "conv") is a1
    assert strategy._map["conv"][0] == "a1"


def test_an_absent_bound_account_is_rebound_not_reused():
    strategy = AffinityStrategy(low_quota_pct=0)
    strategy._map["conv"] = ("gone", time.time())
    assert strategy.choose([_acc("a0")], "conv") is not None
    assert strategy._map["conv"][0] == "a0"


def test_a_nearly_exhausted_account_is_skipped_for_new_conversations():
    strategy = AffinityStrategy(low_quota_pct=85)
    low, roomy = _acc("low", used_pct=92), _acc("roomy", last_used=50, used_pct=10)
    assert strategy.choose([low, roomy], "new-conv") is roomy


def test_a_nearly_exhausted_account_keeps_its_own_conversation():
    strategy = AffinityStrategy(low_quota_pct=85)
    low, roomy = _acc("low", used_pct=95), _acc("roomy", last_used=50, used_pct=5)
    strategy._map["conv"] = ("low", time.time())
    assert strategy.choose([low, roomy], "conv") is low


def test_an_account_without_a_quota_reading_is_never_penalised():
    strategy = AffinityStrategy(low_quota_pct=85)
    unknown = _acc("unknown")
    assert strategy.choose([_acc("low", used_pct=99), unknown], "conv") is unknown


def test_quota_reading_accepts_used_over_limit():
    strategy = AffinityStrategy(low_quota_pct=85)
    low = _acc("low")
    low.meta["quota"] = {"messages_used": 90, "messages_limit": 100}
    roomy = _acc("roomy", last_used=50)
    assert strategy.choose([low, roomy], "conv") is roomy


def test_forget_drops_only_that_accounts_bindings():
    strategy = AffinityStrategy(low_quota_pct=0)
    a0, a1 = _acc("a0"), _acc("a1", last_used=10)
    assert strategy.choose([a0], "one") is a0
    assert strategy.choose([a1], "two") is a1
    strategy.forget("a0")
    assert "one" not in strategy._map
    assert "two" in strategy._map


@pytest.mark.parametrize("bad", [0, 1])
def test_ttl_of_zero_never_expires(bad):
    strategy = AffinityStrategy(ttl=0, low_quota_pct=0)
    a0, a1 = _acc("a0"), _acc("a1", last_used=10)
    assert strategy.choose([a0, a1], "conv") is a0
    strategy._map["conv"] = ("a0", time.time() - 10 * 86400)
    assert strategy.choose([a0, a1], "conv") is a0


async def test_removing_an_account_clears_its_bindings(tmp_path):
    pool = AccountPool(JsonAccountStore(tmp_path / "accounts.json"), strategy="affinity",
                       acquire_timeout=0.5)
    await pool.upsert(_acc("a0"))
    await pool.upsert(_acc("a1"))

    leased = await pool.acquire(hint="conv")
    await pool.release(leased)
    assert pool.strategy._map["conv"][0] == leased.id

    await pool.remove(leased.id)
    assert "conv" not in pool.strategy._map
