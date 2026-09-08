import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from chat import ChatSession
from llm import LLMStreamChunk
import app as app_module


class FailingChatLlm:
    async def chat(self, messages, **kwargs):
        del messages, kwargs
        raise RuntimeError("upstream disconnected")

    async def chat_stream_events(self, messages, **kwargs):
        del messages, kwargs
        yield LLMStreamChunk(kind="content", content="partial reply")
        raise RuntimeError("upstream disconnected")


@pytest.mark.asyncio
async def test_chat_session_discards_a_failed_non_streaming_turn():
    session = ChatSession(FailingChatLlm())

    with pytest.raises(RuntimeError, match="upstream disconnected"):
        await session.send("retry me")

    assert session.history == []


@pytest.mark.asyncio
async def test_chat_session_discards_a_failed_streaming_turn():
    session = ChatSession(FailingChatLlm())
    chunks = []

    with pytest.raises(RuntimeError, match="upstream disconnected"):
        async for chunk in session.send_stream_events("retry me"):
            chunks.append(chunk.content)

    assert chunks == ["partial reply"]
    assert session.history == []


@pytest.mark.asyncio
async def test_paper_chat_reports_a_stream_failure_without_closing_sse(monkeypatch):
    session = ChatSession(FailingChatLlm())
    monkeypatch.setattr(app_module, "ensure_llm_configured", lambda: None)
    monkeypatch.setattr(app_module, "assert_chat_owner", lambda *_args: {"id": "session-1"})
    monkeypatch.setitem(app_module.chat_sessions, "session-1", session)

    response = await app_module.chat_with_paper(
        "paper-1",
        app_module.ChatRequest(message="what happened?", session_id="session-1"),
        {"id": "user-1"},
    )
    events = [event async for event in response.body_iterator]

    assert events[-1] == {
        "event": "error",
        "data": "模型连接中断或对话生成失败，请稍后重试",
    }
    assert session.history == []
