"""Admin dashboard: a single self-contained HTML page.

The page itself is public and reveals nothing. It exchanges the admin key once at
``POST /admin/session`` for an HttpOnly cookie and then calls ``/admin/*`` with
that; the key is never kept in browser storage.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter(tags=["dashboard"])

_PAGE = Path(__file__).resolve().parents[2] / "web" / "dashboard.html"
_FONTS = _PAGE.parent / "fonts"
_FONT_FILES = frozenset({"lexend-latin.woff2", "lexend-latin-ext.woff2", "lexend-vietnamese.woff2"})


@lru_cache(maxsize=1)
def _html() -> str:
    return _PAGE.read_text(encoding="utf-8")


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(_html(), headers={"Cache-Control": "no-cache",
                                          "X-Frame-Options": "DENY",
                                          "Referrer-Policy": "no-referrer"})


@router.get("/dashboard/fonts/{name}", include_in_schema=False)
async def dashboard_font(name: str) -> FileResponse:
    if name not in _FONT_FILES:
        raise HTTPException(status_code=404)
    return FileResponse(_FONTS / name, media_type="font/woff2",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})
