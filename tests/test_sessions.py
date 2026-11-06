from __future__ import annotations

from fastapi.testclient import TestClient

from muse2api.app import create_app
from muse2api.drivers.mock import MockDriver


def test_a_wrong_key_is_rejected(client):
    r = client.post("/admin/session", json={"key": "nope"})
    assert r.status_code == 401
    assert "set-cookie" not in r.headers


def test_sign_in_sets_an_httponly_cookie_and_unlocks_admin(client, admin):
    r = client.post("/admin/session", json={"key": "admin-key"})

    assert r.status_code == 200 and r.json()["authenticated"] is True
    cookie = r.headers["set-cookie"]
    assert "muse2api_session=" in cookie
    assert "HttpOnly" in cookie and "Path=/" in cookie
    # The cookie alone is enough, and the header keeps working too.
    assert client.get("/admin/status").status_code == 200
    assert client.get("/admin/status", headers=admin).status_code == 200


def test_sign_out_ends_the_session(client):
    client.post("/admin/session", json={"key": "admin-key"})
    assert client.delete("/admin/session").json() == {"authenticated": False}
    assert client.get("/admin/status").status_code == 401


def test_header_auth_works_without_signing_in(settings, admin):
    app = create_app(settings, MockDriver(delay=0))
    with TestClient(app) as fresh:
        assert fresh.get("/admin/status").status_code == 401
        assert fresh.get("/admin/status", headers=admin).status_code == 200
        assert fresh.get("/admin/session", headers=admin).json() == {"authenticated": True}


def test_a_forged_cookie_is_rejected(client):
    client.cookies.set("muse2api_session", "made-up")
    assert client.get("/admin/status").status_code == 401
