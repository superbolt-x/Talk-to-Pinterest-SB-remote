"""Tests for money conversion and bookmark pagination (HTTP mocked)."""

import responses

from pinloop.api import client as client_mod
from pinloop.api.client import from_micro, get_paginated, to_micro


def test_money_roundtrip():
    assert to_micro(49.99) == 49_990_000
    assert from_micro(49_990_000) == 49.99
    assert to_micro(None) is None
    assert from_micro(None) is None
    assert from_micro("") is None


@responses.activate
def test_pagination_follows_bookmark(config, monkeypatch):
    monkeypatch.setattr(client_mod, "get_access_token", lambda cfg, **k: "tok")

    base = "https://api.pinterest.com/v5/boards"
    responses.add(
        responses.GET, base,
        json={"items": [{"id": "1"}, {"id": "2"}], "bookmark": "PAGE2"},
        status=200,
    )
    responses.add(
        responses.GET, base,
        json={"items": [{"id": "3"}], "bookmark": None},
        status=200,
    )

    items = get_paginated(config, "/boards")
    assert [i["id"] for i in items] == ["1", "2", "3"]
    # Second request carried the bookmark from the first response.
    assert "bookmark=PAGE2" in responses.calls[1].request.url


@responses.activate
def test_max_items_truncates(config, monkeypatch):
    monkeypatch.setattr(client_mod, "get_access_token", lambda cfg, **k: "tok")
    responses.add(
        responses.GET, "https://api.pinterest.com/v5/pins",
        json={"items": [{"id": str(i)} for i in range(10)], "bookmark": None},
        status=200,
    )
    items = get_paginated(config, "/pins", max_items=5)
    assert len(items) == 5
