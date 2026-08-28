import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import llm as llm_module
from llm import ManagedLLM
from app import public_active_llm_config, public_selectable_llm_provider


class FakeCompletions:
    def __init__(self, fail_first: bool = False, usage=None, model: str | None = None):
        self.calls = []
        self.fail_first = fail_first
        self.usage = usage
        self.model = model

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_first and len(self.calls) == 1:
            raise ValueError("max_tokens unsupported")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="7")),
            ],
            usage=self.usage,
            model=self.model,
        )


class FakeClient:
    def __init__(self, completions: FakeCompletions):
        self.chat = SimpleNamespace(completions=completions)


def managed_llm_with_fake_client(completions: FakeCompletions) -> ManagedLLM:
    llm = ManagedLLM()
    llm._get_active_config = lambda: {
        "id": "provider-1",
        "name": "Test Provider",
        "base_url": "https://example.test/v1",
        "api_key": "test-key",
        "model_name": "test-model",
        "default_parameters": {},
    }
    llm._client_for_config = lambda config: FakeClient(completions)
    return llm


def test_public_active_llm_config_exposes_display_fields_only():
    payload = public_active_llm_config(
        {
            "provider_key": "deepseek",
            "name": "DeepSeek",
            "base_url": "https://api.deepseek.com",
            "api_key": "secret-key",
            "model_name": "deepseek-v4-pro",
        }
    )

    assert payload == {
        "configured": True,
        "provider_key": "deepseek",
        "provider_name": "DeepSeek",
        "model_name": "deepseek-v4-pro",
    }
    assert "api_key" not in payload
    assert "base_url" not in payload


def test_public_selectable_llm_provider_exposes_models_without_credentials():
    payload = public_selectable_llm_provider(
        {
            "id": "provider-1",
            "provider_key": "sub2api",
            "name": "Sub2API",
            "base_url": "https://sub2api.example/v1",
            "api_key": "secret-key",
            "is_active": True,
            "active_model": "claude-opus-5",
            "models": [
                {
                    "id": "model-1",
                    "provider_id": "provider-1",
                    "model_name": "deepseek-v3",
                    "display_name": "DeepSeek V3",
                    "is_enabled": True,
                },
                {
                    "id": "model-2",
                    "provider_id": "provider-1",
                    "model_name": "disabled-model",
                    "is_enabled": False,
                },
            ],
        }
    )

    assert payload["models"] == [
        {
            "id": "model-1",
            "provider_id": "provider-1",
            "model_name": "deepseek-v3",
            "display_name": "DeepSeek V3",
        }
    ]
    assert "api_key" not in payload
    assert "base_url" not in payload


def test_managed_llm_selects_an_enabled_model_without_changing_global_default(monkeypatch):
    provider = {
        "id": "provider-1",
        "provider_key": "sub2api",
        "name": "Sub2API",
        "base_url": "https://sub2api.example/v1",
        "api_key": "secret-key",
        "is_enabled": True,
        "active_model": "claude-opus-5",
        "default_parameters": {"_api_protocol": "anthropic_claude_code"},
        "models": [
            {
                "model_name": "claude-opus-5",
                "is_enabled": True,
            },
            {
                "model_name": "deepseek-v3",
                "is_enabled": True,
            },
        ],
    }
    monkeypatch.setattr("database.get_llm_provider", lambda provider_id: provider)

    managed = ManagedLLM()
    selected = managed.select("provider-1", "deepseek-v3")

    assert selected.public_config() == {
        "provider_id": "provider-1",
        "provider_key": "sub2api",
        "provider_name": "Sub2API",
        "model_name": "deepseek-v3",
    }
    assert managed._config_override is None


def test_managed_llm_rejects_disabled_or_unknown_selected_model(monkeypatch):
    monkeypatch.setattr(
        "database.get_llm_provider",
        lambda provider_id: {
            "id": provider_id,
            "name": "Sub2API",
            "base_url": "https://sub2api.example/v1",
            "api_key": "secret-key",
            "is_enabled": True,
            "active_model": "claude-opus-5",
            "models": [{"model_name": "claude-opus-5", "is_enabled": True}],
        },
    )

    with pytest.raises(RuntimeError, match="不存在或已停用"):
        ManagedLLM().select("provider-1", "glm-unknown")


def test_extract_llm_usage_tokens_reads_cache_fields():
    tokens = llm_module.extract_llm_usage_tokens(
        SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=35,
            total_tokens=155,
            cache_creation_input_tokens=18,
            prompt_tokens_details=SimpleNamespace(cached_tokens=42),
        )
    )

    assert tokens.input_tokens == 120
    assert tokens.output_tokens == 35
    assert tokens.cache_input_tokens == 18
    assert tokens.cache_output_tokens == 42
    assert tokens.total_tokens == 155


@pytest.mark.asyncio
async def test_managed_llm_chat_records_usage(monkeypatch):
    usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=4,
        prompt_tokens_details=SimpleNamespace(cached_tokens=3),
    )
    completions = FakeCompletions(usage=usage, model="actual-model")
    llm = managed_llm_with_fake_client(completions)
    records = []

    monkeypatch.setattr(
        llm_module,
        "_record_llm_usage",
        lambda recorded_usage, **context: records.append((recorded_usage, context)),
    )

    output = await llm.chat([{"role": "user", "content": "hello"}], _usage_context="paper_chat")

    assert output == "7"
    assert records == [
        (
            usage,
            {
                "provider_id": "provider-1",
                "provider_key": None,
                "provider_name": "Test Provider",
                "model_name": "actual-model",
                "request_type": "paper_chat",
            },
        )
    ]


@pytest.mark.asyncio
async def test_managed_llm_analysis_uses_stable_default_temperature():
    completions = FakeCompletions()
    llm = managed_llm_with_fake_client(completions)

    output = await llm.get_response("paper content")

    assert output == "7"
    assert completions.calls[0]["temperature"] == llm_module.DEFAULT_ANALYSIS_TEMPERATURE
    assert llm_module.DEFAULT_ANALYSIS_TEMPERATURE == 0.3


@pytest.mark.asyncio
async def test_one_token_uses_max_tokens_limit():
    completions = FakeCompletions()
    llm = managed_llm_with_fake_client(completions)

    result = await llm.test_one_token()

    assert result["provider_name"] == "Test Provider"
    assert result["model_name"] == "test-model"
    assert result["output"] == "7"
    assert completions.calls[0]["max_tokens"] == 1


@pytest.mark.asyncio
async def test_one_token_falls_back_to_max_completion_tokens():
    completions = FakeCompletions(fail_first=True)
    llm = managed_llm_with_fake_client(completions)

    result = await llm.test_one_token()

    assert result["output"] == "7"
    assert completions.calls[0]["max_tokens"] == 1
    assert completions.calls[1]["max_completion_tokens"] == 1
