"""Key lifecycle: expiry, rotation and clearing an expiry."""

from __future__ import annotations

import time


def _issue(client, admin, **extra) -> dict:
    r = client.post("/admin/keys", headers=admin, json={"name": "lifecycle", **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_expired_key_is_rejected(client, admin):
    body = _issue(client, admin, expires_at=time.time() - 60)
    headers = {"Authorization": f"Bearer {body['api_key']}"}
    assert client.get("/v1/models", headers=headers).status_code == 401

    listed = {k["id"]: k for k in client.get("/admin/keys", headers=admin).json()["data"]}
    assert listed[body["key"]["id"]]["expired"] is True


def test_future_expiry_keeps_working(client, admin):
    body = _issue(client, admin, expires_at=time.time() + 3600)
    headers = {"Authorization": f"Bearer {body['api_key']}"}
    assert client.get("/v1/models", headers=headers).status_code == 200
    listed = {k["id"]: k for k in client.get("/admin/keys", headers=admin).json()["data"]}
    assert listed[body["key"]["id"]]["expired"] is False


def test_rotate_replaces_the_secret(client, admin):
    body = _issue(client, admin)
    key_id = body["key"]["id"]
    old = {"Authorization": f"Bearer {body['api_key']}"}
    assert client.get("/v1/models", headers=old).status_code == 200

    rotated = client.post(f"/admin/keys/{key_id}/rotate", headers=admin).json()
    assert rotated["key"]["id"] == key_id
    assert rotated["key"]["prefix"] != body["key"]["prefix"]
    assert client.get("/v1/models", headers=old).status_code == 401
    new = {"Authorization": f"Bearer {rotated['api_key']}"}
    assert client.get("/v1/models", headers=new).status_code == 200


def test_rotate_unknown_key_is_404(client, admin):
    assert client.post("/admin/keys/key_missing/rotate", headers=admin).status_code == 404


def test_expiry_can_be_cleared(client, admin):
    body = _issue(client, admin, expires_at=time.time() - 60)
    path = f"/admin/keys/{body['key']['id']}"
    patched = client.patch(path, headers=admin, json={"expires_at": None})
    assert patched.status_code == 200
    assert patched.json()["key"]["expires_at"] is None
    headers = {"Authorization": f"Bearer {body['api_key']}"}
    assert client.get("/v1/models", headers=headers).status_code == 200


def test_rename_leaves_expiry_untouched(client, admin):
    expires = time.time() + 3600
    body = _issue(client, admin, expires_at=expires)
    patched = client.patch(f"/admin/keys/{body['key']['id']}", headers=admin,
                           json={"name": "renamed"}).json()
    assert patched["key"]["name"] == "renamed"
    assert patched["key"]["expires_at"] == expires
