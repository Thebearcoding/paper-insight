"""Zotero PDF translation must remain scoped to the owner and exact cloud attachment."""

import pytest
from fastapi import HTTPException, Response

import app as app_module
import pdf_translation


OWNER_ITEM = {
    "item_key": "PARENT", "children": [
        {"item_key": "CLOUD", "parent_item_key": "PARENT", "item_type": "attachment",
         "content_type": "application/pdf", "link_mode": "imported_file", "item_version": 4},
        {"item_key": "LOCAL", "parent_item_key": "PARENT", "item_type": "attachment",
         "content_type": "application/pdf", "link_mode": "linked_file", "item_version": 1},
        {"item_key": "OTHER", "parent_item_key": "ELSEWHERE", "item_type": "attachment",
         "content_type": "application/pdf", "link_mode": "imported_file", "item_version": 1},
    ],
}


@pytest.mark.asyncio
async def test_zotero_translation_rejects_cross_user_and_non_child(monkeypatch):
    monkeypatch.setattr(app_module, "get_zotero_item",
                        lambda user_id, _: OWNER_ITEM if user_id == "owner" else None)
    for user_id, attachment_key in [("stranger", "CLOUD"), ("owner", "OTHER")]:
        with pytest.raises(HTTPException) as failure:
            await app_module._owned_zotero_pdf(user_id, "PARENT", attachment_key)
        assert failure.value.status_code == 404
    with pytest.raises(HTTPException) as failure:
        await app_module._owned_zotero_pdf("owner", "PARENT", "LOCAL")
    assert failure.value.status_code == 400


@pytest.mark.asyncio
async def test_zotero_translation_scopes_status_and_download(monkeypatch):
    monkeypatch.setattr(app_module, "translation_enabled", lambda: True)
    monkeypatch.setattr(app_module, "get_zotero_item",
                        lambda user_id, _: OWNER_ITEM if user_id == "owner" else None)
    calls = []
    def get_row(*args):
        calls.append(args)
        return None
    monkeypatch.setattr(app_module, "get_zotero_attachment_translation", get_row)
    response = Response()
    result = await app_module.get_my_zotero_translation(
        "PARENT", "CLOUD", response, user={"id": "owner"})
    assert result["status"] == "idle"
    assert response.headers["cache-control"] == "private, no-store"
    assert calls[0][:4] == ("owner", "PARENT", "CLOUD", 4)
    with pytest.raises(HTTPException) as failure:
        await app_module.download_my_zotero_translation(
            "PARENT", "CLOUD", "mono", user={"id": "stranger"})
    assert failure.value.status_code == 404


@pytest.mark.asyncio
async def test_zotero_translation_invalidates_changed_attachment_version(monkeypatch):
    monkeypatch.setattr(app_module, "translation_enabled", lambda: True)
    changed = {**OWNER_ITEM, "children": [
        {**OWNER_ITEM["children"][0], "item_version": 5},
    ]}
    monkeypatch.setattr(app_module, "get_zotero_item", lambda *_: changed)
    observed = []
    def row_for_version(*args):
        observed.append(args[3])
        return {"id": "old", "status": "success", "remote_task_id": "old-task"} if args[3] == 4 else None
    monkeypatch.setattr(app_module, "get_zotero_attachment_translation", row_for_version)
    result = await app_module.get_my_zotero_translation(
        "PARENT", "CLOUD", Response(), user={"id": "owner"})
    assert observed == [5]
    assert result["status"] == "idle"
    assert result["mono_url"] is None


@pytest.mark.asyncio
async def test_zotero_translation_starts_from_owner_attachment(monkeypatch):
    monkeypatch.setattr(app_module, "translation_enabled", lambda: True)
    monkeypatch.setattr(app_module, "get_zotero_item", lambda *_: OWNER_ITEM)
    monkeypatch.setattr(app_module, "get_zotero_attachment_translation", lambda *_: None)
    claims = []
    monkeypatch.setattr(app_module, "claim_zotero_attachment_translation",
                        lambda *args: (claims.append(args) or {"id": "12", "status": "pending"}, True))
    started = []
    monkeypatch.setattr(app_module, "start_zotero_translation_task", lambda *args: started.append(args))
    result = await app_module.start_my_zotero_translation(
        "PARENT", "CLOUD", Response(), user={"id": "owner"})
    assert result["status"] == "pending"
    assert claims[0][:4] == ("owner", "PARENT", "CLOUD", 4)
    assert started == [("owner", "PARENT", "CLOUD", 4, "12")]


@pytest.mark.asyncio
async def test_zotero_translation_source_never_uses_public_pdf(monkeypatch):
    monkeypatch.setattr(pdf_translation, "get_zotero_connection", lambda *_: None)
    monkeypatch.setattr(pdf_translation, "update_zotero_attachment_translation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("database.get_zotero_item", lambda *_: OWNER_ITEM)
    monkeypatch.setattr(pdf_translation, "download_public_pdf_bytes",
                        lambda *_: pytest.fail("public PDF must not be accessed"))
    await pdf_translation._run_zotero_translation("owner", "PARENT", "CLOUD", 4, "12")


@pytest.mark.asyncio
async def test_zotero_translation_downloads_only_owners_cloud_attachment(monkeypatch):
    import zotero

    captured = []
    updates = []
    monkeypatch.setattr("database.get_zotero_item", lambda *_: OWNER_ITEM)
    monkeypatch.setattr(pdf_translation, "get_zotero_connection",
                        lambda user_id, *_: {"api_key": "owner-secret", "zotero_user_id": 42})
    class FakeClient:
        def __init__(self, key):
            captured.append(("key", key))
        def download_attachment(self, library_id, attachment_key):
            captured.append(("download", library_id, attachment_key))
            return b"%PDF-1.4\nprivate attachment"
    class FakeHTTP:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return None
    async def submit(_client, _cfg, content):
        captured.append(("submitted", content))
        return "remote-1"
    async def progress(*_):
        return {"state": "success", "progress": 100}
    monkeypatch.setattr(zotero, "ZoteroClient", FakeClient)
    monkeypatch.setattr(pdf_translation.httpx, "AsyncClient", FakeHTTP)
    monkeypatch.setattr(pdf_translation, "submit_translation", submit)
    monkeypatch.setattr(pdf_translation, "query_translation_progress", progress)
    monkeypatch.setattr(pdf_translation, "_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(pdf_translation, "update_zotero_attachment_translation",
                        lambda *args, **kwargs: updates.append((args, kwargs)))
    monkeypatch.setattr(pdf_translation, "download_public_pdf_bytes",
                        lambda *_: pytest.fail("public PDF must not be accessed"))
    await pdf_translation._run_zotero_translation("owner", "PARENT", "CLOUD", 4, "12")
    assert captured == [("key", "owner-secret"), ("download", 42, "CLOUD"),
                        ("submitted", b"%PDF-1.4\nprivate attachment")]
    assert updates[-1][1] == {"status": "success", "progress": 100}
