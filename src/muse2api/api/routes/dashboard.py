"""Admin dashboard: a single self-contained HTML page.

The page itself is public and reveals nothing. It exchanges the admin key once at
``POST /admin/session`` for an HttpOnly cookie and then calls ``/admin/*`` with
that; the key is never kept in browser storage.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

_PAGE = Path(__file__).resolve().parents[2] / "web" / "dashboard.html"


@lru_cache(maxsize=1)
def _html() -> str:
    return _PAGE.read_text(encoding="utf-8")


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(_html(), headers={"Cache-Control": "no-cache",
                                          "X-Frame-Options": "DENY",
                                          "Referrer-Policy": "no-referrer"})
