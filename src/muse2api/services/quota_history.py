"""Weekly-quota samples: enough history to show a burn rate and a forecast.

Only readings the server actually took are stored (``/admin/accounts/{id}/quota``),
so the trend is honest about what was observed — nothing here talks to muse.ai on
its own. The forecast is deliberately simple: a straight line through the samples
in the window, compared with the reset time the page reported.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.quota import used_pct

log = logging.getLogger(__name__)

# A repeat reading this soon adds nothing to the trend.
MIN_SAMPLE_GAP = 60.0

_RESET_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS quota_samples (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL    NOT NULL,
    account_id TEXT    NOT NULL,
    used_pct   REAL,
    plan       TEXT,
    resets_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_quota_account ON quota_samples (account_id, ts);
"""


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def hours_to_reset(resets_at: Any, now: float) -> float | None:
    """Hours until the reset, for a unix timestamp or a date string; None if unreadable."""
    if isinstance(resets_at, (int, float)):
        return max(0.0, (float(resets_at) - now) / 3600)
    if not isinstance(resets_at, str) or not resets_at.strip():
        return None
    text = resets_at.strip()
    try:
        return max(0.0, (float(text) - now) / 3600)
    except ValueError:
        pass
    for fmt in _RESET_FORMATS:
        try:
            when = datetime.strptime(text[:19], fmt).timestamp()
        except ValueError:
            continue
        return max(0.0, (when - now) / 3600)
    return None


def forecast(samples: list[dict[str, Any]], *, now: float | None = None) -> dict[str, Any]:
    """Burn rate and whether the quota runs out before the reset."""
    now = time.time() if now is None else now
    points = [(float(s["ts"]), float(s["used_pct"])) for s in samples
              if s.get("used_pct") is not None and s.get("ts") is not None]
    out: dict[str, Any] = {
        "samples": len(points),
        "pct": points[-1][1] if points else None,
        "rate_per_hour": None,
        "hours_to_limit": None,
        "hours_to_reset": None,
        "will_exhaust": None,
    }
    if not points:
        return out
    out["hours_to_reset"] = hours_to_reset(samples[-1].get("resets_at"), now)
    if len(points) < 2:
        return out
    (t0, p0), (t1, p1) = points[0], points[-1]
    hours = (t1 - t0) / 3600
    if hours <= 0:
        return out
    rate = (p1 - p0) / hours
    out["rate_per_hour"] = round(rate, 3)
    if rate <= 0:
        # Usage flat or falling (the quota reset): nothing to run out of.
        out["will_exhaust"] = False
        return out
    out["hours_to_limit"] = round(max(0.0, (100.0 - p1) / rate), 2)
    if out["hours_to_reset"] is not None:
        out["will_exhaust"] = out["hours_to_limit"] < out["hours_to_reset"]
    return out


class QuotaHistory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None
        # One connection shared by worker threads; sqlite3 objects are not thread-safe.
        self._lock = threading.Lock()

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            self._conn = conn
        return self._conn

    def _call(self, fn, *args):
        with self._lock:
            return fn(self._db(), *args)

    async def open(self) -> None:
        try:
            await asyncio.to_thread(self._call, lambda db: None)
        except Exception:  # noqa: BLE001
            # Not fatal: the connection is retried on the next write.
            log.exception("failed to open quota history %s", self.path)

    async def close(self) -> None:
        def _close() -> None:
            with self._lock:
                if self._conn is not None:
                    self._conn.close()
                    self._conn = None

        await asyncio.to_thread(_close)

    async def record(self, account_id: str, snapshot: dict[str, Any]) -> None:
        """Store one reading; a repeat of the same value within a minute is skipped."""
        row = (time.time(), account_id, used_pct(snapshot),
               _text(snapshot.get("plan")), _text(snapshot.get("resets_at")))

        def run(db: sqlite3.Connection) -> None:
            last = db.execute(
                "SELECT ts, used_pct FROM quota_samples WHERE account_id = ? "
                "ORDER BY ts DESC LIMIT 1", (account_id,)).fetchone()
            if last is not None and row[0] - last["ts"] < MIN_SAMPLE_GAP \
                    and last["used_pct"] == row[2]:
                return
            db.execute(
                "INSERT INTO quota_samples (ts, account_id, used_pct, plan, resets_at) "
                "VALUES (?, ?, ?, ?, ?)", row)

        try:
            await asyncio.to_thread(self._call, run)
        except Exception:  # noqa: BLE001
            log.exception("failed to record a quota sample for %s", account_id)

    async def series(self, *, account_id: str | None = None,
                     days: float = 7.0) -> dict[str, Any]:
        since = time.time() - days * 86400
        sql = ("SELECT account_id, ts, used_pct, plan, resets_at FROM quota_samples "
               "WHERE ts >= ?" + (" AND account_id = ?" if account_id else "") +
               " ORDER BY ts")
        args: list[Any] = [since, *([account_id] if account_id else [])]

        def run(db: sqlite3.Connection) -> dict[str, Any]:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in db.execute(sql, args):
                grouped.setdefault(row["account_id"], []).append({
                    "ts": row["ts"], "used_pct": row["used_pct"],
                    "plan": row["plan"], "resets_at": row["resets_at"],
                })
            return {"since": since, "days": days, "accounts": grouped,
                    "forecast": {acc: forecast(rows) for acc, rows in grouped.items()}}

        try:
            return await asyncio.to_thread(self._call, run)
        except Exception:  # noqa: BLE001
            log.exception("failed to read quota history")
            return {"since": since, "days": days, "accounts": {}, "forecast": {}}

    async def prune(self, retention_days: float) -> int:
        if retention_days <= 0:
            return 0
        cutoff = time.time() - retention_days * 86400
        try:
            cur = await asyncio.to_thread(
                self._call, lambda db: db.execute(
                    "DELETE FROM quota_samples WHERE ts < ?", (cutoff,)))
        except Exception:  # noqa: BLE001
            log.exception("failed to prune quota history")
            return 0
        if cur.rowcount:
            log.info("pruned %d quota sample(s) older than %s days",
                     cur.rowcount, retention_days)
        return cur.rowcount
