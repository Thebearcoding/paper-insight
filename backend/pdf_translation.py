"""Paper PDF translation via the pdf2zh HTTP backend.

Flow: POST /paper/{id}/translation creates a pending row and spawns an
asyncio task that downloads the source PDF, submits it to the pdf2zh
service and polls until the job finishes. The frontend polls
GET /paper/{id}/translation for status.

Nothing is persisted on this server: pdf2zh keeps the mono/dual PDFs in its
own result backend (Redis) and the download route proxies the bytes straight
to the browser, so translated papers never occupy our disk. That also means
results vanish when the pdf2zh service restarts — such rows are reported as
`expired` and can simply be translated again.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

import anyio
import httpx

from config import settings
from database import (
    DatabaseError,
    create_paper_translation,
    get_latest_paper_translation,
    get_paper_translation,
    update_paper_translation,
)
from paper_resources import ReaderError, download_public_pdf_bytes

logger = logging.getLogger(__name__)

# Poll cadence for the pdf2zh task status endpoint.
_POLL_INTERVAL_SECONDS = 2.0
# HTTP timeouts for talking to the pdf2zh service.
_SUBMIT_TIMEOUT_SECONDS = 60.0
_PROBE_TIMEOUT_SECONDS = 10.0
_DOWNLOAD_TIMEOUT_SECONDS = 300.0

EXPIRED_ERROR_MESSAGE = "翻译结果已过期（pdf2zh 服务重启会清空缓存），请重新翻译"

TRANSLATION_KINDS = frozenset({"mono", "dual"})


class TranslationError(Exception):
    """Raised when the pdf2zh service fails or misbehaves."""


class TranslationDownloadError(TranslationError):
    """An upstream download failure with a status for pre-response error mapping."""

    def __init__(
        self, message: str, *, status_code: int = 502, upstream_status_code: int | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.upstream_status_code = upstream_status_code


@dataclass(frozen=True)
class TranslationServiceConfig:
    base_url: str
    service: str
    lang_in: str
    lang_out: str
    openai_base_url: str | None
    openai_api_key: str | None
    openai_model: str | None


def translation_service_config() -> TranslationServiceConfig:
    cfg = settings.pdf_translation
    return TranslationServiceConfig(
        base_url=cfg.service_base_url,
        service=cfg.service,
        lang_in=cfg.lang_in,
        lang_out=cfg.lang_out,
        openai_base_url=cfg.openai_base_url,
        openai_api_key=cfg.openai_api_key,
        openai_model=cfg.openai_model,
    )


def translation_enabled() -> bool:
    return settings.pdf_translation.enabled


def _build_translate_payload(cfg: TranslationServiceConfig) -> dict[str, Any]:
    """Build the `data` JSON payload for pdf2zh /v1/translate."""

    payload: dict[str, Any] = {
        "lang_in": cfg.lang_in,
        "lang_out": cfg.lang_out,
        "service": cfg.service,
        "thread": 4,
    }
    if cfg.service.startswith("openai"):
        envs: dict[str, str] = {}
        if cfg.openai_base_url:
            envs["OPENAI_BASE_URL"] = cfg.openai_base_url
        if cfg.openai_api_key:
            envs["OPENAI_API_KEY"] = cfg.openai_api_key
        if cfg.openai_model:
            envs["OPENAI_MODEL"] = cfg.openai_model
        if envs:
            payload["envs"] = envs
    return payload


async def submit_translation(
    client: httpx.AsyncClient,
    cfg: TranslationServiceConfig,
    pdf_bytes: bytes,
) -> str:
    """Submit a PDF to pdf2zh and return the remote task id."""

    files = {"file": ("paper.pdf", pdf_bytes, "application/pdf")}
    data = {"data": _json_dumps(_build_translate_payload(cfg))}
    try:
        response = await client.post(
            f"{cfg.base_url}/v1/translate",
            files=files,
            data=data,
            timeout=_SUBMIT_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise TranslationError(f"无法连接 pdf2zh 服务: {exc}") from exc
    if response.status_code != 200:
        raise TranslationError(
            f"pdf2zh 提交失败 (HTTP {response.status_code}): {response.text[:200]}"
        )
    task_id = (response.json() or {}).get("id")
    if not task_id:
        raise TranslationError("pdf2zh 未返回任务 ID")
    return str(task_id)


async def query_translation_progress(
    client: httpx.AsyncClient,
    cfg: TranslationServiceConfig,
    task_id: str,
) -> dict[str, Any]:
    """Return {'state': ..., 'progress': 0-100} for a remote task."""

    try:
        response = await client.get(
            f"{cfg.base_url}/v1/translate/{task_id}",
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise TranslationError(f"查询 pdf2zh 进度失败: {exc}") from exc
    if response.status_code != 200:
        raise TranslationError(
            f"查询 pdf2zh 进度失败 (HTTP {response.status_code}): {response.text[:200]}"
        )
    return _map_remote_task_state(response.json() or {})


def _map_remote_task_state(body: dict[str, Any]) -> dict[str, Any]:
    state = str(body.get("state") or "")
    if state == "PROGRESS":
        info = body.get("info") or {}
        total = int(info.get("total") or 0)
        current = int(info.get("n") or 0)
        # Upstream counts processed pages before all translated PDFs are ready.
        # Reserve 100% for SUCCESS so an unfinished/failed job cannot look done.
        progress = min(99, max(0, int(current * 100 / total))) if total > 0 else 0
        return {"state": "progress", "progress": progress}
    if state == "SUCCESS":
        return {"state": "success", "progress": 100}
    if state in {"FAILURE", "REVOKED"}:
        return {"state": "error", "progress": 0}
    # PENDING / STARTED and anything unknown
    return {"state": state.lower() or "pending", "progress": 0}


async def stream_translation_result(
    cfg: TranslationServiceConfig,
    task_id: str,
    kind: str,
) -> AsyncIterator[bytes]:
    """Relay a PDF with bounded buffering; prime before sending HTTP 200.

    The first yield validates both the upstream response and the PDF signature.
    The caller owns this generator and must close it on downstream disconnect.
    No translated PDF is stored on disk or buffered in full.
    """

    if kind not in TRANSLATION_KINDS:
        raise TranslationError(f"无效的翻译产物类型: {kind}")
    resources = AsyncExitStack()
    try:
        client = await resources.enter_async_context(httpx.AsyncClient())
        response = await resources.enter_async_context(client.stream(
            "GET",
            f"{cfg.base_url}/v1/translate/{task_id}/{kind}",
            timeout=_DOWNLOAD_TIMEOUT_SECONDS,
        ))
        if response.status_code != 200:
            status = response.status_code
            public_status = 410 if status in {404, 410} else (
                503 if status >= 500 or status == 429 else 502
            )
            raise TranslationDownloadError(
                f"下载翻译结果失败 (HTTP {status})",
                status_code=public_status,
                upstream_status_code=status,
            )
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"", "application/pdf", "application/octet-stream"}:
            raise TranslationDownloadError("pdf2zh 返回的翻译结果不是 PDF")

        # Fixed-size chunks bound memory even if the upstream sends large frames.
        # Accommodate signatures split across transport chunks without losing bytes.
        prefix = b""
        validated = False
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            if not validated:
                prefix += chunk
                if len(prefix) < 5:
                    continue
                if not prefix.startswith(b"%PDF-"):
                    raise TranslationDownloadError("pdf2zh 返回的翻译结果不是 PDF")
                validated = True
                yield prefix
                prefix = b""
            else:
                yield chunk
        if not validated:
            raise TranslationDownloadError("pdf2zh 返回的翻译结果为空或不是 PDF")
    except httpx.HTTPError as exc:
        raise TranslationDownloadError(
            "pdf2zh 服务暂时不可用，请稍后重试", status_code=503
        ) from exc
    finally:
        # Starlette cancels streaming tasks when the browser disconnects. Cleanup
        # must survive that cancellation, including disconnects during a read.
        with anyio.CancelScope(shield=True):
            await resources.aclose()


async def revoke_remote_task(
    client: httpx.AsyncClient,
    cfg: TranslationServiceConfig,
    task_id: str,
) -> None:
    """Best-effort cancel so pdf2zh stops burning CPU on an abandoned job."""

    try:
        await client.delete(
            f"{cfg.base_url}/v1/translate/{task_id}",
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:  # pragma: no cover - cleanup must never raise
        logger.warning("Revoking pdf2zh task %s failed: %s", task_id, exc)


def remote_result_state(
    cfg: TranslationServiceConfig,
    task_id: str,
) -> str | None:
    """Sync probe of a finished remote task.

    Returns "success" when pdf2zh still holds the result, "gone" when the
    service answered but no longer has it (Redis was restarted), and None when
    the service could not be reached — callers must not expire rows in that
    case.
    """

    try:
        response = httpx.get(
            f"{cfg.base_url}/v1/translate/{task_id}",
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("Probing pdf2zh task %s failed: %s", task_id, exc)
        return None
    if response.status_code in {404, 410}:
        return "gone"
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    state = body.get("state")
    if state == "SUCCESS":
        return "success"
    # This probe is only used for previously successful rows. Celery represents
    # forgotten tasks as PENDING. Unknown/malformed or still-running responses
    # are not evidence of expiry and must never destroy a cached success row.
    if state in ("PENDING", "FAILURE", "REVOKED"):
        return "gone"
    return None


def _json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Background orchestration
# ---------------------------------------------------------------------------

_running_tasks: dict[str, asyncio.Task[None]] = {}
_semaphore: asyncio.Semaphore | None = None


def _task_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(settings.pdf_translation.max_concurrent_tasks, 1))
    return _semaphore


def translation_task_running(paper_id: str) -> bool:
    task = _running_tasks.get(paper_id)
    return task is not None and not task.done()


def start_translation_task(paper_id: str, pdf_url: str) -> None:
    """Spawn (or reuse) the background translation task for a paper."""

    if translation_task_running(paper_id):
        return
    task = asyncio.create_task(_run_translation(paper_id, pdf_url))
    _running_tasks[paper_id] = task
    task.add_done_callback(lambda _t: _running_tasks.pop(paper_id, None))


async def _run_translation(paper_id: str, pdf_url: str) -> None:
    cfg = translation_service_config()
    translation = await asyncio.to_thread(
        create_paper_translation,
        paper_id,
        pdf_url,
        cfg.lang_out,
        cfg.service,
    )
    translation_id = str(translation["id"])

    async with _task_semaphore():
        started_at = time.monotonic()
        remote_task_id: str | None = None
        try:
            await update_translation_status(translation_id, "progress", 0)
            pdf_bytes = await asyncio.to_thread(download_public_pdf_bytes, pdf_url)

            async with httpx.AsyncClient() as client:
                remote_task_id = await submit_translation(client, cfg, pdf_bytes)
                # The remote worker owns the source now; do not retain a large
                # source PDF throughout the potentially 30-minute polling loop.
                del pdf_bytes
                await asyncio.to_thread(
                    update_paper_translation,
                    translation_id,
                    remote_task_id=remote_task_id,
                )

                while True:
                    if time.monotonic() - started_at > settings.pdf_translation.task_timeout_seconds:
                        await revoke_remote_task(client, cfg, remote_task_id)
                        raise TranslationError("翻译超时，请稍后重试")
                    await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                    status = await query_translation_progress(client, cfg, remote_task_id)
                    if status["state"] == "progress":
                        await update_translation_status(
                            translation_id, "progress", status["progress"]
                        )
                        continue
                    if status["state"] == "success":
                        break
                    if status["state"] == "error":
                        raise TranslationError("pdf2zh 翻译任务失败")

            # The PDFs stay in pdf2zh's result backend; the download route
            # relays them to the browser on demand.
            await asyncio.to_thread(
                update_paper_translation,
                translation_id,
                status="success",
                progress=100,
            )
            logger.info("Paper %s translated successfully", paper_id)
        except (TranslationError, ReaderError) as exc:
            logger.warning("Paper %s translation failed: %s", paper_id, exc)
            await asyncio.to_thread(
                update_paper_translation,
                translation_id,
                status="error",
                error=str(exc)[:500],
            )
        except Exception as exc:  # noqa: BLE001 - background task must never crash the app
            logger.exception("Paper %s translation crashed", paper_id)
            await asyncio.to_thread(
                update_paper_translation,
                translation_id,
                status="error",
                error=f"内部错误: {exc}"[:500],
            )


async def update_translation_status(
    translation_id: str, status: str, progress: int
) -> None:
    try:
        await asyncio.to_thread(
            update_paper_translation,
            translation_id,
            status=status,
            progress=progress,
        )
    except DatabaseError as exc:
        logger.warning("Translation status update failed: %s", exc)


def get_translation_status(
    paper_id: str,
    *,
    verify_remote: bool = True,
) -> dict[str, Any] | None:
    """Return the latest translation row for a paper (any pdf_url/service).

    Sync helper — call via asyncio.to_thread from async routes. When the row is
    a finished translation, its remote result is probed so that rows whose PDFs
    vanished from pdf2zh are reported as `expired` instead of dangling.
    """

    cfg = translation_service_config()

    row = get_paper_translation(paper_id, _current_pdf_url(paper_id), cfg.lang_out, cfg.service)
    if not row:
        # Fall back to the most recent row for this paper regardless of cache key.
        row = get_latest_paper_translation(paper_id)
    if row and verify_remote:
        expired = _expire_row_if_remote_result_gone(row, cfg)
        if expired is not None:
            row = expired
    return row


def _expire_row_if_remote_result_gone(
    row: dict[str, Any], cfg: TranslationServiceConfig
) -> dict[str, Any] | None:
    """Mark a stale `success` row as `expired`. Returns the updated row or None."""

    if row.get("status") != "success":
        return None
    task_id = str(row.get("remote_task_id") or "")
    if not task_id or remote_result_state(cfg, task_id) != "gone":
        return None

    translation_id = str(row["id"])
    try:
        update_paper_translation(
            translation_id,
            status="expired",
            error=EXPIRED_ERROR_MESSAGE,
        )
    except DatabaseError as exc:
        logger.warning("Marking translation %s expired failed: %s", translation_id, exc)
        return None
    logger.info("Translation %s expired: pdf2zh no longer holds task %s", translation_id, task_id)
    return {**row, "status": "expired", "error": EXPIRED_ERROR_MESSAGE}


def mark_translation_expired(translation_id: str) -> None:
    """Mark a row expired after pdf2zh reported its artifacts are unavailable."""

    try:
        update_paper_translation(
            translation_id,
            status="expired",
            error=EXPIRED_ERROR_MESSAGE,
        )
    except DatabaseError as exc:
        logger.warning("Marking translation %s expired failed: %s", translation_id, exc)


def _current_pdf_url(paper_id: str) -> str:
    """Best-effort current PDF URL for cache-key matching."""

    from database import get_paper  # noqa: PLC0415 - local import avoids cycle at module load

    paper = get_paper(paper_id)
    if not paper:
        return ""
    # `papers.pdf` is the canonical column; `get_paper` normalizes it into "pdf".
    return str(paper.get("pdf") or "")
