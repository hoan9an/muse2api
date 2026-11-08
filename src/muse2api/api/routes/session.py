"""Dashboard sign-in: exchange the dashboard password (or admin key) for a session cookie.

Keeping the secret out of ``localStorage`` means an XSS in the panel (or another tab
on the same origin) cannot read it, and the cookie is not reachable from JS at all.
Header/Bearer auth keeps working unchanged for scripts and the extension.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from ...errors import Unauthorized
from ...services.container import Services
from ...services.sessions import COOKIE_NAME
from ..deps import get_services, require_admin_key

router = APIRouter(prefix="/admin", tags=["admin"])


class LoginRequest(BaseModel):
    key: str


def _cookie_secure(request: Request) -> bool:
    # A homelab usually serves plain http on the LAN; mark the cookie Secure only
    # when the browser reached us over https, or it would never be sent back.
    return request.url.scheme == "https"


@router.post("/session")
async def login(body: LoginRequest, request: Request, response: Response,
                svc: Services = Depends(get_services)) -> dict:
    # The dashboard password is the normal way in; the admin key keeps working so
    # scripts and the extension can still exchange it for a cookie.
    candidates = (svc.settings.dashboard_password, svc.settings.effective_admin_key)
    if not any(c and hmac.compare_digest(body.key.encode(), c.encode()) for c in candidates):
        raise Unauthorized("invalid password or admin key")
    session = svc.sessions.create()
    response.set_cookie(COOKIE_NAME, session.token, max_age=int(svc.sessions.ttl),
                        httponly=True, samesite="lax", secure=_cookie_secure(request), path="/")
    return {"authenticated": True, "expires_in": int(svc.sessions.ttl)}


@router.get("/session", dependencies=[Depends(require_admin_key)])
async def whoami() -> dict:
    """Reachability probe for the panel: 200 only with a valid cookie or key."""
    return {"authenticated": True}


@router.delete("/session")
async def logout(request: Request, response: Response,
                 svc: Services = Depends(get_services)) -> dict:
    # No auth dependency: signing out must work even after the session expired.
    svc.sessions.drop(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"authenticated": False}
