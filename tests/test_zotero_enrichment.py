from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import zotero
from zotero_enrichment import (
    generate_zotero_enrichment,
    markdown_to_zotero_note_html,
    normalize_suggested_tags,
)


class FakeResponse:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}
        self.closed = False

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


def test_normalize_suggested_tags_groups_and_deduplicates():
    tags = normalize_suggested_tags(
        [
            {"group": "主题", "value": "异常检测"},
            {"group": "方法", "value": "CLIP"},
            {"group": "主题", "value": "异常检测"},
            {"group": "未知", "value": "忽略"},
            "数据集/MVTec-AD",
        ],
        existing_tags=["方法/CLIP"],
    )

    assert tags == [
        {"group": "主题", "value": "异常检测", "tag": "主题/异常检测"},
        {"group": "数据集", "value": "MVTec-AD", "tag": "数据集/MVTec-AD"},
    ]


@pytest.mark.asyncio
async def test_generate_zotero_enrichment_uses_report_and_existing_tags():
    class FakeLLM:
        async def chat(self, messages, **kwargs):
            assert "已有标签" in messages[1]["content"]
            assert "深度阅读报告" in messages[1]["content"]
            assert kwargs["_usage_context"] == "zotero_note_and_tags"
            return json.dumps(
                {
                    "note_markdown": "# 一句话结论\n这是一篇异常检测论文。",
                    "tags": [
                        {"group": "主题", "value": "异常检测"},
                        {"group": "方法", "value": "CLIP"},
                    ],
                },
                ensure_ascii=False,
            )

    result = await generate_zotero_enrichment(
        FakeLLM(),
        {"title": "AA-CLIP", "tags": ["方法/CLIP"]},
        "## 1. 一句话抓住论文\n异常检测。",
    )

    assert result["note_markdown"].startswith("# 一句话结论")
    assert [tag["tag"] for tag in result["tags"]] == ["主题/异常检测"]
    assert result["writeback"]["status"] == "pending"


@pytest.mark.asyncio
async def test_generate_zotero_enrichment_retries_truncated_json():
    class FakeLLM:
        def __init__(self):
            self.calls = []

        async def chat(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            if len(self.calls) == 1:
                return '{"note_markdown":"未闭合'
            return json.dumps(
                {
                    "note_markdown": "# 一句话结论\n重试成功。",
                    "tags": [{"group": "状态", "value": "已精读"}],
                },
                ensure_ascii=False,
            )

    fake_llm = FakeLLM()
    result = await generate_zotero_enrichment(fake_llm, {"title": "Paper"}, "完整报告")

    assert result["note_markdown"].endswith("重试成功。")
    assert len(fake_llm.calls) == 2
    assert fake_llm.calls[1][1]["_usage_context"] == "zotero_note_and_tags_retry"
    assert "保证 JSON 完整闭合" in fake_llm.calls[1][0][1]["content"]


def test_markdown_to_zotero_note_html_marks_and_escapes_content():
    result = markdown_to_zotero_note_html(
        "## 核心问题\n- <script>alert(1)</script>\n结论",
        "AI 精读：测试论文",
    )

    assert 'data-paper-insight-note="paper-insight-ai-note:v1"' in result
    assert "<h3>核心问题</h3>" in result
    assert "&lt;script&gt;" in result
    assert "<script>" not in result


def test_create_note_accepts_zotero_single_write_success_shape(monkeypatch):
    client = zotero.ZoteroClient("write-key")
    response = FakeResponse(
        {"success": {"0": {"key": "NOTE1", "version": 22}}},
        {"Last-Modified-Version": "22"},
    )
    captured = {}

    def fake_request(method, path, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return response

    monkeypatch.setattr(client, "_request", fake_request)

    result = client.create_note(123, "PAPER1", "<p>note</p>")

    assert result == {"key": "NOTE1", "version": 22}
    assert captured["method"] == "POST"
    assert captured["json_body"][0]["parentItem"] == "PAPER1"
    assert captured["headers"]["Zotero-Write-Token"]
    assert response.closed


def test_write_analysis_note_and_tags_preserves_existing_and_updates_same_note(monkeypatch):
    client = zotero.ZoteroClient("write-key")
    patched = []

    def fake_fetch(zotero_user_id, item_key):
        if item_key == "PAPER1":
            return {
                "key": "PAPER1",
                "version": 10,
                "data": {"itemType": "journalArticle", "tags": [{"tag": "手工标签"}]},
            }
        return {
            "key": "NOTE1",
            "version": 11,
            "data": {"itemType": "note", "parentItem": "PAPER1", "note": "old"},
        }

    def fake_patch(zotero_user_id, item_key, version, changes):
        patched.append((item_key, version, changes))
        return 12 if item_key == "NOTE1" else 13

    monkeypatch.setattr(client, "fetch_item", fake_fetch)
    monkeypatch.setattr(client, "patch_item", fake_patch)
    monkeypatch.setattr(
        client,
        "create_note",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must update existing note")),
    )

    result = client.write_analysis_note_and_tags(
        123,
        "PAPER1",
        note_html="<p>new</p>",
        suggested_tags=["主题/异常检测", "手工标签"],
        note_item_key="NOTE1",
    )

    assert result["note_item_key"] == "NOTE1"
    assert result["added_tags"] == ["主题/异常检测"]
    assert result["all_tags"] == ["手工标签", "主题/异常检测"]
    assert patched[0][0] == "PAPER1"
    assert patched[1][0] == "NOTE1"
