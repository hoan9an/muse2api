from __future__ import annotations


def test_config_view_masks_the_keys(client, admin):
    response = client.get("/admin/config", headers=admin)

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["api_key"].startswith("••••")
    assert body["data"]["admin_key"].startswith("••••")
    assert "test-key" not in response.text
    assert "admin-key" not in response.text
    assert "account_cooldown" in body["live_fields"]
    assert "driver" in body["restart_fields"]


def test_patch_applies_a_live_setting(client, admin):
    svc = client.app.state.services

    body = client.patch("/admin/config", headers=admin,
                        json={"account_cooldown": 5, "debug_capture": True}).json()

    assert body["applied"] == {"account_cooldown": 5.0, "debug_capture": True}
    assert svc.settings.account_cooldown == 5.0
    assert svc.settings.debug_capture is True
    assert svc.pool.cooldown == 5.0


def test_patch_reaches_the_affinity_strategy(client, admin):
    svc = client.app.state.services

    client.patch("/admin/config", headers=admin,
                 json={"affinity_ttl": 120, "quota_low_threshold_pct": 50})

    assert svc.pool.strategy._ttl == 120
    assert svc.pool.strategy._low_quota_pct == 50


def test_patch_rejects_out_of_range_and_unknown_fields(client, admin):
    assert client.patch("/admin/config", headers=admin,
                        json={"account_cooldown": 0}).status_code == 400
    assert client.patch("/admin/config", headers=admin,
                        json={"not_a_setting": 1}).status_code == 400


def test_an_empty_patch_is_an_error(client, admin):
    r = client.patch("/admin/config", headers=admin, json={})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


def test_config_view_requires_admin(client, auth):
    assert client.get("/admin/config", headers=auth).status_code == 401
