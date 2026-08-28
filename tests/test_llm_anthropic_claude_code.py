import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import llm as llm_module


def test_claude_code_payload_adds_required_identity_and_preserves_system_prompt():
    payload = llm_module._claude_code_payload(
        "claude-opus-5",
        [
            {"role": "system", "content": "Academic research assistant."},
            {"role": "user", "content": "Summarize this paper."},
        ],
        {"max_completion_tokens": 321, "temperature": 0.3},
    )

    assert payload["model"] == "claude-opus-5"
    assert payload["max_tokens"] == 321
    assert payload["stream"] is True
    assert payload["temperature"] == 0.3
    assert payload["system"][0]["text"] == llm_module.CLAUDE_CODE_SYSTEM_PROMPT
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert payload["system"][1]["text"] == "Academic research assistant."
    assert payload["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    metadata = json.loads(payload["metadata"]["user_id"])
    assert len(metadata["device_id"]) == 64
    assert metadata["account_uuid"] == ""
    assert metadata["session_id"]


def test_private_provider_parameters_are_not_forwarded():
    managed = llm_module.ManagedLLM()
    config = {
        "default_parameters": {
            "_api_protocol": llm_module.ANTHROPIC_CLAUDE_CODE_PROTOCOL,
            "max_tokens": 2048,
        }
    }

    assert managed._uses_anthropic_claude_code(config)
    assert managed._default_parameters(config) == {"max_tokens": 2048}


def test_selected_models_honor_the_configured_provider_transport():
    base_config = {
        "default_parameters": {
            "_api_protocol": llm_module.ANTHROPIC_CLAUDE_CODE_PROTOCOL,
        }
    }

    assert llm_module._provider_api_protocol({**base_config, "model_name": "claude-opus-5"}) == (
        llm_module.ANTHROPIC_CLAUDE_CODE_PROTOCOL
    )
    assert llm_module._provider_api_protocol({**base_config, "model_name": "glm-5.3"}) == (
        llm_module.ANTHROPIC_CLAUDE_CODE_PROTOCOL
    )
    assert llm_module._provider_api_protocol(
        {**base_config, "model_name": "deepseek-ai/DeepSeek-V3"}
    ) == llm_module.ANTHROPIC_CLAUDE_CODE_PROTOCOL


@pytest.mark.asyncio
async def test_managed_llm_claude_code_transport_reads_anthropic_sse(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_lines(self):
            yield 'event: message_start'
            yield 'data: {"type":"message_start","message":{"usage":{"input_tokens":12,"output_tokens":0}}}'
            yield 'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"check"}}'
            yield 'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"OK"}}'
            yield 'data: {"type":"message_delta","usage":{"output_tokens":2}}'
            yield 'data: {"type":"message_stop"}'

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, *, headers, json):
            captured.update(
                method=method,
                url=url,
                headers=headers,
                payload=json,
            )
            return FakeResponse()

    config = {
        "id": "provider-id",
        "provider_key": "sub2api",
        "name": "Sub2API",
        "base_url": "https://sub2api.example/v1",
        "api_key": "secret-client-key",
        "model_name": "claude-opus-5",
        "default_parameters": {
            "_api_protocol": llm_module.ANTHROPIC_CLAUDE_CODE_PROTOCOL,
            "max_tokens": 256,
        },
    }
    managed = llm_module.ManagedLLM()
    monkeypatch.setattr(managed, "_get_active_config", lambda: config)
    monkeypatch.setattr(llm_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(llm_module, "_record_llm_usage", lambda *args, **kwargs: None)

    events = []
    async for event in managed.chat_stream_events([{"role": "user", "content": "hi"}]):
        events.append((event.kind, event.content))

    assert events == [("reasoning", "check"), ("content", "OK")]
    assert captured["method"] == "POST"
    assert captured["url"] == "https://sub2api.example/v1/messages"
    assert captured["headers"]["x-api-key"] == "secret-client-key"
    assert captured["headers"]["user-agent"].startswith("claude-cli/")
    assert captured["payload"]["model"] == "claude-opus-5"
    assert captured["payload"]["stream"] is True
