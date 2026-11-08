"""Local media storage for generated images/videos and helpers for image inputs."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import mimetypes
import re
import socket
import time
import urllib.parse
import uuid
from pathlib import Path

import httpx

from ..errors import InvalidRequest, NotFound

_EXT_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-]+\.[A-Za-z0-9]+$")


class MediaStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, data: bytes, mime: str, prefix: str = "m") -> str:
        ext = _EXT_BY_MIME.get(mime.split(";")[0].strip().lower(), ".bin")
        name = f"{prefix}_{uuid.uuid4().hex[:16]}{ext}"
        (self.root / name).write_bytes(data)
        return name

    def path_of(self, name: str) -> Path:
        if not _SAFE_NAME.match(name):
            raise NotFound("media not found")
        p = self.root / name
        if not p.is_file():
            raise NotFound("media not found")
        return p

    def prune(self, max_age_seconds: float) -> int:
        """Delete stored media files older than ``max_age_seconds``.

        Only removes files whose names match the store's safe pattern, so a
        foreign file dropped into the directory is never touched. Returns the
        number of deleted files.
        """
        if max_age_seconds <= 0:
            return 0
        cutoff = time.time() - max_age_seconds
        removed = 0
        for child in self.root.iterdir():
            if (
                child.is_file()
                and _SAFE_NAME.match(child.name)
                and child.stat().st_mtime < cutoff
            ):
                child.unlink()
                removed += 1
        return removed

    @staticmethod
    def mime_of(name: str) -> str:
        return mimetypes.guess_type(name)[0] or "application/octet-stream"


MAX_REFERENCE_ITEMS = 10
MAX_REFERENCE_BYTES = 40 * 1024 * 1024  # per attachment
MAX_REFERENCE_TOTAL_BYTES = 100 * 1024 * 1024  # per request
_MAX_FETCH_BYTES = MAX_REFERENCE_BYTES
_MAX_REDIRECTS = 5
_DEFAULT_MIME = {
    "image": "image/png",
    "video": "video/mp4",
    "audio": "audio/mpeg",
    "file": "application/octet-stream",
}


def _label(kind: str) -> str:
    return "image_url" if kind == "image" else f"{kind}_url"


def _assert_public_url(url: str, *, kind: str = "image") -> str:
    """Reject non-http(s) URLs and hosts resolving to non-public IPs (SSRF guard)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise InvalidRequest(f"{_label(kind)} must be a public http(s) URL")
    return url


async def _assert_public_host(hostname: str, *, kind: str = "image") -> None:
    """Reject hostnames resolving to non-public IPs (SSRF guard)."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            hostname, None, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise InvalidRequest(f"{_label(kind)} host does not resolve") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise InvalidRequest(f"{_label(kind)} must resolve to a public IP address")


async def _fetch_public_media(url: str, *, timeout: float, kind: str = "image") -> tuple[bytes, str]:
    """Fetch a reference URL with manual redirect validation and a size cap."""
    label = _label(kind)
    url = _assert_public_url(url, kind=kind)
    await _assert_public_host(urllib.parse.urlparse(url).hostname or "", kind=kind)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            async with client.stream("GET", url) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise InvalidRequest(f"{label} redirect missing location")
                    url = _assert_public_url(urllib.parse.urljoin(url, location), kind=kind)
                    await _assert_public_host(urllib.parse.urlparse(url).hostname or "", kind=kind)
                    continue
                resp.raise_for_status()
                mime = resp.headers.get("content-type", _DEFAULT_MIME[kind]).split(";")[0]
                body = bytearray()
                async for chunk in resp.aiter_bytes(65536):
                    body.extend(chunk)
                    if len(body) > _MAX_FETCH_BYTES:
                        raise InvalidRequest(f"{label} response exceeds 40 MiB")
                return bytes(body), mime
        raise InvalidRequest(f"{label} exceeded redirect limit")


async def load_media_ref(ref: str, *, kind: str = "image",
                         timeout: float = 20.0) -> tuple[bytes, str]:
    """Decode a data URL or download an http(s) URL. Returns ``(bytes, mime)``.

    ``kind`` is one of image/video/audio/file and only selects the default MIME
    for raw base64; routes enforce the MIME family where it matters.
    """
    ref = ref.strip()
    if not ref:
        raise InvalidRequest("reference media must not be empty")
    if ref.startswith("data:"):
        header, _, payload = ref.partition(",")
        mime = header[5:].split(";")[0] or _DEFAULT_MIME[kind]
        try:
            data = base64.b64decode(payload)
        except ValueError as exc:
            raise InvalidRequest("invalid base64 media data") from exc
    elif ref.startswith(("http://", "https://")):
        data, mime = await _fetch_public_media(ref, timeout=timeout, kind=kind)
    else:
        try:
            data = base64.b64decode(ref, validate=True)
        except ValueError as exc:
            raise InvalidRequest("media must be a data URL, http(s) URL or base64 string") from exc
        mime = _DEFAULT_MIME[kind]
    if len(data) > MAX_REFERENCE_BYTES:
        raise InvalidRequest(f"each reference media item is limited to {MAX_REFERENCE_BYTES // (1024 * 1024)} MiB")
    return data, mime


async def load_image_ref(ref: str, *, timeout: float = 20.0) -> tuple[bytes, str]:
    return await load_media_ref(ref, kind="image", timeout=timeout)
