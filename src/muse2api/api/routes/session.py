"""Dashboard sign-in: exchange the admin key once for an HttpOnly session cookie.

Keeping the key out of ``localStorage`` means an XSS in the panel (or another tab
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
    expected = svc.settings.effective_admin_key
    if not expected or not hmac.compare_digest(body.key.encode(), expected.encode()):
        raise Unauthorized("invalid admin key")
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
