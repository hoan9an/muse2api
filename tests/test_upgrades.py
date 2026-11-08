from __future__ import annotations

import base64
import socketserver
import threading

from muse2api.accounts.proxy import AccountProxy, normalize_proxy, proxy_from_input
from muse2api.api.routes import admin as admin_routes
from muse2api.core.models import resolve_model
from muse2api.drivers.browser.proxy_relay import ProxyRelay


def _account(client, admin, proxy=None):
    body = {"label": "proxy", "cookies": {"hatch_sess": "secret"}}
    if proxy is not None:
        body["proxy"] = proxy
    response = client.post("/admin/accounts", headers=admin, json=body)
    assert response.status_code == 200, response.text
    return response.json()["account"]


def test_proxy_normalization_moves_credentials_out_of_the_url():
    proxy = normalize_proxy("socks5h://user:p%40ss@[::1]:1080")
    assert proxy.server == "socks5h://[::1]:1080"
    assert proxy.username == "user" and proxy.password == "p@ss"
    assert proxy.public() == {"server": "socks5h://[::1]:1080", "username": "user",
                             "has_password": True}
    assert "p%40ss" in proxy.httpx_url() and proxy.httpx_url().startswith("socks5://")


def test_proxy_patch_keeps_password_only_for_the_same_route():
    current = AccountProxy(server="http://127.0.0.1:8080", username="u", password="secret")
    kept = proxy_from_input("http://127.0.0.1:8080", "u", "", existing=current)
    assert kept.password == "secret"
    changed = proxy_from_input("http://127.0.0.1:8081", "u", "", existing=current)
    assert changed.password == ""


def test_admin_account_masks_proxy_password(client, admin):
    account = _account(client, admin, {"server": "http://user:secret@127.0.0.1:8080"})
    assert account["proxy"] == {"server": "http://127.0.0.1:8080", "username": "user",
                                "has_password": True}
    listed = client.get("/admin/accounts", headers=admin)
    assert "secret" not in listed.text
    patched = client.patch(f"/admin/accounts/{account['id']}", headers=admin,
                           json={"proxy": {"server": "http://127.0.0.1:8080",
                                           "username": "user"}})
    assert patched.status_code == 200
    assert patched.json()["account"]["proxy"]["has_password"] is True
    changed = client.patch(f"/admin/accounts/{account['id']}", headers=admin,
                           json={"proxy": {"server": "socks5://127.0.0.1:1080",
                                           "username": "user"}})
    assert changed.json()["account"]["proxy"]["has_password"] is False


def test_proxy_test_endpoint_draft_and_save(client, admin, monkeypatch):
    account = _account(client, admin, {"server": "http://127.0.0.1:8080"})
    calls = []

    async def fake_probe(proxy, acc, svc, **_):
        calls.append(proxy.server)
        return {"ok": True, "proxy_reachable": True, "exit_ip": "203.0.113.9",
                "session_ok": True, "workspace_status": "ready",
                "checked_at": 1.0}

    monkeypatch.setattr(admin_routes, "_probe_proxy", fake_probe)
    saved = client.post(f"/admin/accounts/{account['id']}/proxy/test", headers=admin)
    assert saved.status_code == 200
    assert saved.json()["tested_saved_proxy"] is True
    assert calls == ["http://127.0.0.1:8080"]
    listed = client.get("/admin/accounts", headers=admin).json()["data"][0]
    assert listed["meta"]["proxy_status"]["exit_ip"] == "203.0.113.9"

    draft = client.post(f"/admin/accounts/{account['id']}/proxy/test", headers=admin,
                        json={"server": "socks5://127.0.0.1:1081"})
    assert draft.status_code == 200
    assert draft.json()["tested_saved_proxy"] is False
    assert calls[-1] == "socks5://127.0.0.1:1081"
    still_saved = client.get("/admin/accounts", headers=admin).json()["data"][0]
    assert still_saved["proxy"]["server"] == "http://127.0.0.1:8080"
    assert still_saved["meta"]["proxy_status"]["exit_ip"] == "203.0.113.9"


def test_proxy_test_returns_conflict_when_account_is_busy(client, admin, monkeypatch):
    account = _account(client, admin, {"server": "http://127.0.0.1:8080"})
    monkeypatch.setattr(client.app.state.services.pool, "is_busy", lambda _: True)
    response = client.post(f"/admin/accounts/{account['id']}/proxy/test", headers=admin)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


class _ConnectProxy(socketserver.BaseRequestHandler):
    def handle(self):
        head = bytearray()
        while not head.endswith(b"\r\n\r\n"):
            chunk = self.request.recv(1)
            if not chunk:
                return
            head.extend(chunk)
        self.server.seen = bytes(head).decode("latin-1")
        self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")


def test_proxy_relay_forwards_connect_with_http_basic_auth():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _ConnectProxy)
    server.daemon_threads = True
    server.seen = ""
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    relay = None
    try:
        relay = ProxyRelay(AccountProxy(server=f"http://127.0.0.1:{server.server_address[1]}",
                                        username="user", password="secret"))
        sock = relay.connect("example.com", 443)
        sock.close()
        assert server.seen.startswith("CONNECT example.com:443 HTTP/1.1\r\n")
        expected = base64.b64encode(b"user:secret").decode()
        assert f"Proxy-Authorization: Basic {expected}" in server.seen
    finally:
        if relay:
            relay.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_image_reference_accepts_all_media_kinds(client, auth, monkeypatch):
    seen = []
    gateway = client.app.state.services.gateway
    original = gateway.generate_image

    async def spy(req):
        seen.append(req)
        return await original(req)

    monkeypatch.setattr(gateway, "generate_image", spy)
    data = {
        "prompt": "mix these",
        "images": "data:image/png;base64," + base64.b64encode(b"i").decode(),
        "video": "data:video/mp4;base64," + base64.b64encode(b"v").decode(),
        "audio": "data:audio/mpeg;base64," + base64.b64encode(b"a").decode(),
        "file": "data:application/pdf;base64," + base64.b64encode(b"f").decode(),
    }
    response = client.post("/v1/images/generations", headers=auth, json=data)
    assert response.status_code == 200, response.text
    assert [ref.kind for ref in seen[0].reference_images] == ["image", "video", "audio", "file"]


def test_image_edit_accepts_video_audio_and_file_uploads(client, auth, monkeypatch):
    seen = []
    gateway = client.app.state.services.gateway
    original = gateway.generate_image

    async def spy(req):
        seen.append(req)
        return await original(req)

    monkeypatch.setattr(gateway, "generate_image", spy)
    response = client.post(
        "/v1/images/edits", headers=auth, data={"prompt": "collage"},
        files=[("video", ("clip.mp4", b"v", "video/mp4")),
               ("audio", ("voice.mp3", b"a", "audio/mpeg")),
               ("file", ("notes.pdf", b"f", "application/pdf"))],
    )
    assert response.status_code == 200, response.text
    assert [ref.kind for ref in seen[0].reference_images] == ["video", "audio", "file"]


def test_video_references_are_loaded(client, auth):
    body = {
        "prompt": "animate", "duration": 3,
        "references": "data:image/png;base64," + base64.b64encode(b"i").decode(),
        "videos": "data:video/mp4;base64," + base64.b64encode(b"v").decode(),
    }
    response = client.post("/v1/videos", headers=auth, json=body)
    assert response.status_code == 200, response.text
    task_id = response.json()["id"]
    task = client.app.state.services.tasks.get(task_id)
    assert task.request["references"] == 2
    assert task.request["reference_kinds"] == ["file", "video"]


def test_async_image_idempotency_key_reuses_the_task_and_rejects_other_bodies(client, auth):
    headers = {**auth, "Idempotency-Key": "same-key"}
    body = {"prompt": "stable fox", "async": True}
    first = client.post("/v1/images/generations", headers=headers, json=body)
    second = client.post("/v1/images/generations", headers=headers, json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    different = client.post("/v1/images/generations", headers=headers,
                            json={"prompt": "different fox", "async": True})
    assert different.status_code == 409
    assert different.json()["error"]["code"] == "conflict"


def test_muse_spark_and_popular_aliases_resolve():
    assert resolve_model("muse-spark", "chat").id == "muse-chat"
    assert resolve_model("claude-sonnet-4", "chat").id == "muse-chat"
    assert resolve_model("gemini-2.5-flash-image", "image").id == "muse-image"
