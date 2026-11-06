"""Reading a weekly-quota snapshot, shared by the account pool and the quota history."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def used_pct(snapshot: Any) -> float | None:
    """Percentage of the weekly quota used, or ``None`` when the snapshot has no reading.

    Drivers differ: the browser driver reports ``weekly_used_pct``, other sources may
    only carry ``messages_used``/``messages_limit``. A snapshot without either must
    read as unknown (``None``), never as 0% — otherwise a missing reading would look
    like an empty account.
    """
    if not isinstance(snapshot, Mapping):
        return None
    pct = snapshot.get("weekly_used_pct")
    if isinstance(pct, (int, float)):
        return float(pct)
    used, limit = snapshot.get("messages_used"), snapshot.get("messages_limit")
    if isinstance(used, (int, float)) and isinstance(limit, (int, float)) and limit > 0:
        return used / limit * 100
    return None
