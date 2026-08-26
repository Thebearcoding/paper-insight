from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app


@pytest.mark.asyncio
async def test_refresh_zotero_connection_updates_changed_write_permission(monkeypatch):
    stored = {
        "user_id": "user-1",
        "api_key": "encrypted-at-rest-key",
        "zotero_user_id": 123,
        "username": "reader",
        "display_name": "Reader",
        "can_read": True,
        "can_write": False,
    }
    live = {**stored, "can_write": True}
    live.pop("api_key")
    saved_calls = []

    monkeypatch.setattr(app, "get_zotero_connection", lambda user_id, include_api_key: stored)
    monkeypatch.setattr(
        app,
        "save_zotero_connection",
        lambda user_id, api_key, metadata: saved_calls.append((user_id, api_key, metadata))
        or {**metadata, "user_id": user_id},
    )

    class FakeClient:
        def __init__(self, api_key):
            assert api_key == stored["api_key"]

        def verify_key(self):
            return live

    monkeypatch.setattr(app, "ZoteroClient", FakeClient)

    result = await app.refresh_zotero_connection_metadata("user-1")

    assert result["can_write"] is True
    assert result["api_key"] == stored["api_key"]
    assert saved_calls == [("user-1", stored["api_key"], live)]


@pytest.mark.asyncio
async def test_refresh_zotero_connection_avoids_write_when_metadata_is_current(monkeypatch):
    stored = {
        "user_id": "user-1",
        "api_key": "encrypted-at-rest-key",
        "zotero_user_id": 123,
        "username": "reader",
        "display_name": "Reader",
        "can_read": True,
        "can_write": True,
    }
    live = {key: value for key, value in stored.items() if key not in {"user_id", "api_key"}}

    monkeypatch.setattr(app, "get_zotero_connection", lambda user_id, include_api_key: stored)
    monkeypatch.setattr(
        app,
        "save_zotero_connection",
        lambda *args: (_ for _ in ()).throw(AssertionError("metadata is unchanged")),
    )

    monkeypatch.setattr(
        app,
        "ZoteroClient",
        lambda *args: (_ for _ in ()).throw(AssertionError("write permission is cached")),
    )

    assert await app.refresh_zotero_connection_metadata("user-1") == stored


@pytest.mark.asyncio
async def test_refresh_zotero_connection_force_rechecks_cached_write_permission(monkeypatch):
    stored = {
        "user_id": "user-1",
        "api_key": "encrypted-at-rest-key",
        "zotero_user_id": 123,
        "username": "reader",
        "display_name": "Reader",
        "can_read": True,
        "can_write": True,
    }
    live = {key: value for key, value in stored.items() if key not in {"user_id", "api_key"}}
    checked = []

    monkeypatch.setattr(app, "get_zotero_connection", lambda user_id, include_api_key: stored)
    monkeypatch.setattr(
        app,
        "save_zotero_connection",
        lambda *args: (_ for _ in ()).throw(AssertionError("metadata is unchanged")),
    )

    class FakeClient:
        def __init__(self, api_key):
            checked.append(api_key)

        def verify_key(self):
            return live

    monkeypatch.setattr(app, "ZoteroClient", FakeClient)

    result = await app.refresh_zotero_connection_metadata("user-1", force=True)

    assert result == stored
    assert checked == [stored["api_key"]]
