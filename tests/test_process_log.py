from __future__ import annotations

import logging


def _lines(client, admin, **params):
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return client.get(f"/admin/process-log?{query}", headers=admin).json()


def test_lines_are_returned_with_an_id_cursor(client, admin):
    logging.getLogger("muse2api.test").warning("needle-one")

    first = _lines(client, admin, limit=1000)
    messages = [item["message"] for item in first["items"]]
    assert "needle-one" in messages
    assert first["items"][-1]["id"] == first["next"]
    assert {"id", "ts", "level", "logger", "message"} <= set(first["items"][-1])

    logging.getLogger("muse2api.test").warning("needle-two")
    later = _lines(client, admin, after=first["next"])
    later_messages = [item["message"] for item in later["items"]]
    assert "needle-two" in later_messages
    assert "needle-one" not in later_messages


def test_level_filter_drops_lower_levels(client, admin):
    logger = logging.getLogger("muse2api.test")
    logger.info("quiet-info")
    logger.error("loud-error")

    res = _lines(client, admin, level="error", limit=1000)

    messages = [item["message"] for item in res["items"]]
    assert "loud-error" in messages
    assert "quiet-info" not in messages
