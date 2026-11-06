from __future__ import annotations

import time

from fastapi.testclient import TestClient

from muse2api.accounts import Account, AccountStatus
from muse2api.app import create_app
from muse2api.drivers.mock import MockDriver


def test_response_and_request_log_share_a_request_id(client, auth, admin):
    r = client.post("/v1/chat/completions", headers=auth,
                    json={"messages": [{"role": "user", "content": "hi"}]})

    assert r.status_code == 200
    request_id = r.headers["x-request-id"]
    assert request_id.startswith("req_") and len(request_id) == 24
    rows = client.get("/admin/requests", headers=admin).json()["data"]
    assert rows[0]["request_id"] == request_id


def test_every_request_gets_its_own_id(client, auth):
    first = client.get("/v1/models", headers=auth).headers["x-request-id"]
    second = client.get("/v1/models", headers=auth).headers["x-request-id"]
    assert first != second


class _PooledDriver(MockDriver):
    requires_account = True


def test_error_body_carries_the_request_id(settings, auth):
    settings.pool_acquire_timeout = 0.05
    app = create_app(settings, _PooledDriver(delay=0))
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", headers=auth,
                        json={"messages": [{"role": "user", "content": "hi"}]})

    assert r.status_code == 503
    error = r.json()["error"]
    assert error["code"] == "no_account_available"
    assert error["request_id"] == r.headers["x-request-id"]


def test_a_cooling_pool_answers_with_retry_after(settings, auth):
    settings.pool_acquire_timeout = 0.05
    app = create_app(settings, _PooledDriver(delay=0))
    with TestClient(app) as client:
        pool = app.state.services.pool
        pool._accounts["a0"] = Account(
            id="a0", cookies={"hatch_sess": "s"}, status=AccountStatus.COOLING,
            cooldown_until=time.time() + 30,
        )
        r = client.post("/v1/chat/completions", headers=auth,
                        json={"messages": [{"role": "user", "content": "hi"}]})

    assert r.status_code == 503
    # A hint, not a promise: the account rests for ~30s.
    assert 1 <= int(r.headers["retry-after"]) <= 30


def test_an_empty_pool_has_no_retry_after(settings, auth):
    settings.pool_acquire_timeout = 0.05
    app = create_app(settings, _PooledDriver(delay=0))
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", headers=auth,
                        json={"messages": [{"role": "user", "content": "hi"}]})

    assert r.status_code == 503
    assert "retry-after" not in r.headers
