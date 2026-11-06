"""Failure evidence for the browser driver.

When a muse.ai page stops matching ``dom.py`` the only useful question is "what did
the page actually look like?", and a log line cannot answer it. With
``MUSE2API_DEBUG_CAPTURE`` on, a failed request saves one directory per failure:

    data/debug/<timestamp>-<id>/
        meta.json   account, thread, error
        page.txt    location, title and the first part of the page text
        page.png    screenshot (best effort)

Captures are opt-in and bounded (count + age), because a screenshot of a chat page
contains whatever the account was working on.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .cdp import CDPSession

log = logging.getLogger(__name__)

# Enough to recognise the page, bounded so a huge thread cannot fill the disk.
_PAGE_TEXT_CHARS = 4000
_PAGE_STATE_JS = (
    "(() => ({ url: location.href, title: document.title,"
    f" text: (document.body ? document.body.innerText : '').slice(0, {_PAGE_TEXT_CHARS}) }}))()"
)


class Evidence:
    def __init__(self, root: Path, *, enabled: bool = False, max_dirs: int = 50,
                 retention_days: float = 3.0) -> None:
        self.root = root
        self.enabled = enabled
        self.max_dirs = max(1, max_dirs)
        self.retention_days = retention_days

    async def capture(self, session: CDPSession, *, reason: str,
                      meta: dict[str, Any] | None = None) -> Path | None:
        """Save one failure; returns its directory, or None when disabled or unwritable.

        Never raises: diagnostics must not turn a request failure into a different one.
        """
        if not self.enabled:
            return None
        try:
            directory = self.root / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            log.exception("cannot create evidence directory under %s", self.root)
            return None

        payload: dict[str, Any] = {"reason": reason, "captured_at": time.time(), **(meta or {})}
        written: list[str] = []
        with contextlib.suppress(Exception):
            page = await session.evaluate(_PAGE_STATE_JS, timeout=5.0) or {}
            payload["page"] = page
            (directory / "page.txt").write_text(
                f"{page.get('url', '')}\n{page.get('title', '')}\n\n{page.get('text', '')}\n",
                encoding="utf-8",
            )
            written.append("page.txt")
        with contextlib.suppress(Exception):
            shot = await session.send("Page.captureScreenshot", {"format": "png"}, timeout=10.0)
            if data := (shot or {}).get("data"):
                (directory / "page.png").write_bytes(base64.b64decode(data))
                written.append("page.png")
        payload["files"] = written
        with contextlib.suppress(OSError):
            (directory / "meta.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
        log.warning("browser failure evidence (%s) saved to %s", reason, directory)
        self.prune()
        return directory

    # ---- reads (the dashboard viewer) ----
    def _dirs(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted((d for d in self.root.iterdir() if d.is_dir()),
                      key=lambda d: d.name, reverse=True)

    @staticmethod
    def _meta(directory: Path) -> dict[str, Any] | None:
        try:
            return json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def captures(self, limit: int = 50) -> list[dict[str, Any]]:
        """Summaries of saved captures, newest first; unreadable ones are skipped."""
        out: list[dict[str, Any]] = []
        for directory in self._dirs()[:limit]:
            meta = self._meta(directory)
            if meta is None:
                continue
            out.append({
                "id": directory.name,
                "request_id": meta.get("request_id"),
                "reason": meta.get("reason"),
                "captured_at": meta.get("captured_at"),
                "account_id": meta.get("account_id"),
                "files": meta.get("files") or [],
                "screenshot": (directory / "page.png").is_file(),
            })
        return out

    def find(self, request_id: str) -> Path | None:
        if not request_id:
            return None
        for directory in self._dirs():
            meta = self._meta(directory)
            if meta is not None and meta.get("request_id") == request_id:
                return directory
        return None

    def load(self, request_id: str) -> dict[str, Any] | None:
        directory = self.find(request_id)
        if directory is None:
            return None
        page_text = ""
        with contextlib.suppress(OSError):
            page_text = (directory / "page.txt").read_text(encoding="utf-8")
        return {"id": directory.name, "meta": self._meta(directory) or {},
                "page_text": page_text,
                "screenshot": (directory / "page.png").is_file()}

    def screenshot(self, request_id: str) -> bytes | None:
        directory = self.find(request_id)
        if directory is None or not (directory / "page.png").is_file():
            return None
        try:
            return (directory / "page.png").read_bytes()
        except OSError:
            return None

    def prune(self) -> int:
        """Drop captures past the age limit, then the oldest beyond the count limit."""
        if not self.enabled or not self.root.is_dir():
            return 0
        directories = sorted((d for d in self.root.iterdir() if d.is_dir()), key=lambda d: d.name)
        removed = 0
        if self.retention_days > 0:
            cutoff = time.time() - self.retention_days * 86400
            for directory in list(directories):
                with contextlib.suppress(OSError):
                    if directory.stat().st_mtime < cutoff:
                        shutil.rmtree(directory, ignore_errors=True)
                        directories.remove(directory)
                        removed += 1
        for directory in directories[: max(0, len(directories) - self.max_dirs)]:
            shutil.rmtree(directory, ignore_errors=True)
            removed += 1
        return removed
