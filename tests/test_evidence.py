from __future__ import annotations

import base64
import json
import os
import time

from muse2api.drivers.browser.evidence import Evidence


class _Session:
    def __init__(self, fail_shot: bool = False) -> None:
        self.fail_shot = fail_shot
        self.calls: list[str] = []

    async def evaluate(self, expr, **_):
        self.calls.append("evaluate")
        return {"url": "https://muse.ai/thread/abc", "title": "muse", "text": "hello page"}

    async def send(self, method, params=None, timeout=30):
        self.calls.append(method)
        if self.fail_shot:
            raise RuntimeError("no page")
        return {"data": base64.b64encode(b"\x89PNG\r\n").decode()}


async def test_capture_saves_meta_page_text_and_screenshot(tmp_path):
    store = Evidence(tmp_path / "debug", enabled=True)
    directory = await store.capture(_Session(), reason="cdp", meta={"account_id": "a0"})

    assert directory is not None and directory.is_dir()
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    assert meta["reason"] == "cdp"
    assert meta["account_id"] == "a0"
    assert meta["page"]["url"].endswith("/thread/abc")
    assert meta["files"] == ["page.txt", "page.png"]
    page_text = (directory / "page.txt").read_text(encoding="utf-8")
    assert page_text.startswith("https://muse.ai/thread/abc\nmuse\n")
    assert page_text.endswith("hello page\n")
    assert (directory / "page.png").read_bytes() == b"\x89PNG\r\n"


async def test_disabled_capture_touches_nothing(tmp_path):
    root = tmp_path / "debug"
    assert await Evidence(root, enabled=False).capture(_Session(), reason="cdp") is None
    assert not root.exists()


async def test_evidence_never_masks_the_original_failure(tmp_path):
    class _Broken:
        async def evaluate(self, expr, **_):
            raise RuntimeError("target closed")

        async def send(self, method, params=None, timeout=30):
            raise RuntimeError("target closed")

    directory = await Evidence(tmp_path / "debug", enabled=True).capture(_Broken(), reason="cdp")

    assert directory is not None and (directory / "meta.json").is_file()
    assert json.loads((directory / "meta.json").read_text())["files"] == []


async def test_prune_drops_aged_and_excess_captures(tmp_path):
    root = tmp_path / "debug"
    root.mkdir()
    aged = root / "20200101-000000-aged"
    aged.mkdir()
    stamp = time.time() - 10 * 86400
    os.utime(aged, (stamp, stamp))
    for i in range(1, 4):
        (root / f"2026010{i}-00000{i}-fresh").mkdir()

    removed = Evidence(root, enabled=True, max_dirs=2, retention_days=3).prune()

    assert removed == 2  # the aged directory plus the oldest beyond the cap
    assert not aged.exists()
    assert sorted(d.name for d in root.iterdir()) == [
        "20260102-000002-fresh", "20260103-000003-fresh",
    ]


async def test_driver_only_captures_when_enabled(tmp_path):
    from muse2api.config import Settings
    from muse2api.drivers.browser.driver import BrowserDriver, _Tab

    tab = _Tab("a0", "ctx", "target", _Session())
    off = BrowserDriver(Settings(_env_file=None, data_dir=tmp_path / "off"))
    await off._capture(tab, "cdp", RuntimeError("boom"))
    assert not (tmp_path / "off" / "debug").exists()

    on = BrowserDriver(Settings(_env_file=None, data_dir=tmp_path / "on", debug_capture=True))
    await on._capture(tab, "cdp", RuntimeError("boom"))
    assert any((tmp_path / "on" / "debug").iterdir())
