from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import zotero
from paper_resources import ResolvedDocument


class FakeResponse:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}
        self.closed = False

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


def test_public_key_metadata_requires_personal_library_read_access():
    with pytest.raises(zotero.ZoteroAuthError):
        zotero.public_key_metadata(
            {
                "userID": 123,
                "username": "reader",
                "access": {"user": {"library": False}},
            }
        )


def test_public_key_metadata_extracts_safe_identity_fields():
    result = zotero.public_key_metadata(
        {
            "userID": 123,
            "username": "reader",
            "displayName": "Paper Reader",
            "access": {"user": {"library": True, "write": False}},
        }
    )

    assert result == {
        "zotero_user_id": 123,
        "username": "reader",
        "display_name": "Paper Reader",
        "can_read": True,
        "can_write": False,
    }


def test_normalize_item_keeps_parent_notes_annotations_and_tags():
    result = zotero.normalize_item(
        {
            "key": "ANNOTATION1",
            "version": 7,
            "data": {
                "itemType": "annotation",
                "parentItem": "PARENT1",
                "annotationText": "<b>important</b>",
                "annotationComment": "review this",
                "tags": [{"tag": "deep-learning"}],
                "collections": ["COLL1"],
            },
        }
    )

    assert result["item_key"] == "ANNOTATION1"
    assert result["parent_item_key"] == "PARENT1"
    assert result["annotation_text"] == "important"
    assert result["annotation_comment"] == "review this"
    assert result["tags"] == ["deep-learning"]
    assert result["collections"] == ["COLL1"]


def test_get_item_reading_context_prefers_zotero_indexed_fulltext(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(zotero, "_cache_dir", lambda: tmp_path)

    class FakeClient:
        def fetch_fulltext(self, zotero_user_id: int, attachment_key: str) -> str:
            assert zotero_user_id == 123
            assert attachment_key == "PDF1"
            return "A sufficiently useful indexed paper body."

        def download_attachment(self, zotero_user_id: int, attachment_key: str) -> bytes:
            raise AssertionError("indexed full text should avoid attachment download")

    context, source, warning = zotero.get_item_reading_context(
        user_id="user-1",
        zotero_user_id=123,
        item={
            "item_key": "PAPER1",
            "item_type": "journalArticle",
            "title": "A Paper",
            "creators": [],
            "tags": [],
        },
        children=[
            {
                "item_key": "PDF1",
                "item_version": 2,
                "item_type": "attachment",
                "content_type": "application/pdf",
                "filename": "paper.pdf",
            }
        ],
        client=FakeClient(),
    )

    assert source == "zotero-fulltext"
    assert warning is None
    assert "A sufficiently useful indexed paper body." in context
    assert (tmp_path / "user-1" / "PDF1.txt").exists()


def test_get_item_reading_context_skips_compare_pdf_when_primary_exists(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(zotero, "_cache_dir", lambda: tmp_path)

    class FakeClient:
        def fetch_fulltext(self, zotero_user_id: int, attachment_key: str) -> str:
            assert zotero_user_id == 123
            assert attachment_key == "PRIMARY"
            return "The primary paper body."

        def download_attachment(self, zotero_user_id: int, attachment_key: str) -> bytes:
            raise AssertionError("indexed primary full text should avoid attachment download")

    context, source, warning = zotero.get_item_reading_context(
        user_id="user-1",
        zotero_user_id=123,
        item={
            "item_key": "PAPER1",
            "item_type": "journalArticle",
            "title": "A Paper",
            "creators": [],
            "tags": [],
        },
        children=[
            {
                "item_key": "COMPARE",
                "item_version": 1,
                "item_type": "attachment",
                "content_type": "application/pdf",
                "filename": "paper.compare.pdf",
            },
            {
                "item_key": "PRIMARY",
                "item_version": 1,
                "item_type": "attachment",
                "content_type": "application/pdf",
                "filename": "paper.pdf",
            },
        ],
        client=FakeClient(),
    )

    assert source == "zotero-fulltext"
    assert warning is None
    assert "The primary paper body." in context


def test_build_metadata_context_includes_user_notes_and_annotations():
    context = zotero.build_metadata_context(
        {
            "title": "A Paper",
            "item_type": "journalArticle",
            "creators": [{"firstName": "Ada", "lastName": "Lovelace"}],
            "tags": ["methods"],
        },
        [
            {"note": "My reading note"},
            {"annotation_text": "Highlighted claim", "annotation_comment": "Verify this"},
        ],
    )

    assert "Ada Lovelace" in context
    assert "My reading note" in context
    assert "Highlighted claim" in context
    assert "Verify this" in context


def test_compact_zotero_analysis_context_keeps_main_text_and_drops_tail():
    context = (
        "Zotero 条目元数据：\n"
        + "metadata " * 2_000
        + "\n\n论文全文：\n"
        + "MAIN_TEXT_START\n"
        + "method experiment metric " * 4_000
        + "\nSUPPLEMENT_TAIL_SENTINEL"
    )

    compact = zotero.compact_zotero_analysis_context(context, max_tokens=2_000)

    assert "Zotero 条目元数据" in compact
    assert "MAIN_TEXT_START" in compact
    assert "模型输入范围" in compact
    assert "SUPPLEMENT_TAIL_SENTINEL" not in compact


def test_compact_zotero_analysis_context_preserves_short_input():
    context = "Zotero 条目元数据：短内容\n\n论文全文：\nShort paper body."

    assert zotero.compact_zotero_analysis_context(context) == context


def test_glm_analysis_context_defaults_to_sixteen_thousand_tokens():
    assert zotero.ZOTERO_ANALYSIS_PROXY_TOKEN_LIMIT == 16_000
    assert zotero.ZOTERO_ANALYSIS_PROXY_OUTPUT_TOKEN_LIMIT == 32_768


def test_compact_zotero_analysis_context_samples_method_experiment_and_conclusion():
    context = (
        "Zotero 条目元数据：\nA paper\n\n论文全文：\n"
        + "Abstract\nTASK_SENTINEL "
        + "introduction " * 1_500
        + "\n4 Proposed Method\n"
        + "method details " * 250
        + "METHOD_SENTINEL "
        + "method details " * 100
        + "\n5 Experiments\nEXPERIMENT_SENTINEL "
        + "results " * 800
        + "\n6 Conclusion\nCONCLUSION_SENTINEL "
        + "summary " * 300
        + "\nReferences\nREFERENCE_TAIL_SENTINEL "
        + "citation " * 2_000
    )

    compact = zotero.compact_zotero_analysis_context(context, max_tokens=2_000)

    assert "TASK_SENTINEL" in compact
    assert "METHOD_SENTINEL" in compact
    assert "EXPERIMENT_SENTINEL" in compact
    assert "CONCLUSION_SENTINEL" in compact
    assert "REFERENCE_TAIL_SENTINEL" not in compact


def test_linked_file_falls_back_to_public_pdf_and_includes_repository(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(zotero, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(
        zotero,
        "resolve_public_document",
        lambda item, children: (
            ResolvedDocument(
                content="Full paper body with experiments and implementation details.",
                url="https://arxiv.org/pdf/2503.06661",
                source="arxiv",
            ),
            [],
        ),
    )
    monkeypatch.setattr(
        zotero,
        "build_repository_context",
        lambda urls: "公开源码仓库：\n" + "\n".join(urls),
    )

    class FakeClient:
        def fetch_fulltext(self, zotero_user_id: int, attachment_key: str) -> None:
            return None

        def download_attachment(self, zotero_user_id: int, attachment_key: str) -> bytes:
            raise AssertionError("linked_file must not be requested from Zotero Storage")

    context, source, warning = zotero.get_item_reading_context(
        user_id="user-1",
        zotero_user_id=123,
        item={
            "item_key": "PAPER1",
            "item_version": 5,
            "item_type": "preprint",
            "title": "AA-CLIP",
            "doi": "10.48550/arXiv.2503.06661",
            "url": "https://arxiv.org/abs/2503.06661",
            "abstract_note": "Code: https://github.com/Mwxinnn/AA-CLIP",
            "creators": [],
            "tags": [],
            "raw": {"data": {}},
        },
        children=[
            {
                "item_key": "PDF1",
                "item_version": 2,
                "item_type": "attachment",
                "content_type": "application/pdf",
                "link_mode": "linked_file",
                "raw": {"data": {}},
            }
        ],
        client=FakeClient(),
    )

    assert source == "public-document:arxiv"
    assert warning is None
    assert "公开 PDF 地址：https://arxiv.org/pdf/2503.06661" in context
    assert "https://github.com/Mwxinnn/AA-CLIP" in context
    assert "Full paper body with experiments" in context


def test_item_without_zotero_attachment_can_use_public_document(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(zotero, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(
        zotero,
        "resolve_public_document",
        lambda item, children: (
            ResolvedDocument("Public full text", "https://example.org/paper.pdf", "openalex"),
            [],
        ),
    )
    monkeypatch.setattr(zotero, "build_repository_context", lambda urls: "")

    context, source, warning = zotero.get_item_reading_context(
        user_id="user-1",
        zotero_user_id=123,
        item={
            "item_key": "PAPER2",
            "item_version": 1,
            "item_type": "journalArticle",
            "title": "Public Paper",
            "creators": [],
            "tags": [],
            "raw": {"data": {}},
        },
        children=[],
        client=object(),
    )

    assert source == "public-document:openalex"
    assert warning is None
    assert "Public full text" in context


def test_fetch_sync_data_normalizes_incremental_library_changes(monkeypatch):
    client = zotero.ZoteroClient("read-only-key")
    responses = {
        "/users/123/collections": FakeResponse(
            [
                {
                    "key": "COLL1",
                    "version": 11,
                    "data": {"name": "Reading", "parentCollection": False},
                }
            ],
            {"Last-Modified-Version": "11", "Total-Results": "1"},
        ),
        "/users/123/items": FakeResponse(
            [
                {
                    "key": "PAPER1",
                    "version": 12,
                    "data": {
                        "itemType": "journalArticle",
                        "title": "Incremental Sync",
                        "collections": ["COLL1"],
                    },
                },
                {
                    "key": "PDF1",
                    "version": 13,
                    "data": {
                        "itemType": "attachment",
                        "parentItem": "PAPER1",
                        "contentType": "application/pdf",
                        "filename": "paper.pdf",
                    },
                },
                {
                    "key": "ANNOTATION1",
                    "version": 14,
                    "data": {
                        "itemType": "annotation",
                        "parentItem": "PDF1",
                        "annotationText": "Important result",
                    },
                },
            ],
            {"Last-Modified-Version": "14", "Total-Results": "3"},
        ),
        "/users/123/deleted": FakeResponse(
            {"items": ["OLDITEM"], "collections": ["OLDCOLL"]},
            {"Last-Modified-Version": "15"},
        ),
    }
    calls = []

    def fake_request(method, path, *, params=None, stream=False):
        calls.append((method, path, params, stream))
        return responses[path]

    monkeypatch.setattr(client, "_request", fake_request)

    payload = client.fetch_sync_data(123, since=9)

    assert payload["zotero_user_id"] == 123
    assert payload["library_version"] == 15
    assert payload["collections"][0]["collection_key"] == "COLL1"
    assert [item["item_key"] for item in payload["items"]] == [
        "PAPER1",
        "PDF1",
        "ANNOTATION1",
    ]
    assert payload["items"][2]["parent_item_key"] == "PDF1"
    assert payload["deleted_item_keys"] == ["OLDITEM"]
    assert payload["deleted_collection_keys"] == ["OLDCOLL"]
    assert all(call[2]["since"] == 9 for call in calls)
    assert all(response.closed for response in responses.values())
