from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError
import asyncio
import copy
import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from typing import Any
import httpx
from config import settings
from prompt import PAPER_ANALYSIS_PROMPT

MISSING_API_KEY_PLACEHOLDER = "missing-api-key"
DEFAULT_ANALYSIS_TEMPERATURE = 0.3
ANTHROPIC_CLAUDE_CODE_PROTOCOL = "anthropic_claude_code"
CLAUDE_CODE_SYSTEM_PROMPT = "You are Claude Code, Anthropic's official CLI for Claude."
CLAUDE_CODE_BETA_HEADER = (
    "claude-code-20250219,"
    "interleaved-thinking-2025-05-14,"
    "fine-grained-tool-streaming-2025-05-14"
)
CLAUDE_CODE_DEVICE_ID = secrets.token_hex(32)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMStreamChunk:
    kind: str
    content: str


@dataclass(frozen=True)
class LLMUsageTokens:
    input_tokens: int
    output_tokens: int
    cache_input_tokens: int
    cache_output_tokens: int
    total_tokens: int


def _provider_api_protocol(config: dict) -> str:
    params = config.get("default_parameters") or {}
    if not isinstance(params, dict):
        return "openai"
    return str(params.get("_api_protocol") or "openai").strip().lower()


def _claude_code_headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": CLAUDE_CODE_BETA_HEADER,
        "content-type": "application/json",
        "user-agent": "claude-cli/2.1.220 (external, cli)",
        "x-stainless-lang": "js",
        "x-stainless-package-version": "0.94.0",
        "x-stainless-os": "Linux",
        "x-stainless-arch": "arm64",
        "x-stainless-runtime": "node",
        "x-stainless-runtime-version": "v24.3.0",
        "x-stainless-retry-count": "0",
        "x-stainless-timeout": "600",
        "x-app": "cli",
        "anthropic-dangerous-direct-browser-access": "true",
    }


def _content_blocks(content: Any) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return [{"type": "text", "text": str(content or "")}]

    blocks: list[dict] = []
    for item in content:
        if isinstance(item, str):
            blocks.append({"type": "text", "text": item})
            continue
        if not isinstance(item, dict):
            blocks.append({"type": "text", "text": str(item)})
            continue
        block_type = str(item.get("type") or "")
        if block_type in {"text", "image", "tool_use", "tool_result", "thinking"}:
            blocks.append(copy.deepcopy(item))
        elif block_type == "image_url":
            blocks.append({"type": "text", "text": "[Image attachment omitted by the LLM gateway]"})
        else:
            blocks.append({"type": "text", "text": json.dumps(item, ensure_ascii=False)})
    return blocks or [{"type": "text", "text": ""}]


def _claude_code_payload(model: str, messages: list, params: dict) -> dict:
    request_params = dict(params)
    max_tokens = request_params.pop("max_tokens", None)
    if max_tokens is None:
        max_tokens = request_params.pop("max_completion_tokens", 4096)
    try:
        max_tokens = max(int(max_tokens), 1)
    except (TypeError, ValueError):
        max_tokens = 4096

    system_blocks: list[dict] = [
        {
            "type": "text",
            "text": CLAUDE_CODE_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    anthropic_messages: list[dict] = []
    for message in messages:
        role = str(message.get("role") or "user").lower()
        blocks = _content_blocks(message.get("content"))
        if role == "system":
            system_blocks.extend(blocks)
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        if anthropic_messages and anthropic_messages[-1]["role"] == role:
            anthropic_messages[-1]["content"].extend(blocks)
        else:
            anthropic_messages.append({"role": role, "content": blocks})

    if not anthropic_messages:
        anthropic_messages.append({"role": "user", "content": [{"type": "text", "text": "hi"}]})

    for message in reversed(anthropic_messages):
        if message["role"] != "user":
            continue
        for block in reversed(message["content"]):
            if block.get("type") == "text":
                block.setdefault("cache_control", {"type": "ephemeral"})
                break
        break

    metadata_user_id = json.dumps(
        {
            "device_id": CLAUDE_CODE_DEVICE_ID,
            "account_uuid": "",
            "session_id": str(uuid.uuid4()),
        },
        separators=(",", ":"),
    )
    payload = {
        "model": model,
        "messages": anthropic_messages,
        "system": system_blocks,
        "metadata": {"user_id": metadata_user_id},
        "max_tokens": max_tokens,
        "stream": True,
    }

    supported = {
        "temperature",
        "top_p",
        "top_k",
        "tools",
        "tool_choice",
        "thinking",
        "context_management",
    }
    for key in supported:
        if key in request_params and request_params[key] is not None:
            payload[key] = request_params[key]
    if request_params.get("stop") is not None:
        stop = request_params["stop"]
        payload["stop_sequences"] = stop if isinstance(stop, list) else [stop]
    return payload


def _object_to_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {
            key: _object_to_dict(item) if isinstance(item, dict) or hasattr(item, "model_dump") or hasattr(item, "__dict__") else item
            for key, item in value.items()
        }
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(exclude_none=True)
        return _object_to_dict(dumped)
    if hasattr(value, "__dict__"):
        return {
            key: _object_to_dict(item) if isinstance(item, dict) or hasattr(item, "model_dump") or hasattr(item, "__dict__") else item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return {}


def _nested_value(data: dict, path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            current = getattr(current, key, None)
        if current is None:
            return None
    return current


def _first_int(data: dict, *paths: tuple[str, ...]) -> int:
    for path in paths:
        value = _nested_value(data, path)
        if value is None or isinstance(value, bool):
            continue
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            continue
    return 0


def extract_llm_usage_tokens(usage: Any) -> LLMUsageTokens | None:
    data = _object_to_dict(usage)
    if not data:
        return None

    input_tokens = _first_int(
        data,
        ("prompt_tokens",),
        ("input_tokens",),
    )
    output_tokens = _first_int(
        data,
        ("completion_tokens",),
        ("output_tokens",),
    )
    cache_input_tokens = _first_int(
        data,
        ("cache_creation_input_tokens",),
        ("cache_write_input_tokens",),
        ("cached_write_input_tokens",),
        ("prompt_tokens_details", "cache_creation_tokens"),
        ("prompt_tokens_details", "cache_write_tokens"),
        ("input_tokens_details", "cache_creation_tokens"),
        ("input_tokens_details", "cache_write_tokens"),
        ("input_token_details", "cache_creation"),
        ("input_token_details", "cache_creation_input_tokens"),
    )
    cache_output_tokens = _first_int(
        data,
        ("cache_read_input_tokens",),
        ("cache_hit_input_tokens",),
        ("cached_input_tokens",),
        ("prompt_cache_hit_tokens",),
        ("prompt_tokens_details", "cached_tokens"),
        ("input_tokens_details", "cached_tokens"),
        ("input_token_details", "cache_read"),
        ("input_token_details", "cache_read_input_tokens"),
    )
    total_tokens = _first_int(
        data,
        ("total_tokens",),
    )
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens
    if (
        input_tokens == 0
        and output_tokens == 0
        and cache_input_tokens == 0
        and cache_output_tokens == 0
        and total_tokens == 0
    ):
        return None

    return LLMUsageTokens(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_input_tokens=cache_input_tokens,
        cache_output_tokens=cache_output_tokens,
        total_tokens=total_tokens,
    )


def _response_usage(response: Any) -> Any:
    if response is None:
        return None
    if isinstance(response, dict):
        return response.get("usage")
    return getattr(response, "usage", None)


def _response_model(response: Any, default_model: str) -> str:
    if response is None:
        return default_model
    if isinstance(response, dict):
        return str(response.get("model") or default_model)
    return str(getattr(response, "model", None) or default_model)


def _pop_usage_context(params: dict, default_request_type: str) -> str:
    request_type = params.pop("_usage_context", default_request_type)
    return str(request_type or default_request_type)


def _pop_analysis_instruction(params: dict) -> str:
    instruction = params.pop("_analysis_instruction", None)
    return str(instruction or PAPER_ANALYSIS_PROMPT)


def _stream_params_with_usage(params: dict) -> dict:
    next_params = dict(params)
    stream_options = next_params.get("stream_options")
    if isinstance(stream_options, dict):
        next_params["stream_options"] = {**stream_options, "include_usage": True}
    elif stream_options is None:
        next_params["stream_options"] = {"include_usage": True}
    return next_params


async def _create_streaming_completion(client: AsyncOpenAI, params: dict):
    params_with_usage = _stream_params_with_usage(params)
    try:
        return await client.chat.completions.create(**params_with_usage)
    except APIError as exc:
        if params_with_usage != params and "stream_options" in str(exc).lower():
            logger.warning("LLM provider rejected stream_options.include_usage; retrying stream without usage")
            return await client.chat.completions.create(**params)
        raise


def _record_llm_usage(
    usage: Any,
    *,
    provider_id: str | None,
    provider_key: str | None,
    provider_name: str | None,
    model_name: str,
    request_type: str,
) -> None:
    tokens = extract_llm_usage_tokens(usage)
    if not tokens:
        return
    try:
        from database import record_llm_token_usage

        record_llm_token_usage(
            provider_id=provider_id,
            provider_key=provider_key,
            provider_name=provider_name,
            model_name=model_name,
            request_type=request_type,
            input_tokens=tokens.input_tokens,
            output_tokens=tokens.output_tokens,
            cache_input_tokens=tokens.cache_input_tokens,
            cache_output_tokens=tokens.cache_output_tokens,
            total_tokens=tokens.total_tokens,
        )
    except Exception as exc:
        logger.warning("LLM token usage 记录失败: %s", exc)


def _delta_to_dict(delta) -> dict:
    if delta is None:
        return {}
    if isinstance(delta, dict):
        return delta
    if hasattr(delta, "model_dump"):
        return delta.model_dump(exclude_none=True)
    return {}


def _extract_delta_text(delta, *field_names: str) -> str | None:
    delta_dict = _delta_to_dict(delta)
    model_extra = getattr(delta, "model_extra", None)

    for field_name in field_names:
        value = getattr(delta, field_name, None)
        if not value:
            value = delta_dict.get(field_name)
        if not value and isinstance(model_extra, dict):
            value = model_extra.get(field_name)
        if value:
            return str(value)
    return None


def iter_llm_stream_chunks(chunk):
    if not getattr(chunk, "choices", None):
        return

    delta = chunk.choices[0].delta
    reasoning = _extract_delta_text(delta, "reasoning", "reasoning_content")
    if reasoning:
        yield LLMStreamChunk(kind="reasoning", content=reasoning)

    content = _extract_delta_text(delta, "content")
    if content:
        yield LLMStreamChunk(kind="content", content=content)

async def retry_on_error(func, max_retries=3, delay=1.0):
    """Simple retry wrapper for async functions"""
    for attempt in range(max_retries):
        try:
            return await func()
        except (APIError, APITimeoutError, RateLimitError) as e:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(delay * (attempt + 1))

class BaseLLM:
    def __init__(self, model: str, api_key: str = None, base_url: str = None):
        self.api_key = api_key if api_key else settings.llm.openai_api_key
        self.model = model
        self.client = AsyncOpenAI(api_key=self.api_key or MISSING_API_KEY_PLACEHOLDER, base_url=base_url)

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key != MISSING_API_KEY_PLACEHOLDER)

    async def get_response(self, prompt: str, **kwargs) -> str:
        params = dict(kwargs)
        request_type = _pop_usage_context(params, "analysis")
        analysis_instruction = _pop_analysis_instruction(params)
        params.setdefault("temperature", DEFAULT_ANALYSIS_TEMPERATURE)

        async def _call():
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant for academic research."},
                    {"role": "user", "content": prompt + "\n\n" + analysis_instruction}
                ],
                **params,
            )
            _record_llm_usage(
                _response_usage(response),
                provider_id=None,
                provider_key=None,
                provider_name=self.__class__.__name__,
                model_name=_response_model(response, self.model),
                request_type=request_type,
            )
            return response.choices[0].message.content
        return await retry_on_error(_call)

    async def get_response_stream_events(self, prompt: str, **kwargs):
        params = dict(kwargs)
        request_type = _pop_usage_context(params, "analysis_stream")
        analysis_instruction = _pop_analysis_instruction(params)
        params.setdefault("temperature", DEFAULT_ANALYSIS_TEMPERATURE)
        response = await _create_streaming_completion(
            self.client,
            {
                "model": self.model,
                "stream": True,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant for academic research."},
                    {"role": "user", "content": prompt + "\n\n" + analysis_instruction}
                ],
                **params,
            },
        )
        usage = None
        model_name = self.model
        async for chunk in response:
            usage = _response_usage(chunk) or usage
            model_name = _response_model(chunk, model_name)
            for stream_chunk in iter_llm_stream_chunks(chunk):
                yield stream_chunk
        _record_llm_usage(
            usage,
            provider_id=None,
            provider_key=None,
            provider_name=self.__class__.__name__,
            model_name=model_name,
            request_type=request_type,
        )

    async def get_response_stream(self, prompt: str, **kwargs):
        async for stream_chunk in self.get_response_stream_events(prompt, **kwargs):
            if stream_chunk.kind == "content":
                yield stream_chunk.content

    async def chat(self, messages: list, **kwargs) -> str:
        params = dict(kwargs)
        request_type = _pop_usage_context(params, "chat")
        params.setdefault("temperature", 1.0)

        async def _call():
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                **params,
            )
            _record_llm_usage(
                _response_usage(response),
                provider_id=None,
                provider_key=None,
                provider_name=self.__class__.__name__,
                model_name=_response_model(response, self.model),
                request_type=request_type,
            )
            return response.choices[0].message.content
        return await retry_on_error(_call)

    async def chat_stream_events(self, messages: list, **kwargs):
        params = dict(kwargs)
        request_type = _pop_usage_context(params, "chat_stream")
        params.setdefault("temperature", 1.0)
        response = await _create_streaming_completion(
            self.client,
            {
                "model": self.model,
                "stream": True,
                "messages": messages,
                **params,
            },
        )
        usage = None
        model_name = self.model
        async for chunk in response:
            usage = _response_usage(chunk) or usage
            model_name = _response_model(chunk, model_name)
            for stream_chunk in iter_llm_stream_chunks(chunk):
                yield stream_chunk
        _record_llm_usage(
            usage,
            provider_id=None,
            provider_key=None,
            provider_name=self.__class__.__name__,
            model_name=model_name,
            request_type=request_type,
        )

    async def chat_stream(self, messages: list, **kwargs):
        async for stream_chunk in self.chat_stream_events(messages, **kwargs):
            if stream_chunk.kind == "content":
                yield stream_chunk.content


class ManagedLLM:
    def _get_active_config(self) -> dict | None:
        from database import get_active_llm_config

        return get_active_llm_config()

    def is_configured(self) -> bool:
        try:
            config = self._get_active_config()
        except Exception as exc:
            logger.warning("LLM 配置读取失败: %s", exc)
            return False
        return bool(config and config.get("api_key") and config.get("model_name") and config.get("base_url"))

    def _client_for_config(self, config: dict) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=config.get("api_key") or MISSING_API_KEY_PLACEHOLDER,
            base_url=config.get("base_url"),
        )

    def _default_parameters(self, config: dict) -> dict:
        params = config.get("default_parameters") or {}
        if not isinstance(params, dict):
            return {}
        return {key: value for key, value in params.items() if not str(key).startswith("_")}

    def _uses_anthropic_claude_code(self, config: dict) -> bool:
        return _provider_api_protocol(config) == ANTHROPIC_CLAUDE_CODE_PROTOCOL

    def _anthropic_messages_url(self, config: dict) -> str:
        base_url = str(config.get("base_url") or "").strip().rstrip("/")
        if base_url.endswith("/v1/messages"):
            return base_url
        if base_url.endswith("/v1"):
            return base_url + "/messages"
        return base_url + "/v1/messages"

    async def _anthropic_claude_code_stream_events(
        self,
        config: dict,
        messages: list,
        params: dict,
        request_type: str,
    ):
        payload = _claude_code_payload(config["model_name"], messages, params)
        timeout = httpx.Timeout(180.0, connect=20.0)
        usage: dict[str, Any] = {}

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream(
                "POST",
                self._anthropic_messages_url(config),
                headers=_claude_code_headers(config["api_key"]),
                json=payload,
            ) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise RuntimeError(
                        f"LLM upstream returned HTTP {response.status_code}: {body[:1000]}"
                    )

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw_data = line[len("data:"):].strip()
                    if not raw_data or raw_data == "[DONE]":
                        continue
                    try:
                        event = json.loads(raw_data)
                    except json.JSONDecodeError:
                        logger.debug("忽略无法解析的 Anthropic SSE 数据行")
                        continue

                    event_type = event.get("type")
                    event_usage = event.get("usage")
                    if event_type == "message_start":
                        event_usage = (event.get("message") or {}).get("usage") or event_usage
                    if isinstance(event_usage, dict):
                        usage.update(event_usage)

                    if event_type == "error":
                        error = event.get("error") or {}
                        raise RuntimeError(str(error.get("message") or "LLM upstream stream error"))

                    if event_type == "content_block_start":
                        block = event.get("content_block") or {}
                        if block.get("type") == "text" and block.get("text"):
                            yield LLMStreamChunk(kind="content", content=str(block["text"]))
                        elif block.get("type") == "thinking" and block.get("thinking"):
                            yield LLMStreamChunk(kind="reasoning", content=str(block["thinking"]))
                        continue

                    if event_type != "content_block_delta":
                        continue
                    delta = event.get("delta") or {}
                    delta_type = delta.get("type")
                    if delta_type == "text_delta" and delta.get("text"):
                        yield LLMStreamChunk(kind="content", content=str(delta["text"]))
                    elif delta_type == "thinking_delta" and delta.get("thinking"):
                        yield LLMStreamChunk(kind="reasoning", content=str(delta["thinking"]))

        _record_llm_usage(
            usage,
            provider_id=str(config.get("id")) if config.get("id") else None,
            provider_key=config.get("provider_key"),
            provider_name=config.get("name"),
            model_name=config["model_name"],
            request_type=request_type,
        )

    def _require_config(self) -> dict:
        config = self._get_active_config()
        if not config or not config.get("api_key"):
            raise RuntimeError("LLM API key is not configured")
        if not config.get("model_name"):
            raise RuntimeError("LLM model is not configured")
        if not config.get("base_url"):
            raise RuntimeError("LLM base URL is not configured")
        return config

    def _parameters(self, config: dict, overrides: dict) -> dict:
        params = self._default_parameters(config)
        params.update(overrides)
        return params

    async def get_response(self, prompt: str, **kwargs) -> str:
        config = self._require_config()
        params = self._parameters(config, kwargs)
        request_type = _pop_usage_context(params, "analysis")
        analysis_instruction = _pop_analysis_instruction(params)
        params.setdefault("temperature", DEFAULT_ANALYSIS_TEMPERATURE)
        messages = [
            {"role": "system", "content": "You are a helpful assistant for academic research."},
            {"role": "user", "content": prompt + "\n\n" + analysis_instruction},
        ]

        if self._uses_anthropic_claude_code(config):
            chunks: list[str] = []
            async for chunk in self._anthropic_claude_code_stream_events(
                config,
                messages,
                params,
                request_type,
            ):
                if chunk.kind == "content":
                    chunks.append(chunk.content)
            return "".join(chunks)

        client = self._client_for_config(config)

        async def _call():
            response = await client.chat.completions.create(
                model=config["model_name"],
                messages=messages,
                **params,
            )
            _record_llm_usage(
                _response_usage(response),
                provider_id=str(config.get("id")) if config.get("id") else None,
                provider_key=config.get("provider_key"),
                provider_name=config.get("name"),
                model_name=_response_model(response, config["model_name"]),
                request_type=request_type,
            )
            return response.choices[0].message.content

        return await retry_on_error(_call)

    async def get_response_stream_events(self, prompt: str, **kwargs):
        config = self._require_config()
        params = self._parameters(config, kwargs)
        request_type = _pop_usage_context(params, "analysis_stream")
        analysis_instruction = _pop_analysis_instruction(params)
        params.setdefault("temperature", DEFAULT_ANALYSIS_TEMPERATURE)
        messages = [
            {"role": "system", "content": "You are a helpful assistant for academic research."},
            {"role": "user", "content": prompt + "\n\n" + analysis_instruction},
        ]

        if self._uses_anthropic_claude_code(config):
            async for chunk in self._anthropic_claude_code_stream_events(
                config,
                messages,
                params,
                request_type,
            ):
                yield chunk
            return

        client = self._client_for_config(config)
        response = await _create_streaming_completion(
            client,
            {
                "model": config["model_name"],
                "stream": True,
                "messages": messages,
                **params,
            },
        )
        usage = None
        model_name = config["model_name"]
        async for chunk in response:
            usage = _response_usage(chunk) or usage
            model_name = _response_model(chunk, model_name)
            for stream_chunk in iter_llm_stream_chunks(chunk):
                yield stream_chunk
        _record_llm_usage(
            usage,
            provider_id=str(config.get("id")) if config.get("id") else None,
            provider_key=config.get("provider_key"),
            provider_name=config.get("name"),
            model_name=model_name,
            request_type=request_type,
        )

    async def get_response_stream(self, prompt: str, **kwargs):
        async for stream_chunk in self.get_response_stream_events(prompt, **kwargs):
            if stream_chunk.kind == "content":
                yield stream_chunk.content

    async def chat(self, messages: list, **kwargs) -> str:
        config = self._require_config()
        params = self._parameters(config, kwargs)
        request_type = _pop_usage_context(params, "chat")
        params.setdefault("temperature", 1.0)

        if self._uses_anthropic_claude_code(config):
            chunks: list[str] = []
            async for chunk in self._anthropic_claude_code_stream_events(
                config,
                messages,
                params,
                request_type,
            ):
                if chunk.kind == "content":
                    chunks.append(chunk.content)
            return "".join(chunks)

        client = self._client_for_config(config)

        async def _call():
            response = await client.chat.completions.create(
                model=config["model_name"],
                messages=messages,
                **params,
            )
            _record_llm_usage(
                _response_usage(response),
                provider_id=str(config.get("id")) if config.get("id") else None,
                provider_key=config.get("provider_key"),
                provider_name=config.get("name"),
                model_name=_response_model(response, config["model_name"]),
                request_type=request_type,
            )
            return response.choices[0].message.content

        return await retry_on_error(_call)

    async def chat_stream_events(self, messages: list, **kwargs):
        config = self._require_config()
        params = self._parameters(config, kwargs)
        request_type = _pop_usage_context(params, "chat_stream")
        params.setdefault("temperature", 1.0)

        if self._uses_anthropic_claude_code(config):
            async for chunk in self._anthropic_claude_code_stream_events(
                config,
                messages,
                params,
                request_type,
            ):
                yield chunk
            return

        client = self._client_for_config(config)
        response = await _create_streaming_completion(
            client,
            {
                "model": config["model_name"],
                "stream": True,
                "messages": messages,
                **params,
            },
        )
        usage = None
        model_name = config["model_name"]
        async for chunk in response:
            usage = _response_usage(chunk) or usage
            model_name = _response_model(chunk, model_name)
            for stream_chunk in iter_llm_stream_chunks(chunk):
                yield stream_chunk
        _record_llm_usage(
            usage,
            provider_id=str(config.get("id")) if config.get("id") else None,
            provider_key=config.get("provider_key"),
            provider_name=config.get("name"),
            model_name=model_name,
            request_type=request_type,
        )

    async def chat_stream(self, messages: list, **kwargs):
        async for stream_chunk in self.chat_stream_events(messages, **kwargs):
            if stream_chunk.kind == "content":
                yield stream_chunk.content

    async def test_one_token(self) -> dict:
        config = self._require_config()
        messages = [{"role": "user", "content": "Output exactly one digit."}]
        base_params = self._default_parameters(config)

        if self._uses_anthropic_claude_code(config):
            chunks: list[str] = []
            params = {**base_params, "max_tokens": 128, "temperature": 0}
            async for chunk in self._anthropic_claude_code_stream_events(
                config,
                messages,
                params,
                "admin_test",
            ):
                if chunk.kind == "content":
                    chunks.append(chunk.content)
            return {
                "provider_id": str(config["id"]),
                "provider_name": config["name"],
                "model_name": config["model_name"],
                "output": "".join(chunks),
            }

        client = self._client_for_config(config)

        async def _call(params: dict):
            response = await client.chat.completions.create(
                model=config["model_name"],
                temperature=0,
                messages=messages,
                **params,
            )
            _record_llm_usage(
                _response_usage(response),
                provider_id=str(config.get("id")) if config.get("id") else None,
                provider_key=config.get("provider_key"),
                provider_name=config.get("name"),
                model_name=_response_model(response, config["model_name"]),
                request_type="admin_test",
            )
            return response.choices[0].message.content or ""

        try:
            params = {**base_params, "max_tokens": 1}
            params.pop("max_completion_tokens", None)
            output = await retry_on_error(lambda: _call(params), max_retries=1)
        except Exception:
            params = {**base_params, "max_completion_tokens": 1}
            params.pop("max_tokens", None)
            output = await retry_on_error(lambda: _call(params), max_retries=1)

        return {
            "provider_id": str(config["id"]),
            "provider_name": config["name"],
            "model_name": config["model_name"],
            "output": output,
        }


async def fetch_openai_compatible_model_names(base_url: str, api_key: str | None) -> list[str]:
    client = AsyncOpenAI(
        api_key=(api_key or MISSING_API_KEY_PLACEHOLDER),
        base_url=base_url.strip().rstrip("/"),
    )
    response = await client.models.list()
    names: list[str] = []
    for item in response.data:
        model_id = getattr(item, "id", None)
        if not model_id and isinstance(item, dict):
            model_id = item.get("id")
        if model_id:
            names.append(str(model_id))
    return sorted(set(names))
    
class SiliconflowLLM(BaseLLM):
    def __init__(self, api_key: str = None, base_url: str = None, model: str = "Pro/MiniMaxAI/MiniMax-M2.5"):
        self.api_key = api_key if api_key else settings.llm.siliconflow_api_key
        self.base_url = base_url if base_url else "https://api.siliconflow.cn/v1"
        self.model = model
        self.client = AsyncOpenAI(api_key=self.api_key or MISSING_API_KEY_PLACEHOLDER, base_url=self.base_url)

class OpenRouterLLM(BaseLLM):
    def __init__(self, api_key: str = None, base_url: str = None, model: str = "stepfun/step-3.5-flash:free", max_completion_tokens: int = 12000):
        self.api_key = api_key if api_key else settings.llm.open_router_api_key
        self.base_url = base_url if base_url else "https://openrouter.ai/api/v1"
        self.model = model
        self.max_completion_tokens = max_completion_tokens
        self.client = AsyncOpenAI(api_key=self.api_key or MISSING_API_KEY_PLACEHOLDER, base_url=self.base_url)

    async def get_response(self, prompt: str, **kwargs) -> str:
        kwargs.setdefault('max_completion_tokens', self.max_completion_tokens)
        return await super().get_response(prompt, **kwargs)

    async def get_response_stream(self, prompt: str, **kwargs):
        kwargs.setdefault('max_completion_tokens', self.max_completion_tokens)
        async for chunk in super().get_response_stream(prompt, **kwargs):
            yield chunk

    async def get_response_stream_events(self, prompt: str, **kwargs):
        kwargs.setdefault('max_completion_tokens', self.max_completion_tokens)
        async for chunk in super().get_response_stream_events(prompt, **kwargs):
            yield chunk

    async def chat(self, messages: list, **kwargs) -> str:
        kwargs.setdefault('max_completion_tokens', self.max_completion_tokens)
        return await super().chat(messages, **kwargs)

    async def chat_stream(self, messages: list, **kwargs):
        kwargs.setdefault('max_completion_tokens', self.max_completion_tokens)
        async for chunk in super().chat_stream(messages, **kwargs):
            yield chunk

    async def chat_stream_events(self, messages: list, **kwargs):
        kwargs.setdefault('max_completion_tokens', self.max_completion_tokens)
        async for chunk in super().chat_stream_events(messages, **kwargs):
            yield chunk

class StepLLM(BaseLLM):
    def __init__(self, api_key: str = None, base_url: str = None, model: str = "step-3.5-flash-2603"):
        self.api_key = api_key if api_key else settings.llm.step_api_key
        self.base_url = base_url if base_url else settings.llm.step_base_url
        self.model = model
        self.client = AsyncOpenAI(api_key=self.api_key or MISSING_API_KEY_PLACEHOLDER, base_url=self.base_url)

class ArkPlanLLM(BaseLLM):
    def __init__(self, api_key: str = None, base_url: str = None, model: str = "ark-code-latest"):
        self.api_key = api_key if api_key else settings.llm.arkplan_api_key
        self.base_url = base_url if base_url else "https://ark.cn-beijing.volces.com/api/coding/v3"
        self.model = model
        self.client = AsyncOpenAI(api_key=self.api_key or MISSING_API_KEY_PLACEHOLDER, base_url=self.base_url)

class DeepSeekLLM(BaseLLM):
    def __init__(self, api_key: str = None, base_url: str = None, model: str = "deepseek-v4-flash"):
        self.api_key = api_key if api_key else settings.llm.deepseek_api_key
        self.base_url = base_url if base_url else "https://api.deepseek.com"
        self.model = model
        self.client = AsyncOpenAI(api_key=self.api_key or MISSING_API_KEY_PLACEHOLDER, base_url=self.base_url)

if __name__ == "__main__":
    import asyncio

    async def test():
        try:
            llm = StepLLM()
            print(f"API Key configured: {bool(llm.api_key)}")
            print(f"Base URL: {llm.base_url}")
            print(f"Model: {llm.model}")

            prompt = "请简要介绍一下Transformer模型。"

            # 测试非流式响应
            print("\n开始测试非流式响应...")
            response = await llm.get_response(prompt)
            print("Response:", response)

            # 测试流式响应
            print("\n开始测试流式响应...")
            print("Streamed Response:", end=" ")
            async for chunk in llm.get_response_stream(prompt):
                print(chunk, end="", flush=True)
            print("\n\n测试完成！")
        except Exception as e:
            print(f"错误: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

    asyncio.run(test())
