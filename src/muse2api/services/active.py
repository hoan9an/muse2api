"""In-flight ``/v1`` requests, so the dashboard can show them and abort one.

Only the long-running surfaces register here (chat and responses): image and video
calls already release the account before returning, and task-based media is
cancelled through ``/admin/tasks/{id}/cancel``. Aborting an entry sets that
request's own cancel event — exactly what a client disconnect does — so the driver
and the pool unwind through the path they already handle.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ActiveRequest:
    request_id: str
    path: str
    cancel: asyncio.Event
    model: str | None = None
    stream: bool = False
    started_at: float = field(default_factory=time.time)

    def view(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "path": self.path,
            "model": self.model,
            "stream": self.stream,
            "started_at": self.started_at,
            "elapsed_ms": int((time.time() - self.started_at) * 1000),
        }


class ActiveRequests:
    def __init__(self) -> None:
        self._items: dict[str, ActiveRequest] = {}

    def track(self, cancel: asyncio.Event, *, request_id: str | None, path: str,
              model: str | None = None, stream: bool = False) -> ActiveRequest:
        """Register a request; an empty id stays abortable from its own scope but is
        not listed, since there would be nothing to address it by."""
        item = ActiveRequest(request_id=request_id or "", path=path, cancel=cancel,
                             model=model, stream=stream)
        if item.request_id:
            self._items[item.request_id] = item
        return item

    def finish(self, item: ActiveRequest | str | None) -> None:
        if item is not None:
            self._items.pop(item if isinstance(item, str) else item.request_id, None)

    def abort(self, request_id: str) -> bool:
        item = self._items.get(request_id)
        if item is None:
            return False
        # Setting the event is enough: every route closes its stream on it already.
        item.cancel.set()
        return True

    def snapshot(self) -> list[dict[str, Any]]:
        return [item.view() for item in sorted(self._items.values(), key=lambda i: i.started_at)]

    def __len__(self) -> int:
        return len(self._items)
