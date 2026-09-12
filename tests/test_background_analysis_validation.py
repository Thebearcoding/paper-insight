import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import background_tasks


@pytest.mark.asyncio
async def test_background_analysis_does_not_persist_empty_or_partial_report(monkeypatch):
    saved = []
    calls = []

    async def response(*args):
        calls.append(True)
        return "## 1. 论文解决的任务\n\n只有开头。"

    async def no_wait(*args):
        return None

    monkeypatch.setattr(background_tasks, "get_paper", lambda _: {"title": "Paper"})
    monkeypatch.setattr(background_tasks, "update_llm_response", lambda *args: saved.append(args))
    monkeypatch.setattr(background_tasks.asyncio, "sleep", no_wait)
    analyzer = background_tasks.BackgroundAnalyzer(SimpleNamespace(
        is_configured=lambda: True, get_response=response,
    ))
    assert not await analyzer.analyze_paper("paper-1")
    assert len(calls) == 3
    assert saved == []
    assert analyzer.current_paper_id is None
