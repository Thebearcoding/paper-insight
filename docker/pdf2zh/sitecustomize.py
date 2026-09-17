"""Patch the openai SDK User-Agent for gateways that do client detection.

Some OpenAI-compatible gateways (e.g. agentrouter.org) answer requests whose
User-Agent looks like the plain openai SDK / httpx default with
401 "unauthorized client detected". The openai SDK forces its own User-Agent at
the transport layer, so `default_headers` cannot override it — we wrap
`openai.OpenAI` / `openai.AsyncOpenAI` with a custom httpx transport that
rewrites the User-Agent (and `x-app`) header on every request.

pdf2zh's translators build `openai.OpenAI(...)` (sync) while babeldoc builds the
sync client too, so both classes are patched.

Loaded automatically via PYTHONPATH (sitecustomize.py is imported by `site` at
interpreter startup).
"""

import httpx
import openai

_SPOOFED_USER_AGENT = "claude-cli/2.0.14 (external, cli)"
_EXTRA_HEADERS = {"x-app": "cli"}
# Fail fast on dead proxies, but allow slow reasoning models to answer.
_DEFAULT_TIMEOUT = httpx.Timeout(600.0, connect=15.0)


def _rewrite_headers(request: httpx.Request) -> None:
    request.headers["User-Agent"] = _SPOOFED_USER_AGENT
    for name, value in _EXTRA_HEADERS.items():
        request.headers[name] = value


class _UAOverrideTransport(httpx.HTTPTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        _rewrite_headers(request)
        return super().handle_request(request)


class _UAOverrideAsyncTransport(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        _rewrite_headers(request)
        return await super().handle_async_request(request)


def _timeout_from(kwargs: dict) -> httpx.Timeout | float:
    configured = kwargs.get("timeout")
    if isinstance(configured, (int, float)):
        return float(configured)
    return _DEFAULT_TIMEOUT


_original_sync_init = openai.OpenAI.__init__
_original_async_init = openai.AsyncOpenAI.__init__


def _patched_sync_init(self, *args, **kwargs):
    if kwargs.get("http_client") is None:
        kwargs["http_client"] = httpx.Client(
            transport=_UAOverrideTransport(),
            timeout=_timeout_from(kwargs),
        )
    _original_sync_init(self, *args, **kwargs)


def _patched_async_init(self, *args, **kwargs):
    if kwargs.get("http_client") is None:
        kwargs["http_client"] = httpx.AsyncClient(
            transport=_UAOverrideAsyncTransport(),
            timeout=_timeout_from(kwargs),
        )
    _original_async_init(self, *args, **kwargs)


openai.OpenAI.__init__ = _patched_sync_init
openai.AsyncOpenAI.__init__ = _patched_async_init
