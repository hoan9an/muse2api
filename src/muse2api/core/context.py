"""Per-request context shared by the middleware, the routes and the drivers.

The logging middleware mints one id per ``/v1/*`` request; deeper layers need it
without threading a parameter through every call (the browser driver, for example,
writes it into failure evidence). A ``ContextVar`` keeps it task-local, and it is
imported from ``core`` so drivers never depend on the service layer.
"""

from __future__ import annotations

from contextvars import ContextVar

current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)
