import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app as app_module


class FakeSelectedLlm:
    def is_configured(self) -> bool:
        return True


class FakeLlmManager:
    def __init__(self, selected):
        self.selected = selected
        self.calls = []

    def select(self, provider_id=None, model_name=None):
        self.calls.append((provider_id, model_name))
        return self.selected


def test_select_configured_llm_uses_request_model(monkeypatch):
    selected = FakeSelectedLlm()
    manager = FakeLlmManager(selected)
    monkeypatch.setattr(app_module, "llm", manager)

    result = app_module.select_configured_llm("provider-1", "deepseek-v4-flash")

    assert result is selected
    assert manager.calls == [("provider-1", "deepseek-v4-flash")]


@pytest.mark.asyncio
async def test_zotero_chat_runtime_uses_selected_llm(monkeypatch):
    selected = FakeSelectedLlm()
    monkeypatch.setattr(
        app_module,
        "get_zotero_item",
        lambda user_id, item_key: {"item_key": item_key, "llm_response": "已有分析"},
    )

    async def fake_context(user_id, item):
        return "论文全文", "zotero-fulltext", None

    monkeypatch.setattr(app_module, "load_zotero_reading_context", fake_context)
    monkeypatch.setattr(app_module, "get_zotero_chat_messages", lambda session_id: [])

    session = await app_module.build_zotero_chat_runtime(
        "user-1",
        "item-1",
        "session-1",
        session_exists=True,
        chat_llm=selected,
    )

    assert session.llm is selected
    assert "论文全文" in session.context
    assert "已有分析" in session.context
