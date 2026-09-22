"""HTTP/ASGI regression coverage for translated PDF downloads.

Only the database and pdf2zh HTTP transport are substituted. Requests run through
FastAPI and the real httpx streaming implementation; no live translation service
is required or implied by these tests.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, unquote
import sys

import httpx
from pypdf import PdfReader, PdfWriter
import pytest
from starlette.requests import ClientDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app as app_module
import pdf_translation


_HTTP_CLIENT = httpx.AsyncClient
_CHUNK_SIZE = 64 * 1024


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.bytes_read = 0
        self.read_started = asyncio.Event()
        self.block_after_first = False

    async def __aiter__(self):
        for index, chunk in enumerate(self.chunks):
            if index and self.block_after_first:
                self.read_started.set()
                await asyncio.Event().wait()
            if isinstance(chunk, Exception):
                raise chunk
            self.bytes_read += len(chunk)
            yield chunk

    async def aclose(self):
        # A checkpoint verifies cleanup still works inside a cancelled task group.
        await asyncio.sleep(0)
        self.closed = True


@pytest.fixture
def pdf_bytes():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def download_backend(monkeypatch, pdf_bytes):
    state = SimpleNamespace(
        row={"id": "17", "status": "success", "remote_task_id": "remote-17"},
        probes=[{"state": "SUCCESS"}],
        probe_status=200,
        probe_content=None,
        probe_error=None,
        probe_calls=[],
        status_calls=[],
        expired=[],
        status=200,
        content_type="application/pdf",
        stream=TrackedStream([pdf_bytes[:2], pdf_bytes[2:]]),
        connect_error=None,
        requests=[],
        clients=[],
    )
    cfg = pdf_translation.TranslationServiceConfig(
        base_url="http://pdf2zh.test", service="google", lang_in="en", lang_out="zh",
        openai_base_url=None, openai_api_key=None, openai_model=None,
    )

    def get_status(paper_id, *, verify_remote=True):
        state.status_calls.append((paper_id, verify_remote))
        return state.row

    def probe(url, **kwargs):
        state.probe_calls.append(url)
        if state.probe_error:
            raise state.probe_error
        if state.probe_content is not None:
            return httpx.Response(state.probe_status, content=state.probe_content)
        payload = state.probes.pop(0) if len(state.probes) > 1 else state.probes[0]
        return httpx.Response(state.probe_status, json=payload)

    def upstream(request):
        state.requests.append(request)
        if state.connect_error:
            raise state.connect_error
        return httpx.Response(
            state.status,
            headers={"content-type": state.content_type},
            stream=state.stream,
        )

    def client_factory(*args, **kwargs):
        client = _HTTP_CLIENT(transport=httpx.MockTransport(upstream))
        state.clients.append(client)
        return client

    monkeypatch.setattr(app_module, "translation_enabled", lambda: True)
    monkeypatch.setattr(app_module, "translation_service_config", lambda: cfg)
    monkeypatch.setattr(app_module, "get_translation_status", get_status)
    monkeypatch.setattr(app_module, "mark_translation_expired", state.expired.append)
    monkeypatch.setattr(pdf_translation.httpx, "get", probe)
    monkeypatch.setattr(pdf_translation.httpx, "AsyncClient", client_factory)
    return state


async def request_download(paper_id="paper-1", kind="mono"):
    async with _HTTP_CLIENT(
        transport=httpx.ASGITransport(app=app_module.app), base_url="http://app.test"
    ) as client:
        return await client.get(f"/paper/{quote(paper_id, safe='')}/translation/{kind}")


def assert_closed(state):
    assert state.clients
    assert all(client.is_closed for client in state.clients)
    assert state.stream.closed


def assert_json_error(response, status):
    assert response.status_code == status
    assert response.headers["content-type"] == "application/json"
    assert response.json()["detail"]
    assert "content-disposition" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,suffix", [("mono", "中文"), ("dual", "中英双语")])
@pytest.mark.parametrize("paper_id", ["paper-1", "论文-é-😀", 'paper\";name'])
async def test_download_http_pdf_and_safe_attachment_headers(
    download_backend, pdf_bytes, kind, suffix, paper_id
):
    state = download_backend
    response = await request_download(paper_id, kind)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert response.content == pdf_bytes
    assert len(PdfReader(BytesIO(response.content)).pages) == 1
    disposition = response.headers["content-disposition"]
    assert disposition.isascii()
    assert disposition.startswith('attachment; filename="')
    fallback, encoded = disposition.split("; filename*=UTF-8''")
    assert fallback.count('"') == 2
    expected_id = app_module._safe_paper_id(paper_id)
    assert unquote(encoded) == f"{expected_id}-{suffix}.pdf"
    assert state.status_calls == [(paper_id, False)]
    assert len(state.probe_calls) == 1
    assert state.requests[0].url.path == f"/v1/translate/remote-17/{kind}"
    assert state.expired == []
    assert_closed(state)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "upstream_status,expected,expired",
    [(400, 502, False), (404, 410, True), (410, 410, True),
     (503, 503, False), (500, 503, False), (429, 503, False),
     (401, 502, False), (302, 502, False)],
)
async def test_success_state_but_result_http_error_is_not_a_pdf(
    download_backend, upstream_status, expected, expired
):
    state = download_backend
    state.status = upstream_status
    response = await request_download()
    assert_json_error(response, expected)
    assert state.expired == (["17"] if expired else [])
    assert state.stream.bytes_read == 0
    assert_closed(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("second_probe,expected", [({"state": "PENDING"}, 410), ({}, 503)])
async def test_result_400_rechecks_expiry_race(download_backend, second_probe, expected):
    state = download_backend
    state.status = 400
    state.probes = [{"state": "SUCCESS"}, second_probe]
    response = await request_download()
    assert_json_error(response, expected)
    assert state.expired == (["17"] if expected == 410 else [])
    assert len(state.probe_calls) == 2
    assert_closed(state)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type,body",
    [("text/html", b"<html>bad gateway</html>"),
     ("application/pdf", b"<html>bad gateway</html>"),
     ("application/pdf", b""), ("application/pdf", b"%PD"),
     ("application/octet-stream", b'{"error":"failed"}')],
)
async def test_invalid_or_empty_result_is_json_502(download_backend, content_type, body):
    state = download_backend
    state.content_type = content_type
    state.stream = TrackedStream([body])
    response = await request_download()
    assert_json_error(response, 502)
    assert state.expired == []
    assert_closed(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["application/octet-stream", "", "application/pdf; charset=binary"])
async def test_pdf_signature_allows_common_upstream_content_types(download_backend, content_type):
    download_backend.content_type = content_type
    response = await request_download()
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert_closed(download_backend)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["connect", "first_read"])
async def test_upstream_network_failure_before_headers_returns_503(download_backend, phase):
    state = download_backend
    if phase == "connect":
        state.connect_error = httpx.ConnectError("unavailable")
    else:
        state.stream = TrackedStream([httpx.ReadTimeout("timeout")])
    response = await request_download()
    assert_json_error(response, 503)
    assert state.expired == []
    assert all(client.is_closed for client in state.clients)
    if phase == "first_read":
        assert state.stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload", [{}, [], None, {"state": "UNKNOWN"}, {"state": "STARTED"},
                {"state": "PROGRESS", "info": "bad"}, {"state": []}],
)
async def test_ambiguous_probe_never_expires_success(download_backend, payload):
    state = download_backend
    state.probes = [payload]
    response = await request_download()
    assert_json_error(response, 503)
    assert state.expired == []
    assert state.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["html", "empty", "connect", "503", "400"])
async def test_unavailable_probe_returns_503_without_expiry(download_backend, failure):
    state = download_backend
    if failure in {"html", "empty"}:
        state.probe_content = b"<html>proxy error</html>" if failure == "html" else b""
    elif failure == "connect":
        state.probe_error = httpx.ConnectError("unavailable")
    else:
        state.probe_status = int(failure)
    response = await request_download()
    assert_json_error(response, 503)
    assert state.expired == []
    assert state.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status,payload", [(404, {}), (410, {}), (200, {"state": "PENDING"}),
                                          (200, {"state": "FAILURE"}), (200, {"state": "REVOKED"})])
async def test_gone_probe_returns_410_and_expires(download_backend, status, payload):
    state = download_backend
    state.probe_status = status
    state.probes = [payload]
    response = await request_download()
    assert_json_error(response, 410)
    assert state.expired == ["17"]
    assert state.requests == []


@pytest.mark.asyncio
async def test_already_expired_row_returns_410_without_probing(download_backend):
    state = download_backend
    state.row["status"] = "expired"
    response = await request_download()
    assert_json_error(response, 410)
    assert state.probe_calls == []
    assert state.requests == []


async def run_asgi(send, receive, *, spec_version="2.4"):
    # Execute the real FastAPI route with controllable ASGI send/disconnects.
    path = "/paper/paper-1/translation/mono"
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [], "client": ("127.0.0.1", 12345), "server": ("app.test", 80),
    }
    await app_module.app(scope, receive, send)


async def no_disconnect():
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_streams_incrementally_instead_of_buffering_full_pdf(download_backend):
    state = download_backend
    state.stream = TrackedStream([b"%PDF-" + b"x" * (_CHUNK_SIZE - 5)] + [b"x" * _CHUNK_SIZE] * 4)
    messages = []

    async def send(message):
        if not messages:
            assert message["type"] == "http.response.start"
            assert message["status"] == 200
            assert state.stream.bytes_read == _CHUNK_SIZE
            assert not state.stream.closed
        messages.append(message)

    await run_asgi(send, no_disconnect)
    bodies = [message["body"] for message in messages if message["type"] == "http.response.body"]
    assert sum(map(len, bodies)) == 5 * _CHUNK_SIZE
    assert max(map(len, bodies)) <= _CHUNK_SIZE
    assert messages[-1]["more_body"] is False
    assert_closed(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", ["http.response.start", "http.response.body"])
async def test_downstream_send_failure_closes_preopened_stream(download_backend, failure_at):
    async def send(message):
        if message["type"] == failure_at:
            raise OSError("browser disconnected")

    with pytest.raises(ClientDisconnect):
        await run_asgi(send, no_disconnect)
    assert_closed(download_backend)


@pytest.mark.asyncio
async def test_disconnect_during_upstream_read_closes_resources(download_backend):
    state = download_backend
    state.stream = TrackedStream([b"%PDF-" + b"x" * (_CHUNK_SIZE - 5), b"tail"])
    state.stream.block_after_first = True
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        await state.stream.read_started.wait()
        return {"type": "http.disconnect"}

    await asyncio.wait_for(run_asgi(send, receive, spec_version="2.0"), timeout=5)
    assert messages[0]["status"] == 200
    assert_closed(state)


@pytest.mark.asyncio
async def test_midstream_failure_aborts_instead_of_claiming_complete_pdf(download_backend):
    state = download_backend
    state.stream = TrackedStream([
        b"%PDF-" + b"x" * (_CHUNK_SIZE - 5), httpx.ReadError("connection lost"),
    ])
    messages = []

    async def send(message):
        messages.append(message)

    # Once 200 has been sent, HTTP cannot be changed to 503. The correct behavior
    # is to abort and close, never append JSON or send a successful end-of-body.
    with pytest.raises(pdf_translation.TranslationDownloadError):
        await run_asgi(send, no_disconnect)
    assert messages[0]["status"] == 200
    assert len(messages) == 2
    assert messages[1]["more_body"] is True
    assert_closed(state)
