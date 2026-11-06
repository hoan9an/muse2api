from __future__ import annotations

import httpx

from muse2api.upstream import muse


def test_endpoints_derive_from_a_local_base_url():
    endpoints = muse.Endpoints.from_base("http://127.0.0.1:8080/")

    assert endpoints.site == "http://127.0.0.1:8080"
    assert endpoints.new_thread == "http://127.0.0.1:8080/thread/new"
    assert endpoints.session_api == "http://127.0.0.1:8080/api/session"
    assert endpoints.vm_wake_api == "http://127.0.0.1:8080/api/hatch/vm/wake"
    assert endpoints.host == "127.0.0.1"
    assert endpoints.cookie_domain == ".127.0.0.1"


def test_blank_means_the_default_site_and_a_bare_host_gets_https():
    assert muse.Endpoints.from_base(None).site == muse.DEFAULT_SITE
    assert muse.Endpoints.from_base("  ").site == muse.DEFAULT_SITE
    assert muse.Endpoints.from_base("muse.test").site == "https://muse.test"


async def test_renew_session_targets_the_configured_site(monkeypatch):
    seen: list[str] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"status": "assigned"})

    monkeypatch.setattr(
        muse.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )

    info = await muse.renew_session({"hatch_sess": "s"}, site="http://muse.test:9999",
                                    wake_vm=False)

    assert info.ok
    assert seen == ["http://muse.test:9999/api/session"]
