import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app as app_module
from llm import LLMStreamChunk


COMPLETE_REPORT = """> 未找到明确的代码仓库。

## 1. 论文解决的任务

论文解决一个有明确输入与输出的研究任务。（依据：Section 1）

## 2. 任务评估指标

论文使用准确率评估任务表现。（依据：Section 4；Table 1）

## 3. 方法提升指标的本质原因

### 方法链路与训练/推理过程

训练阶段学习任务映射，推理阶段直接生成结果。（依据：Section 3）

### 提升指标的本质原因

新设计保留了更多任务信息，因此提高准确率。（依据：Section 3；Table 1）

### SOTA 对比实验

本文方法优于可比基线。（依据：Table 1）

总结而言，论文通过更有效的信息建模改善了任务表现。"""


class FakeBackgroundAnalyzer:
    async def update_code_availability(self, paper_info, response):
        return True


class RetryThenSucceedLlm:
    def __init__(self):
        self.calls = []

    def is_configured(self):
        return True

    def public_config(self):
        return {
            "provider_key": "sub2api",
            "provider_name": "Sub2API",
            "model_name": "glm-5.3",
        }

    async def get_response_stream_events(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if len(self.calls) == 1:
            yield LLMStreamChunk(kind="content", content="## 1. 论文解决的任务\n\n半截内容")
            raise RuntimeError("network error")
        yield LLMStreamChunk(kind="content", content=COMPLETE_REPORT)


class AlwaysFailLlm(RetryThenSucceedLlm):
    async def get_response_stream_events(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if False:
            yield LLMStreamChunk(kind="content", content="")
        raise RuntimeError("network error")


def configure_paper_analysis_dependencies(monkeypatch, fake_llm, updates):
    monkeypatch.setattr(app_module, "llm", fake_llm)
    monkeypatch.setattr(app_module, "background_analyzer", FakeBackgroundAnalyzer())
    monkeypatch.setattr(
        app_module,
        "get_or_fetch_paper_info",
        lambda paper_id: {
            "id": paper_id,
            "title": "Test paper",
            "pdf": "https://example.test/paper.pdf",
            "llm_response": None,
        },
    )
    monkeypatch.setattr(
        app_module,
        "get_or_cache_paper_content",
        lambda paper_id, pdf_url: "full paper text",
    )
    monkeypatch.setattr(
        app_module,
        "update_llm_response",
        lambda paper_id, response: updates.append((paper_id, response)),
    )


@pytest.mark.asyncio
async def test_paper_analysis_retries_glm_stream_and_saves_complete_report(monkeypatch):
    fake_llm = RetryThenSucceedLlm()
    updates = []
    configure_paper_analysis_dependencies(monkeypatch, fake_llm, updates)

    response = await app_module.get_paper_analysis("paper-1", reanalyze=True)
    events = [event async for event in response.body_iterator]

    assert len(fake_llm.calls) == 2
    assert fake_llm.calls[0][1]["thinking"] == {"type": "disabled"}
    assert fake_llm.calls[0][1]["max_tokens"] == 32_768
    assert any(
        event.get("event") == "status" and "自动重试" in event.get("data", "")
        for event in events
    )
    assert events[-2]["data"] == COMPLETE_REPORT
    assert events[-1]["data"] == ""
    assert updates == [("paper-1", COMPLETE_REPORT)]


@pytest.mark.asyncio
async def test_paper_analysis_returns_sse_error_instead_of_breaking_stream(monkeypatch):
    fake_llm = AlwaysFailLlm()
    updates = []
    configure_paper_analysis_dependencies(monkeypatch, fake_llm, updates)

    response = await app_module.get_paper_analysis("paper-2", reanalyze=True)
    events = [event async for event in response.body_iterator]

    assert len(fake_llm.calls) == 2
    assert "上游模型连接中断" in events[-1]["data"]
    assert "network error" not in events[-1]["data"]
    assert updates == []


@pytest.mark.asyncio
async def test_saved_analysis_remains_readable_without_model_or_code_metadata_service(monkeypatch):
    fake_llm = AlwaysFailLlm()
    monkeypatch.setattr(fake_llm, 'is_configured', lambda: False)
    configure_paper_analysis_dependencies(monkeypatch, fake_llm, [])
    monkeypatch.setattr(app_module, 'get_or_fetch_paper_info', lambda _: {
        'id': 'cached', 'llm_response': COMPLETE_REPORT,
    })

    async def metadata_failure(*args):
        raise RuntimeError('code lookup unavailable')

    monkeypatch.setattr(app_module.background_analyzer, 'update_code_availability', metadata_failure)
    response = await app_module.get_paper_analysis('cached')
    events = [event async for event in response.body_iterator]
    assert events[-1]['event'] == 'done'
    assert '论文解决的任务' in events[-2]['data']
    assert '总结而言' in events[-2]['data']
    assert fake_llm.calls == []


@pytest.mark.asyncio
async def test_cached_analysis_still_streams_when_normalization_write_fails(monkeypatch):
    fake_llm = AlwaysFailLlm()
    configure_paper_analysis_dependencies(monkeypatch, fake_llm, [])
    monkeypatch.setattr(app_module, 'get_or_fetch_paper_info', lambda _: {
        'id': 'cached-write-failure',
        'llm_response': 'old cached report',
    })
    monkeypatch.setattr(
        app_module,
        'normalize_llm_markdown',
        lambda *_args, **_kwargs: 'normalized cached report',
    )

    def write_failure(*_args):
        raise RuntimeError('database write unavailable')

    monkeypatch.setattr(app_module, 'update_llm_response', write_failure)
    response = await app_module.get_paper_analysis('cached-write-failure')
    events = [event async for event in response.body_iterator]

    assert events[-2] == {'data': 'normalized cached report'}
    assert events[-1] == {'event': 'done', 'data': ''}
    assert fake_llm.calls == []


@pytest.mark.asyncio
async def test_generated_analysis_finishes_when_persistence_fails(monkeypatch):
    fake_llm = RetryThenSucceedLlm()
    configure_paper_analysis_dependencies(monkeypatch, fake_llm, [])

    def write_failure(*_args):
        raise RuntimeError('database write unavailable')

    monkeypatch.setattr(app_module, 'update_llm_response', write_failure)
    response = await app_module.get_paper_analysis('generated-write-failure', reanalyze=True)
    events = [event async for event in response.body_iterator]

    assert any('暂未保存' in event.get('data', '') for event in events)
    assert events[-2] == {'event': 'final', 'data': COMPLETE_REPORT}
    assert events[-1] == {'event': 'done', 'data': ''}


@pytest.mark.asyncio
async def test_generated_analysis_does_not_wait_for_code_metadata_enrichment(monkeypatch):
    fake_llm = RetryThenSucceedLlm()
    configure_paper_analysis_dependencies(monkeypatch, fake_llm, [])
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingBackgroundAnalyzer:
        async def update_code_availability(self, paper_info, response):
            del paper_info, response
            started.set()
            await release.wait()

    monkeypatch.setattr(app_module, 'background_analyzer', BlockingBackgroundAnalyzer())
    response = await app_module.get_paper_analysis('background-code-check', reanalyze=True)
    events = [event async for event in response.body_iterator]

    assert events[-1] == {'event': 'done', 'data': ''}
    await asyncio.wait_for(started.wait(), timeout=0.2)
    task = app_module.code_availability_tasks['background-code-check']
    assert not task.done()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
