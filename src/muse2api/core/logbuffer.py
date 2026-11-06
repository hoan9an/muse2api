"""In-memory log ring buffer behind the dashboard's Logs page.

Deploying with docker compose puts the process output in ``docker compose logs``;
this keeps the recent lines reachable from the panel itself, without shell access,
and gives the request/account pages a place to point at when something fails.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any

MAX_RECORDS = 2000

LEVELS = {"debug": logging.DEBUG, "info": logging.INFO,
          "warning": logging.WARNING, "error": logging.ERROR}


class LogBuffer(logging.Handler):
    """Keeps the newest records; the dashboard polls them by increasing id."""

    def __init__(self, maxlen: int = MAX_RECORDS) -> None:
        super().__init__()
        self._items: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._next_id = 1

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            message = str(record.msg)
        with self._lock:
            self._items.append({
                "id": self._next_id,
                "ts": record.created,
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": message,
            })
            self._next_id += 1

    def records(self, *, after: int = 0, limit: int = 200,
                level: str | None = None) -> list[dict[str, Any]]:
        floor = LEVELS.get((level or "").lower(), 0)
        with self._lock:
            fresh = [item for item in self._items
                     if item["id"] > after and LEVELS.get(item["level"], 0) >= floor]
        return fresh[-limit:]

    def latest_id(self) -> int:
        with self._lock:
            return self._next_id - 1


LOG_BUFFER = LogBuffer()
# INFO by default: DEBUG would fill the ring in minutes with driver chatter.
LOG_BUFFER.setLevel(logging.INFO)
