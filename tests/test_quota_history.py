from __future__ import annotations

import datetime as dt

from muse2api.core.quota import used_pct
from muse2api.services.quota_history import QuotaHistory, forecast, hours_to_reset


def test_used_pct_reads_both_snapshot_shapes():
    assert used_pct({"weekly_used_pct": 42}) == 42
    assert used_pct({"messages_used": 30, "messages_limit": 60}) == 50
    assert used_pct({"messages_used": 3, "messages_limit": 0}) is None
    assert used_pct({}) is None
    assert used_pct(None) is None


def test_forecast_needs_two_samples():
    f = forecast([{"ts": 0, "used_pct": 10, "resets_at": None}], now=0)
    assert f["pct"] == 10
    assert f["rate_per_hour"] is None
    assert f["will_exhaust"] is None


def test_forecast_flags_exhaustion_before_the_reset():
    now = 1_000_000.0
    reset = now + 3 * 3600
    # 30% used, climbing 25 points an hour: 2.8h to the cap, the week rolls over in 3h.
    samples = [
        {"ts": now - 3600, "used_pct": 5, "resets_at": reset},
        {"ts": now, "used_pct": 30, "resets_at": reset},
    ]
    f = forecast(samples, now=now)
    assert f["rate_per_hour"] == 25.0
    assert f["hours_to_limit"] == 2.8
    assert f["hours_to_reset"] == 3.0
    assert f["will_exhaust"] is True


def test_forecast_says_safe_when_the_reset_comes_first():
    now = 1_000_000.0
    reset = now + 3 * 3600
    # 1 point an hour: 89h to the cap, so the reset arrives long before it.
    samples = [
        {"ts": now - 3600, "used_pct": 10, "resets_at": reset},
        {"ts": now, "used_pct": 11, "resets_at": reset},
    ]
    f = forecast(samples, now=now)
    assert f["hours_to_limit"] == 89.0
    assert f["will_exhaust"] is False


def test_hours_to_reset_parses_iso_dates_and_rejects_junk():
    when = dt.datetime(2026, 1, 2, 3, 4, 5).timestamp()
    assert hours_to_reset("2026-01-02T03:04:05", when - 3600) == 1.0
    assert hours_to_reset("2026-01-02", when) == 0.0  # midnight is already past
    assert hours_to_reset("not a date", when) is None
    assert hours_to_reset(None, when) is None


def test_flat_usage_is_not_going_to_run_out():
    now = 1_000_000.0
    samples = [{"ts": now - 3600, "used_pct": 40, "resets_at": None},
               {"ts": now, "used_pct": 40, "resets_at": None}]
    assert forecast(samples, now=now)["will_exhaust"] is False


def test_quota_covered_by_a_reset_is_not_a_leak():
    # Usage dropped because the week rolled over: nothing to forecast.
    now = 1_000_000.0
    samples = [{"ts": now - 3600, "used_pct": 90, "resets_at": None},
               {"ts": now, "used_pct": 2, "resets_at": None}]
    f = forecast(samples, now=now)
    assert f["rate_per_hour"] < 0
    assert f["will_exhaust"] is False


async def test_store_dedupes_a_repeated_reading(tmp_path):
    store = QuotaHistory(tmp_path / "quota.db")
    try:
        await store.record("a0", {"weekly_used_pct": 12, "plan": "pro"})
        await store.record("a0", {"weekly_used_pct": 12, "plan": "pro"})
        data = await store.series(account_id="a0", days=7)
        assert len(data["accounts"]["a0"]) == 1
        assert data["accounts"]["a0"][0]["plan"] == "pro"
    finally:
        await store.close()


async def test_store_keeps_one_series_per_account(tmp_path):
    store = QuotaHistory(tmp_path / "quota.db")
    try:
        await store.record("a0", {"weekly_used_pct": 10})
        await store.record("a1", {"messages_used": 5, "messages_limit": 10})
        data = await store.series(days=7)
        assert set(data["accounts"]) == {"a0", "a1"}
        assert data["forecast"]["a1"]["pct"] == 50.0
        only = await store.series(account_id="a1", days=7)
        assert set(only["accounts"]) == {"a1"}
    finally:
        await store.close()
