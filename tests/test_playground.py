"""The panel's model catalog and playground endpoints."""

from __future__ import annotations

from muse2api.core.models import ALIASES, MODELS


def test_model_catalog_lists_every_model(client, admin):
    res = client.get("/admin/models", headers=admin)
    assert res.status_code == 200
    body = res.json()
    assert {m["id"] for m in body["data"]} == set(MODELS)
    assert {a["id"] for a in body["aliases"]} == set(ALIASES)
    assert all("requests" in m and "errors" in m for m in body["data"])


def test_model_catalog_counts_traffic(client, admin, auth):
    client.post(
        "/v1/chat/completions",
        headers=auth,
        json={"model": "muse-chat", "messages": [{"role": "user", "content": "hi"}]},
    )
    body = client.get("/admin/models?window=24h", headers=admin).json()
    chat = next(m for m in body["data"] if m["id"] == "muse-chat")
    assert chat["requests"] >= 1


def test_playground_requires_admin_key(client):
    assert client.post("/admin/playground", json={"prompt": "hi"}).status_code == 401


def test_playground_runs_a_chat_turn(client, admin):
    res = client.post("/admin/playground", headers=admin,
                      json={"prompt": "say hello", "model": "muse-chat"})
    assert res.status_code == 200
    body = res.json()
    assert body["model"] == "muse-chat"
    assert body["content"]
    assert body["chars"] == len(body["content"])
    assert body["latency_ms"] >= 0


def test_playground_rejects_blank_prompt(client, admin):
    # The app maps request validation failures to OpenAI-style 400s.
    res = client.post("/admin/playground", headers=admin, json={"prompt": ""})
    assert res.status_code == 400
    assert res.json()["error"]["type"] == "invalid_request_error"


def test_playground_falls_back_to_the_chat_model(client, admin):
    res = client.post("/admin/playground", headers=admin,
                      json={"prompt": "hi", "model": "not-a-model"})
    assert res.status_code == 200
    assert res.json()["model"] == "muse-chat"


def test_playground_rejects_an_image_model(client, admin):
    res = client.post("/admin/playground", headers=admin,
                      json={"prompt": "hi", "model": "muse-image"})
    assert res.status_code == 400
    assert res.json()["error"]["type"] == "invalid_request_error"
