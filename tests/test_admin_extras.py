from __future__ import annotations

import asyncio
import base64
import csv
import io

from muse2api.drivers.browser.evidence import Evidence


class _CaptureSession:
    async def evaluate(self, expr, **_):
        return {"url": "https://muse.ai/thread/abc", "title": "muse", "text": "hello page"}

    async def send(self, method, params=None, timeout=30):
        return {"data": base64.b64encode(b"\x89PNG").decode()}


def _chat(client, auth):
    return client.post("/v1/chat/completions", headers=auth,
                       json={"messages": [{"role": "user", "content": "hi"}]})


def test_export_csv_has_a_header_and_the_filtered_rows(client, auth, admin):
    assert _chat(client, auth).status_code == 200

    r = client.get("/admin/requests/export?format=csv", headers=admin)

    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert rows and rows[0]["path"] == "/v1/chat/completions"
    assert rows[0]["request_id"].startswith("req_")


def test_export_csv_applies_the_filters(client, auth, admin):
    _chat(client, auth)

    empty = client.get("/admin/requests/export?format=csv&status=5xx", headers=admin)

    assert list(csv.DictReader(io.StringIO(empty.text))) == []


def test_export_json_returns_the_same_rows(client, auth, admin):
    _chat(client, auth)

    r = client.get("/admin/requests/export?format=json&limit=10", headers=admin)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()[0]["method"] == "POST"


def test_drivers_without_evidence_export_nothing(client, admin):
    assert client.get("/admin/debug", headers=admin).json() == {"data": []}
    assert client.get("/admin/debug/req_none", headers=admin).status_code == 404


async def test_failure_capture_is_listed_and_served(client, admin):
    store = Evidence(client.app.state.services.settings.debug_dir, enabled=True)
    client.app.state.services.driver.evidence = store
    await store.capture(_CaptureSession(), reason="cdp",
                        meta={"request_id": "req_capture", "account_id": "a0"})

    listed = client.get("/admin/debug", headers=admin).json()["data"]
    assert [(c["request_id"], c["reason"], c["screenshot"]) for c in listed] == [
        ("req_capture", "cdp", True),
    ]

    detail = client.get("/admin/debug/req_capture", headers=admin).json()
    assert detail["meta"]["account_id"] == "a0"
    assert detail["page_text"].endswith("hello page\n")

    shot = client.get("/admin/debug/req_capture/screenshot", headers=admin)
    assert shot.status_code == 200
    assert shot.headers["content-type"] == "image/png"
    assert shot.content == b"\x89PNG"


def test_active_requests_can_be_listed_and_aborted(client, admin):
    svc = client.app.state.services
    cancel = asyncio.Event()
    item = svc.active.track(cancel, request_id="req_live", path="/v1/chat/completions",
                            model="muse-chat", stream=True)

    listed = client.get("/admin/active-requests", headers=admin).json()
    assert [r["request_id"] for r in listed["data"]] == ["req_live"]
    assert listed["count"] == 1
    assert listed["data"][0]["stream"] is True

    aborted = client.post("/admin/active-requests/req_live/abort", headers=admin)
    assert aborted.json() == {"aborted": "req_live"}
    assert cancel.is_set()

    svc.active.finish(item)
    assert client.get("/admin/active-requests", headers=admin).json()["data"] == []
    assert client.post("/admin/active-requests/req_live/abort", headers=admin).status_code == 404


def test_a_reading_is_stored_and_served_as_history(client, admin):
    client.post("/admin/accounts", headers=admin,
                json={"label": "main", "cookies": {"hatch_sess": "s"}})
    account_id = client.get("/admin/accounts", headers=admin).json()["data"][0]["id"]

    reading = client.get(f"/admin/accounts/{account_id}/quota", headers=admin)
    assert reading.status_code == 200

    history = client.get(f"/admin/quota/history?account_id={account_id}", headers=admin).json()
    assert history["accounts"][account_id]
    assert history["forecast"][account_id]["pct"] is not None
