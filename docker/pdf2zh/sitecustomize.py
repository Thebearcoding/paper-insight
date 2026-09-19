"""Container-wide runtime patches for pdf2zh: openai User-Agent and NumPy 2.

Two unrelated upstream quirks are fixed here, both of which have to be applied
inside every Python process of this image (the forked Flask server and the
Celery worker, plus the worker's task children):

1. Some OpenAI-compatible gateways (e.g. agentrouter.org) answer requests whose
   User-Agent looks like the plain openai SDK / httpx default with
   401 "unauthorized client detected". The openai SDK forces its own User-Agent
   while building the request, so `default_headers` cannot override it — we
   instead register an httpx request event hook that rewrites the User-Agent
   (and `x-app`) on every outgoing request. pdf2zh's translators build
   `openai.OpenAI(...)` (sync) while babeldoc builds the sync client too, so
   both classes are patched.

   The hook has to stay the *only* thing we customise on the client: passing an
   explicit `transport=` here would disable httpx's environment-proxy support
   (`Client.__init__` only honours HTTP(S)_PROXY while `transport is None`), and
   this container reaches the gateway solely through `OUTBOUND_PROXY_URL` —
   without it every translation dies with `ConnectError: Network is unreachable`.
   Event hooks run after the SDK has assembled the headers and before the
   transport sends them, so the spoof still wins.

2. pdf2zh 1.9.4 calls the binary mode of `np.fromstring` (removed in NumPy 2) in
   `translate_patch`, and babeldoc's `docvision` does the same, so every page
   would fail with `ValueError: The binary mode of fromstring is removed` — while
   NumPy 1.x cannot be installed at all, because babeldoc 0.1.x requires
   numpy>=2.0.2. `np.fromstring` is therefore reimplemented on top of
   `np.frombuffer`: the upstream calls are `np.fromstring(pix.samples, np.uint8)`
   on a `bytes` buffer, which is exactly `np.frombuffer`, except that the numpy 1.x
   original copies the data and `np.frombuffer` returns a read-only view — hence
   the `.copy()`.

Loaded automatically via PYTHONPATH (sitecustomize.py is imported by `site` at
interpreter startup).
"""

import httpx
import numpy as np
import openai

_SPOOFED_USER_AGENT = "claude-cli/2.0.14 (external, cli)"
_EXTRA_HEADERS = {"x-app": "cli"}
# Fail fast on dead proxies, but allow slow reasoning models to answer.
_DEFAULT_TIMEOUT = httpx.Timeout(600.0, connect=15.0)


def _rewrite_headers(request: httpx.Request) -> None:
    request.headers["User-Agent"] = _SPOOFED_USER_AGENT
    for name, value in _EXTRA_HEADERS.items():
        request.headers[name] = value


def _timeout_from(kwargs: dict) -> httpx.Timeout | float:
    configured = kwargs.get("timeout")
    if isinstance(configured, (int, float)):
        return float(configured)
    return _DEFAULT_TIMEOUT


def _patched_client_kwargs(kwargs: dict) -> dict:
    """kwargs for the httpx client we hand to the openai SDK.

    No `transport=`: that alone would turn off HTTP(S)_PROXY support for this
    container, which only reaches the gateway through the outbound proxy.
    """

    return {
        "event_hooks": {"request": [_rewrite_headers]},
        "timeout": _timeout_from(kwargs),
    }


_original_sync_init = openai.OpenAI.__init__
_original_async_init = openai.AsyncOpenAI.__init__


def _patched_sync_init(self, *args, **kwargs):
    if kwargs.get("http_client") is None:
        kwargs["http_client"] = httpx.Client(**_patched_client_kwargs(kwargs))
    _original_sync_init(self, *args, **kwargs)


def _patched_async_init(self, *args, **kwargs):
    if kwargs.get("http_client") is None:
        kwargs["http_client"] = httpx.AsyncClient(**_patched_client_kwargs(kwargs))
    _original_async_init(self, *args, **kwargs)


openai.OpenAI.__init__ = _patched_sync_init
openai.AsyncOpenAI.__init__ = _patched_async_init


_original_fromstring = np.fromstring


def _fromstring(string, dtype=float, count=-1, *, sep="", like=None):
    if sep != "":
        return _original_fromstring(string, dtype=dtype, count=count, sep=sep, like=like)
    # 上游只按二进制模式调用（bytes + dtype），等价于 frombuffer，但要像 numpy 1.x
    # 的 fromstring 一样返回可写副本，否则后续原地改写会报 read-only。
    return np.frombuffer(string, dtype=dtype, count=count).copy()


np.fromstring = _fromstring
