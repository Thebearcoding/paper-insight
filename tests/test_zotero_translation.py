"""Zotero translations use public PDFs and remain scoped to the item owner."""

import pytest
from fastapi import HTTPException, Response

import app as app_module
import pdf_translation
import paper_resources
from utils import ReaderError


OWNER_ITEM = {"item_key": "PARENT", "item_version": 7, "title": "Matching Paper",
              "url": "https://arxiv.org/abs/2401.01234", "children": []}


@pytest.mark.asyncio
async def test_status_and_download_require_owned_item(monkeypatch):
    monkeypatch.setattr(app_module, "translation_enabled", lambda: True)
    monkeypatch.setattr(app_module, "get_zotero_item",
                        lambda user_id, _: OWNER_ITEM if user_id == "owner" else None)
    calls = []
    monkeypatch.setattr(app_module, "get_zotero_public_translation",
                        lambda *args: (calls.append(args) or None))
    response = Response()
    result = await app_module.get_my_zotero_translation("PARENT", response, user={"id": "owner"})
    assert result["status"] == "idle"
    assert response.headers["cache-control"] == "private, no-store"
    assert calls[0][:3] == ("owner", "PARENT", 7)
    with pytest.raises(HTTPException) as failure:
        await app_module.download_my_zotero_translation("PARENT", "mono", user={"id": "stranger"})
    assert failure.value.status_code == 404


@pytest.mark.asyncio
async def test_translation_claims_public_item_not_attachment(monkeypatch):
    monkeypatch.setattr(app_module, "translation_enabled", lambda: True)
    monkeypatch.setattr(app_module, "get_zotero_item", lambda *_: OWNER_ITEM)
    monkeypatch.setattr(app_module, "get_zotero_public_translation", lambda *_: None)
    claims, started = [], []
    monkeypatch.setattr(app_module, "claim_zotero_public_translation",
                        lambda *args: (claims.append(args) or {"id": "12", "status": "pending"}, True))
    monkeypatch.setattr(app_module, "start_zotero_translation_task", lambda *args: started.append(args))
    result = await app_module.start_my_zotero_translation("PARENT", Response(), user={"id": "owner"})
    assert result["status"] == "pending"
    assert claims[0][:3] == ("owner", "PARENT", 7)
    assert started == [("owner", "PARENT", 7, "12")]


@pytest.mark.asyncio
async def test_public_pdf_task_never_downloads_cloud_attachment(monkeypatch):
    import zotero
    captured, updates = [], []
    monkeypatch.setattr("database.get_zotero_item", lambda *_: OWNER_ITEM)
    monkeypatch.setattr(zotero.ZoteroClient, "download_attachment",
                        lambda *_: pytest.fail("Zotero cloud must not be accessed"))
    monkeypatch.setattr(pdf_translation, "download_item_public_pdf_bytes",
                        lambda item, children: (captured.append((item["item_key"], children)) or b"%PDF-1.4\npublic"))
    class FakeHTTP:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
    async def submit(_client, _cfg, content):
        captured.append(content)
        return "remote-1"
    async def progress(*_): return {"state": "success", "progress": 100}
    monkeypatch.setattr(pdf_translation.httpx, "AsyncClient", FakeHTTP)
    monkeypatch.setattr(pdf_translation, "submit_translation", submit)
    monkeypatch.setattr(pdf_translation, "query_translation_progress", progress)
    monkeypatch.setattr(pdf_translation, "_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(pdf_translation, "update_zotero_public_translation",
                        lambda *args, **kwargs: updates.append((args, kwargs)))
    await pdf_translation._run_zotero_translation("owner", "PARENT", 7, "12")
    assert captured == [("PARENT", []), b"%PDF-1.4\npublic"]
    assert updates[-1][1] == {"status": "success", "progress": 100}


@pytest.mark.asyncio
async def test_public_pdf_missing_and_version_change(monkeypatch):
    updates = []
    monkeypatch.setattr("database.get_zotero_item", lambda *_: OWNER_ITEM)
    monkeypatch.setattr(pdf_translation, "update_zotero_public_translation",
                        lambda *args, **kwargs: updates.append(kwargs))
    monkeypatch.setattr(pdf_translation, "download_item_public_pdf_bytes",
                        lambda *_: (_ for _ in ()).throw(ReaderError("未找到公开 PDF")))
    await pdf_translation._run_zotero_translation("owner", "PARENT", 7, "12")
    assert "未找到公开 PDF" in updates[-1]["error"]
    updates.clear()
    await pdf_translation._run_zotero_translation("owner", "PARENT", 6, "12")
    assert "已更新" in updates[-1]["error"]


def test_public_candidates_skip_zotero_and_reject_unmatched_title(monkeypatch):
    monkeypatch.setattr(paper_resources, "download_public_pdf_bytes",
                        lambda url: (_ for _ in ()).throw(ReaderError("not public")) if "zotero.org" in url else b"%PDF-public")
    monkeypatch.setattr(paper_resources, "semantic_scholar_candidates", lambda *_: [])
    monkeypatch.setattr(paper_resources, "crossref_candidates", lambda *_: [])
    monkeypatch.setattr(paper_resources, "openalex_candidates", lambda *_: [])
    monkeypatch.setattr(paper_resources, "openalex_title_candidates", lambda *_: [])
    with pytest.raises(ReaderError, match="未找到与当前文献匹配"):
        paper_resources.download_item_public_pdf_bytes({"url": "https://api.zotero.org/users/1/items/K/file"}, [])
    assert paper_resources._is_public_url("https://api.zotero.org/users/1/items/K/file") is False
