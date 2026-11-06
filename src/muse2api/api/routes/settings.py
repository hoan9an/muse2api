"""Admin settings: a redacted view of the effective config, plus live-editable knobs.

Only fields the app reads on every use are editable here — timeouts, pool
scheduling, retention and failure capture. Everything else (driver, ports, keys,
Chromium flags) still needs a restart, and ``GET /admin/config`` says so by only
listing the live ones in ``live_fields``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ...errors import InvalidRequest
from ...services.container import Services
from ..deps import get_services, require_admin_key

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_key)])

SECRET_FIELDS = ("api_key", "admin_key")

LIVE_FIELDS = (
    "account_cooldown",
    "account_max_concurrency",
    "affinity_ttl",
    "quota_low_threshold_pct",
    "debug_capture",
    "request_log_retention_days",
    "media_retention_days",
    "quota_history_retention_days",
    "chat_timeout",
    "first_token_timeout",
    "image_timeout",
    "video_timeout",
)


class RuntimeConfig(BaseModel):
    """The live-editable subset; ``None`` means "leave it alone"."""

    model_config = ConfigDict(extra="forbid")

    account_cooldown: float | None = Field(default=None, ge=1, le=86400)
    account_max_concurrency: int | None = Field(default=None, ge=1, le=64)
    affinity_ttl: float | None = Field(default=None, ge=0, le=30 * 86400)
    quota_low_threshold_pct: float | None = Field(default=None, ge=0, le=100)
    debug_capture: bool | None = None
    request_log_retention_days: float | None = Field(default=None, ge=1, le=3650)
    media_retention_days: float | None = Field(default=None, ge=0, le=3650)
    quota_history_retention_days: float | None = Field(default=None, ge=0, le=3650)
    chat_timeout: float | None = Field(default=None, ge=5, le=3600)
    first_token_timeout: float | None = Field(default=None, ge=5, le=3600)
    image_timeout: float | None = Field(default=None, ge=5, le=3600)
    video_timeout: float | None = Field(default=None, ge=5, le=7200)


def _mask(secret: Any) -> str:
    text = str(secret)
    return "••••" + text[-4:] if len(text) > 4 else "••••"


def _mask_url(url: str) -> str:
    """Drop credentials from a proxy URL: it may carry ``user:pass@``."""
    parts = urlsplit(url)
    if parts.username or parts.password:
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
    return url


def config_view(settings: Any) -> dict[str, Any]:
    data = settings.model_dump(mode="json")
    for field in SECRET_FIELDS:
        if data.get(field):
            data[field] = _mask(data[field])
    if data.get("browser_proxy"):
        data["browser_proxy"] = _mask_url(data["browser_proxy"])
    return {"data": data, "live_fields": list(LIVE_FIELDS),
            "restart_fields": sorted(set(data) - set(LIVE_FIELDS))}


@router.get("/config")
async def get_config(svc: Services = Depends(get_services)) -> dict[str, Any]:
    """The effective settings with secrets masked."""
    return config_view(svc.settings)


@router.patch("/config")
async def update_config(body: RuntimeConfig,
                        svc: Services = Depends(get_services)) -> dict[str, Any]:
    """Apply the live-editable settings now, without restarting the process."""
    changes = {key: value for key, value in body.model_dump().items() if value is not None}
    if not changes:
        raise InvalidRequest("no settings supplied")
    for key, value in changes.items():
        setattr(svc.settings, key, value)
    await svc.apply_runtime_config(changes)
    return {**config_view(svc.settings), "applied": changes}
