from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pdf_translation
from pdf_translation import (
    EXPIRED_ERROR_MESSAGE,
    TranslationError,
    TranslationServiceConfig,
    _build_translate_payload,
    query_translation_progress,
    remote_result_state,
    stream_translation_result,
    submit_translation,
)


def _cfg(**overrides) -> TranslationServiceConfig:
    defaults = {
        "base_url": "http://pdf2zh:11008",
        "service": "google",
        "lang_in": "en",
        "lang_out": "zh",
        "openai_base_url": None,
        "openai_api_key": None,
        "openai_model": None,
    }
    defaults.update(overrides)
    return TranslationServiceConfig(**defaults)


class FakeStreamContext:
    """Stands in for httpx's `client.stream(...)` async context manager."""

    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        return None


class FakeStreamResponse:
    def __init__(self, chunks=(b"%PDF-mono",), status_code=200):
        self.status_code = status_code
        self._chunks = chunks

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class FakeAsyncClient:
    def __init__(self, post_response=None, get_responses=None, stream_responses=None):
        self.post_response = post_response
        self.get_responses = get_responses or {}
        self.stream_responses = stream_responses or {}
        self.post_calls: list[dict] = []
        self.get_calls: list[str] = []
        self.stream_calls: list[dict] = []
        self.delete_calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        self.post_calls.append({"url": url, **kwargs})
        if isinstance(self.post_response, Exception):
            raise self.post_response
        return self.post_response

    async def get(self, url, **kwargs):
        self.get_calls.append(url)
        if url not in self.get_responses:
            raise AssertionError(f"unexpected GET {url}")
        response = self.get_responses[url]
        if isinstance(response, Exception):
            raise response
        return response

    async def delete(self, url, **kwargs):
        self.delete_calls.append(url)
        return FakeJsonResponse({"state": "REVOKED"})

    def stream(self, method, url, **kwargs):
        self.stream_calls.append({"method": method, "url": url, **kwargs})
        if url not in self.stream_responses:
            raise AssertionError(f"unexpected stream {url}")
        return FakeStreamContext(self.stream_responses[url])


class FakeAsyncClientFactory:
    """Drop-in replacement for httpx.AsyncClient constructor."""

    def __init__(self, client: FakeAsyncClient):
        self.client = client

    def __call__(self, *args, **kwargs):
        return _AsyncClientContext(self.client)


class _AsyncClientContext:
    def __init__(self, client: FakeAsyncClient):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *args):
        return None


class FakeJsonResponse:
    def __init__(self, payload, status_code=200, content=b"%PDF-fake"):
        self.payload = payload
        self.status_code = status_code
        self.text = str(payload)
        self.content = content

    def json(self):
        return self.payload


# ---------------------------------------------------------------------------
# Payload building
# ---------------------------------------------------------------------------


def test_payload_google_has_no_envs():
    payload = _build_translate_payload(_cfg(service="google"))
    assert payload["service"] == "google"
    assert payload["lang_in"] == "en"
    assert payload["lang_out"] == "zh"
    assert "envs" not in payload


def test_payload_openai_includes_envs():
    payload = _build_translate_payload(
        _cfg(
            service="openai:deepseek-v4-flash",
            openai_base_url="https://agentrouter.org/v1",
            openai_api_key="sk-test",
            openai_model="deepseek-v4-flash",
        )
    )
    assert payload["service"] == "openai:deepseek-v4-flash"
    assert payload["envs"] == {
        "OPENAI_BASE_URL": "https://agentrouter.org/v1",
        "OPENAI_API_KEY": "sk-test",
        "OPENAI_MODEL": "deepseek-v4-flash",
    }


# ---------------------------------------------------------------------------
# Submit / progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_translation_returns_task_id():
    client = FakeAsyncClient(post_response=FakeJsonResponse({"id": "abc-123"}))
    task_id = await submit_translation(client, _cfg(), b"%PDF-bytes")
    assert task_id == "abc-123"
    call = client.post_calls[0]
    assert call["url"] == "http://pdf2zh:11008/v1/translate"
    assert call["files"]["file"][1] == b"%PDF-bytes"


@pytest.mark.asyncio
async def test_submit_translation_missing_id_raises():
    client = FakeAsyncClient(post_response=FakeJsonResponse({}))
    with pytest.raises(TranslationError, match="任务 ID"):
        await submit_translation(client, _cfg(), b"%PDF-bytes")


@pytest.mark.asyncio
async def test_submit_translation_http_error_raises():
    client = FakeAsyncClient(post_response=httpx.ConnectError("boom"))
    with pytest.raises(TranslationError, match="无法连接"):
        await submit_translation(client, _cfg(), b"%PDF-bytes")


@pytest.mark.asyncio
async def test_progress_mapping():
    client = FakeAsyncClient(
        get_responses={
            "http://pdf2zh:11008/v1/translate/t1": FakeJsonResponse(
                {"state": "PROGRESS", "info": {"n": 3, "total": 10}}
            )
        }
    )
    status = await query_translation_progress(client, _cfg(), "t1")
    assert status == {"state": "progress", "progress": 30}

    client2 = FakeAsyncClient(
        get_responses={
            "http://pdf2zh:11008/v1/translate/t2": FakeJsonResponse({"state": "SUCCESS"})
        }
    )
    assert await query_translation_progress(client2, _cfg(), "t2") == {
        "state": "success",
        "progress": 100,
    }

    client3 = FakeAsyncClient(
        get_responses={
            "http://pdf2zh:11008/v1/translate/t3": FakeJsonResponse({"state": "FAILURE"})
        }
    )
    assert await query_translation_progress(client3, _cfg(), "t3") == {
        "state": "error",
        "progress": 0,
    }


# ---------------------------------------------------------------------------
# Result streaming (never written to our disk)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_translation_result_relays_chunks(monkeypatch):
    client = FakeAsyncClient(
        stream_responses={
            "http://pdf2zh:11008/v1/translate/t1/dual": FakeStreamResponse(
                chunks=(b"%PDF-", b"dual")
            )
        }
    )
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClientFactory(client))

    chunks = [chunk async for chunk in stream_translation_result(_cfg(), "t1", "dual")]

    assert chunks == [b"%PDF-", b"dual"]
    assert client.stream_calls[0]["method"] == "GET"
    assert client.stream_calls[0]["url"] == "http://pdf2zh:11008/v1/translate/t1/dual"


@pytest.mark.asyncio
async def test_stream_translation_result_rejects_invalid_kind(monkeypatch):
    monkeypatch.setattr(
        httpx, "AsyncClient", FakeAsyncClientFactory(FakeAsyncClient())
    )
    with pytest.raises(TranslationError, match="无效的翻译产物类型"):
        [chunk async for chunk in stream_translation_result(_cfg(), "t1", "evil")]


@pytest.mark.asyncio
async def test_stream_translation_result_rejects_error_status(monkeypatch):
    client = FakeAsyncClient(
        stream_responses={
            "http://pdf2zh:11008/v1/translate/t1/mono": FakeStreamResponse(
                chunks=(b'{"error": "task not finished"}',), status_code=400
            )
        }
    )
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClientFactory(client))
    with pytest.raises(TranslationError, match="下载翻译结果失败"):
        [chunk async for chunk in stream_translation_result(_cfg(), "t1", "mono")]


# ---------------------------------------------------------------------------
# Remote liveness probe
# ---------------------------------------------------------------------------


def _fake_sync_get(monkeypatch, *, payload=None, exc=None):
    def fake_get(url, **kwargs):
        if exc is not None:
            raise exc
        return FakeJsonResponse(payload or {})

    monkeypatch.setattr(pdf_translation.httpx, "get", fake_get)


def test_remote_result_state_success(monkeypatch):
    _fake_sync_get(monkeypatch, payload={"state": "SUCCESS"})
    assert remote_result_state(_cfg(), "t1") == "success"


def test_remote_result_state_gone_after_redis_restart(monkeypatch):
    # Celery answers PENDING for unknown task ids, i.e. the result is gone.
    _fake_sync_get(monkeypatch, payload={"state": "PENDING"})
    assert remote_result_state(_cfg(), "t1") == "gone"


def test_remote_result_state_unreachable(monkeypatch):
    _fake_sync_get(monkeypatch, exc=httpx.ConnectError("boom"))
    assert remote_result_state(_cfg(), "t1") is None


# ---------------------------------------------------------------------------
# Cache-key / status lookup
# ---------------------------------------------------------------------------


def test_current_pdf_url_reads_normalized_pdf_field(monkeypatch):
    """`get_paper` exposes the papers.pdf column as "pdf", not "pdf_url"."""

    import database

    monkeypatch.setattr(
        database,
        "get_paper",
        lambda paper_id: {"id": paper_id, "pdf": "https://arxiv.org/pdf/2503.06661"},
    )

    assert (
        pdf_translation._current_pdf_url("arxiv:2503.06661")
        == "https://arxiv.org/pdf/2503.06661"
    )


def test_current_pdf_url_missing_paper_returns_empty(monkeypatch):
    import database

    monkeypatch.setattr(database, "get_paper", lambda paper_id: None)

    assert pdf_translation._current_pdf_url("arxiv:2503.06661") == ""


def test_translation_status_uses_cache_key_then_falls_back(monkeypatch):
    cached = {"id": "1", "status": "success", "progress": 100, "remote_task_id": None}
    monkeypatch.setattr(pdf_translation, "translation_service_config", lambda: _cfg())
    monkeypatch.setattr(pdf_translation, "_current_pdf_url", lambda paper_id: "https://a/p.pdf")
    monkeypatch.setattr(
        pdf_translation,
        "get_paper_translation",
        lambda paper_id, pdf_url, lang_out, service: cached,
    )
    monkeypatch.setattr(
        pdf_translation,
        "get_latest_paper_translation",
        lambda paper_id: pytest.fail("should not fall back when the cache key hits"),
    )
    assert pdf_translation.get_translation_status("paper-z") is cached

    latest = {"id": "2", "status": "error", "progress": 0}
    monkeypatch.setattr(
        pdf_translation,
        "get_paper_translation",
        lambda paper_id, pdf_url, lang_out, service: None,
    )
    monkeypatch.setattr(pdf_translation, "get_latest_paper_translation", lambda paper_id: latest)
    assert pdf_translation.get_translation_status("paper-z") is latest


def test_status_marks_row_expired_when_remote_result_is_gone(monkeypatch):
    row = {
        "id": "7",
        "status": "success",
        "progress": 100,
        "remote_task_id": "remote-7",
        "error": None,
    }
    monkeypatch.setattr(pdf_translation, "translation_service_config", lambda: _cfg())
    monkeypatch.setattr(pdf_translation, "_current_pdf_url", lambda paper_id: "https://a/p.pdf")
    monkeypatch.setattr(
        pdf_translation, "get_paper_translation", lambda *args, **kwargs: row
    )
    monkeypatch.setattr(pdf_translation, "remote_result_state", lambda cfg, task_id: "gone")

    updates: list[dict] = []
    monkeypatch.setattr(
        pdf_translation,
        "update_paper_translation",
        lambda translation_id, **kwargs: updates.append({"id": translation_id, **kwargs}),
    )

    status = pdf_translation.get_translation_status("paper-z")

    assert status is not None
    assert status["status"] == "expired"
    assert status["error"] == EXPIRED_ERROR_MESSAGE
    assert updates == [
        {"id": "7", "status": "expired", "error": EXPIRED_ERROR_MESSAGE}
    ]


def test_status_keeps_success_when_pdf2zh_is_unreachable(monkeypatch):
    row = {
        "id": "8",
        "status": "success",
        "progress": 100,
        "remote_task_id": "remote-8",
        "error": None,
    }
    monkeypatch.setattr(pdf_translation, "translation_service_config", lambda: _cfg())
    monkeypatch.setattr(pdf_translation, "_current_pdf_url", lambda paper_id: "https://a/p.pdf")
    monkeypatch.setattr(
        pdf_translation, "get_paper_translation", lambda *args, **kwargs: row
    )
    monkeypatch.setattr(pdf_translation, "remote_result_state", lambda cfg, task_id: None)
    monkeypatch.setattr(
        pdf_translation,
        "update_paper_translation",
        lambda *args, **kwargs: pytest.fail("must not touch the row when probing fails"),
    )

    status = pdf_translation.get_translation_status("paper-z")

    assert status is row


# ---------------------------------------------------------------------------
# Background task orchestration (integration-ish, DB mocked)
# ---------------------------------------------------------------------------


def _patch_translation_db(monkeypatch, rows: dict, updates: list) -> None:
    def fake_create(paper_id, pdf_url, lang_out, service):
        row = {
            "id": "1",
            "paper_id": paper_id,
            "pdf_url": pdf_url,
            "lang_out": lang_out,
            "service": service,
            "status": "pending",
            "progress": 0,
            "remote_task_id": None,
            "error": None,
        }
        rows["1"] = row
        return row

    def fake_update(translation_id, **kwargs):
        updates.append({"id": translation_id, **kwargs})
        if translation_id in rows:
            # Mirror the SQL semantics: a NULL value never clears the column.
            rows[translation_id].update(
                {key: value for key, value in kwargs.items() if value is not None}
            )

    monkeypatch.setattr(pdf_translation, "create_paper_translation", fake_create)
    monkeypatch.setattr(pdf_translation, "update_paper_translation", fake_update)
    monkeypatch.setattr(
        pdf_translation, "download_public_pdf_bytes", lambda url: b"%PDF-source"
    )
    monkeypatch.setattr(pdf_translation, "translation_service_config", lambda: _cfg())
    monkeypatch.setattr(pdf_translation, "_POLL_INTERVAL_SECONDS", 0.01)


@pytest.mark.asyncio
async def test_run_translation_success_flow(monkeypatch):
    rows: dict[str, dict] = {}
    updates: list[dict] = []
    _patch_translation_db(monkeypatch, rows, updates)

    client = FakeAsyncClient(
        post_response=FakeJsonResponse({"id": "remote-1"}),
        get_responses={
            "http://pdf2zh:11008/v1/translate/remote-1": FakeJsonResponse(
                {"state": "SUCCESS"}
            )
        },
    )
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClientFactory(client))

    await pdf_translation._run_translation("paper-x", "https://example.com/p.pdf")

    final = updates[-1]
    assert final["status"] == "success"
    assert final["progress"] == 100
    # Results stay inside pdf2zh: nothing is downloaded, nothing hits our disk.
    assert client.stream_calls == []
    assert rows["1"]["remote_task_id"] == "remote-1"
    assert "mono_path" not in rows["1"]


@pytest.mark.asyncio
async def test_run_translation_service_failure_marks_error(monkeypatch):
    rows: dict[str, dict] = {}
    updates: list[dict] = []
    _patch_translation_db(monkeypatch, rows, updates)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        FakeAsyncClientFactory(
            FakeAsyncClient(
                post_response=FakeJsonResponse({"id": "remote-2"}),
                get_responses={
                    "http://pdf2zh:11008/v1/translate/remote-2": FakeJsonResponse(
                        {"state": "FAILURE"}
                    )
                },
            )
        ),
    )

    await pdf_translation._run_translation("paper-y", "https://example.com/q.pdf")

    final = updates[-1]
    assert final["status"] == "error"
    assert final["error"]


@pytest.mark.asyncio
async def test_run_translation_timeout_revokes_remote_task(monkeypatch):
    rows: dict[str, dict] = {}
    updates: list[dict] = []
    _patch_translation_db(monkeypatch, rows, updates)
    monkeypatch.setattr(
        pdf_translation,
        "settings",
        SimpleNamespace(
            pdf_translation=SimpleNamespace(max_concurrent_tasks=1, task_timeout_seconds=0)
        ),
    )
    pdf_translation._semaphore = None

    client = FakeAsyncClient(post_response=FakeJsonResponse({"id": "remote-3"}))
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClientFactory(client))

    await pdf_translation._run_translation("paper-z", "https://example.com/r.pdf")
    pdf_translation._semaphore = None

    assert client.delete_calls == ["http://pdf2zh:11008/v1/translate/remote-3"]
    final = updates[-1]
    assert final["status"] == "error"
    assert "超时" in final["error"]


def test_task_semaphore_respects_config(monkeypatch):
    monkeypatch.setattr(
        pdf_translation,
        "settings",
        SimpleNamespace(pdf_translation=SimpleNamespace(max_concurrent_tasks=3)),
    )
    pdf_translation._semaphore = None
    try:
        semaphore = pdf_translation._task_semaphore()
        assert semaphore._value == 3
    finally:
        pdf_translation._semaphore = None
    # monkeypatch 自动还原 settings，无需手动断言
