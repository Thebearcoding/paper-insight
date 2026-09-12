import asyncio
import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app as app_module
from llm import LLMOutputTruncatedError, LLMStreamChunk
from zotero_enrichment import enrichment_matches_report, report_fingerprint


REPORT = """## 1. 论文解决的任务

任务定义。（依据：Section 1）

## 2. 任务评估指标

准确率。（依据：Section 4）

## 3. 方法提升指标的本质原因

### 方法链路与训练/推理过程

分别训练和预测。（依据：Section 3）

### 提升指标的本质原因

本文明确改变检索步骤。（依据：Section 3）

### SOTA 对比实验

材料没有可靠对比数值。总结而言，应在论文限定的实验条件下理解结论。"""


def configure(monkeypatch, *, stream_error=None):
    saved = []
    enrichments = []
    item = {"title": "Paper", "llm_response": "旧报告", "analysis_enrichment": {
        "note_markdown": "旧笔记。", "writeback": {"status": "applied", "note_item_key": "NOTE1"},
    }}

    class FakeLLM:
        def public_config(self):
            return {"provider_key": "test", "model_name": "model"}

        def is_configured(self):
            return True

        async def get_response_stream_events(self, prompt, **kwargs):
            yield LLMStreamChunk(kind="content", content=REPORT)
            if stream_error:
                raise stream_error

    async def context(*args):
        return "论文全文：\n方法和实验内容", "zotero-fulltext", None

    async def assets(*args, **kwargs):
        return []

    async def enrichment(*args):
        assert saved, "The report must be saved before starting optional enrichment"
        return {"note_markdown": "新笔记。", "report_status": "current"}

    def save_enrichment(*args, expected_report):
        assert expected_report == saved[-1][2]
        enrichments.append(args)
        return True

    monkeypatch.setattr(app_module, "llm", SimpleNamespace(select=lambda *_: FakeLLM()))
    monkeypatch.setattr(app_module, "get_zotero_item", lambda *_: item)
    monkeypatch.setattr(app_module, "load_zotero_reading_context", context)
    monkeypatch.setattr(app_module, "extract_zotero_analysis_assets", assets)
    monkeypatch.setattr(app_module, "update_zotero_analysis", lambda *args: saved.append(args))
    monkeypatch.setattr(app_module, "update_zotero_analysis_enrichment", save_enrichment)
    monkeypatch.setattr(app_module, "generate_zotero_enrichment", enrichment)
    return saved, enrichments, item


@pytest.mark.asyncio
async def test_primary_report_is_saved_before_enrichment(monkeypatch):
    saved, enrichments, _ = configure(monkeypatch)
    response = await app_module.analyze_my_zotero_item("P1", reanalyze=True, user={"id": "u1"})
    events = [event async for event in response.body_iterator]
    assert saved[0][2] == REPORT
    assert saved[0][4]["report_status"] == "stale"
    assert saved[0][4]["writeback"]["note_item_key"] == "NOTE1"
    assert enrichments[0][2]["report_status"] == "current"
    assert events[-1]["event"] == "done"


@pytest.mark.asyncio
async def test_report_survives_enrichment_failure_and_old_notes_are_marked_stale(monkeypatch):
    saved, enrichments, _ = configure(monkeypatch)

    async def fail(*args):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(app_module, "generate_zotero_enrichment", fail)
    response = await app_module.analyze_my_zotero_item("P1", reanalyze=True, user={"id": "u1"})
    events = [event async for event in response.body_iterator]
    assert saved[0][2] == REPORT
    assert saved[0][4]["report_status"] == "stale"
    assert enrichments == []
    assert any(event.get("event") == "enrichment-error" for event in events)
    assert events[-1]["event"] == "done"


@pytest.mark.asyncio
async def test_disconnect_after_report_cannot_lose_primary_artifact(monkeypatch):
    saved, enrichments, _ = configure(monkeypatch)
    response = await app_module.analyze_my_zotero_item("P1", reanalyze=True, user={"id": "u1"})
    async for event in response.body_iterator:
        if event.get("event") == "final":
            break
    await response.body_iterator.aclose()
    assert saved[0][2] == REPORT
    assert enrichments == []


@pytest.mark.asyncio
async def test_truncated_report_never_overwrites_previous_report(monkeypatch):
    saved, enrichments, _ = configure(monkeypatch, stream_error=LLMOutputTruncatedError("输出达到 token 上限"))
    response = await app_module.analyze_my_zotero_item("P1", reanalyze=True, user={"id": "u1"})
    events = [event async for event in response.body_iterator]
    assert saved == [] and enrichments == []
    assert events[-1]["event"] == "error"
    assert "token 上限" in events[-1]["data"]


@pytest.mark.asyncio
async def test_write_failure_does_not_claim_report_was_saved(monkeypatch):
    _, enrichments, _ = configure(monkeypatch)

    def fail(*args):
        raise app_module.DatabaseError("unavailable")

    monkeypatch.setattr(app_module, "update_zotero_analysis", fail)
    response = await app_module.analyze_my_zotero_item("P1", reanalyze=True, user={"id": "u1"})
    events = [event async for event in response.body_iterator]
    assert any(event.get("event") == "warning" and "暂未保存" in event.get("data", "") for event in events)
    assert enrichments == []
    assert events[-2]["data"] == REPORT


@pytest.mark.asyncio
async def test_stale_notes_cannot_be_written_to_zotero(monkeypatch):
    _, _, item = configure(monkeypatch)
    item["analysis_enrichment"]["report_status"] = "stale"

    async def connection(*args, **kwargs):
        return {"can_write": True}

    monkeypatch.setattr(app_module, "refresh_zotero_connection_metadata", connection)
    with pytest.raises(app_module.HTTPException) as error:
        await app_module.writeback_my_zotero_item_enrichment("P1", user={"id": "u1"})
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_slow_analysis_cannot_overwrite_newer_report_enrichment(monkeypatch):
    stored = {"title": "Paper", "llm_response": REPORT, "analysis_enrichment": {}}
    first_report_saved = asyncio.Event()
    release_first_note = asyncio.Event()

    class FakeLLM:
        def __init__(self, name):
            self.name = name

        def public_config(self):
            return {"provider_key": "test", "model_name": self.name}

        def is_configured(self):
            return True

        async def get_response_stream_events(self, *args, **kwargs):
            yield LLMStreamChunk(kind="content", content=REPORT.replace("任务定义。", f"任务定义 {self.name}。"))

    async def context(*args):
        return "论文全文：方法和实验内容", "zotero-fulltext", None

    async def assets(*args, **kwargs):
        return []

    def save_report(*args):
        stored["llm_response"] = args[2]
        stored["analysis_enrichment"] = copy.deepcopy(args[4])

    def save_enrichment(*args, expected_report):
        if stored["llm_response"] != expected_report:
            return False
        stored["analysis_enrichment"] = copy.deepcopy(args[2])
        return True

    async def enrichment(llm, item, report):
        if llm.name == "A":
            first_report_saved.set()
            await release_first_note.wait()
        return {
            "note_markdown": f"笔记 {llm.name}",
            "source_report_hash": report_fingerprint(report),
            "report_status": "current",
        }

    async def consume(name):
        response = await app_module.analyze_my_zotero_item(
            "P1", reanalyze=True, provider_id=name, user={"id": "u1"},
        )
        return [event async for event in response.body_iterator]

    monkeypatch.setattr(app_module, "llm", SimpleNamespace(select=lambda provider, model: FakeLLM(provider)))
    monkeypatch.setattr(app_module, "get_zotero_item", lambda *_: copy.deepcopy(stored))
    monkeypatch.setattr(app_module, "load_zotero_reading_context", context)
    monkeypatch.setattr(app_module, "extract_zotero_analysis_assets", assets)
    monkeypatch.setattr(app_module, "update_zotero_analysis", save_report)
    monkeypatch.setattr(app_module, "update_zotero_analysis_enrichment", save_enrichment)
    monkeypatch.setattr(app_module, "generate_zotero_enrichment", enrichment)

    first = asyncio.create_task(consume("A"))
    try:
        await asyncio.wait_for(first_report_saved.wait(), timeout=5)
        second_events = await asyncio.wait_for(consume("B"), timeout=5)
        release_first_note.set()
        first_events = await asyncio.wait_for(first, timeout=5)
    finally:
        release_first_note.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)

    assert "任务定义 B。" in stored["llm_response"]
    assert stored["analysis_enrichment"]["note_markdown"] == "笔记 B"
    assert enrichment_matches_report(stored["analysis_enrichment"], stored["llm_response"])
    assert any(event.get("event") == "enrichment-error" and "刷新" in event["data"] for event in first_events)
    assert not any(event.get("event") == "enrichment" and '"current"' in event["data"] for event in first_events)
    assert first_events[-1]["event"] == second_events[-1]["event"] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("report_changed", [False, True])
async def test_manual_enrichment_compares_original_stored_report(monkeypatch, report_changed):
    raw_report = "  " + REPORT + "\n\n"
    item = {"title": "Paper", "llm_response": raw_report}
    generated = {"note_markdown": "新笔记。", "report_status": "current"}
    expected_reports = []

    async def generate(llm, source_item, report):
        assert report == REPORT
        return generated

    def save(*args, expected_report):
        expected_reports.append(expected_report)
        return not report_changed

    monkeypatch.setattr(app_module, "llm", SimpleNamespace(select=lambda *_: object()))
    monkeypatch.setattr(app_module, "get_zotero_item", lambda *_: item)
    monkeypatch.setattr(app_module, "generate_zotero_enrichment", generate)
    monkeypatch.setattr(app_module, "update_zotero_analysis_enrichment", save)
    if report_changed:
        with pytest.raises(app_module.HTTPException) as error:
            await app_module.generate_my_zotero_item_enrichment("P1", user={"id": "u1"})
        assert error.value.status_code == 409
        assert "刷新" in error.value.detail
    else:
        assert await app_module.generate_my_zotero_item_enrichment("P1", user={"id": "u1"}) == generated
    assert expected_reports == [raw_report]
