"""OpenAI-compatible request schemas. Unknown fields are accepted and ignored so that
clients sending newer parameters keep working."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="allow")


class ChatMessage(_Lenient):
    role: str
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None


class ChatCompletionRequest(_Lenient):
    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    user: str | None = None
    # Accepted for compatibility; muse.ai does not expose sampling or tool controls.
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None


class ImageGenerationRequest(_Lenient):
    prompt: str = Field(min_length=1)
    model: str | None = None
    n: int = Field(default=1, ge=1, le=4)
    size: str | None = None
    response_format: Literal["url", "b64_json"] = "url"
    # Same values as OpenAI gpt-image-1. "transparent" cuts the subject out locally
    # and returns an RGBA PNG; "auto"/"opaque" leave the image as generated.
    background: Literal["auto", "opaque", "transparent"] | None = None
    image: str | list[str] | None = Field(
        default=None, description="Legacy reference image(s); data URL, http(s) URL or base64")
    images: str | list[str] | None = None
    video: str | list[str] | None = None
    videos: str | list[str] | None = None
    audio: str | list[str] | None = None
    audios: str | list[str] | None = None
    file: str | list[str] | None = None
    files: str | list[str] | None = None
    # A sync call can outlive a proxy's timeout (Cloudflare cuts at ~100 s with 524), so
    # "async": true returns a task to poll at GET /v1/images/generations/{task_id}.
    async_: bool = Field(default=False, alias="async")
    user: str | None = None


class VideoCreateRequest(_Lenient):
    prompt: str = Field(min_length=1)
    model: str | None = None
    size: str | None = None
    duration: int | None = Field(default=None, ge=1, le=60)
    seconds: int | None = Field(default=None, ge=1, le=60)
    image: str | None = Field(default=None, description="First frame as data URL or http(s) URL")
    input_reference: str | None = None
    references: str | list[str] | None = None
    images: str | list[str] | None = None
    videos: str | list[str] | None = None
    audios: str | list[str] | None = None
    files: str | list[str] | None = None


class AccountProxyInput(BaseModel):
    """Proxy supplied by an admin. Credentials may also be inline in ``server``."""

    server: str = Field(min_length=1, max_length=500)
    username: str = Field(default="", max_length=255)
    password: str | None = Field(default=None, max_length=255)


class AccountCreate(BaseModel):
    label: str = ""
    # Three accepted formats (TODO D1):
    #   1. dict {"name": "value"}
    #   2. raw "Cookie" request header string "a=1; b=2"
    #   3. list of cookie-export entries (e.g. EditThisCookie):
    #      [{"name": ..., "value": ..., "expirationDate": ...}, ...]
    cookies: dict[str, str] | str | list[dict[str, Any]]
    cookie_expires: dict[str, int] = Field(default_factory=dict)
    enabled: bool = True
    proxy: AccountProxyInput | None = None


class AccountsImportRequest(BaseModel):
    """Bulk cookie import: every entry is validated like AccountCreate; entries that
    fail are reported individually without aborting the batch."""
    accounts: list[AccountCreate] = Field(min_length=1)


class AccountUpdate(BaseModel):
    label: str | None = None
    enabled: bool | None = None
    cookies: dict[str, str] | None = None
    cookie_expires: dict[str, int] | None = None
    # An explicit null removes the proxy; omitting the field leaves it alone.
    proxy: AccountProxyInput | None = None


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=500)
    # Unix seconds; omit or null for a key that never expires.
    expires_at: float | None = Field(default=None, ge=0)


class KeyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=500)
    revoked: bool | None = None
    # Send null explicitly to remove an expiry; omit the field to leave it alone.
    expires_at: float | None = Field(default=None, ge=0)
