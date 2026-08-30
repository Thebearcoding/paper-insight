from __future__ import annotations

import hashlib
import html
import logging
import multiprocessing
import queue
import re
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import pymupdf
import requests

from config import REPO_ROOT, settings
from paper_resources import (
    MAX_REDIRECTS,
    RESOURCE_USER_AGENT,
    _is_public_url,
    _raw_data,
    direct_document_candidates,
    download_public_pdf_bytes,
    extract_arxiv_id,
)
from utils import ReaderError


logger = logging.getLogger(__name__)
MAX_FIGURE_IMAGE_BYTES = 12 * 1024 * 1024
PDF_FIGURE_TIMEOUT_SECONDS = 60
FRAMEWORK_FIGURE_KIND = "framework"
RESULTS_TABLE_KIND = "results_table"
CAPTION_LABEL_PATTERN = re.compile(r"(?i)\b(?:figure|fig\.)\s*([A-Z]?\d+[A-Za-z]?)")
TABLE_CAPTION_LABEL_PATTERN = re.compile(
    r"(?i)\b(?:table|tab\.)\s*([A-Z]?\d+[A-Za-z]?|[IVXLCDM]+)\b"
)
POSITIVE_CAPTION_TERMS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"(?i)\b(?:frameworks?|architectures?)\b|框架|架构"), 16),
    (re.compile(r"(?i)\b(?:pipelines?|workflows?|overviews?|schematics?)\b|流程|概览|示意"), 12),
    (re.compile(r"(?i)\b(?:two-stage|multi-stage|end-to-end)\b|两阶段|多阶段|端到端"), 8),
    (re.compile(r"(?i)\b(?:proposed|our)\s+(?:method|model|approach|system|network)\b"), 7),
    (re.compile(r"(?i)\b(?:method|model|approach|system|network)\b|方法|模型|系统|网络"), 3),
)
NEGATIVE_CAPTION_TERMS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"(?i)\b(?:t-sne|ablation|qualitative|quantitative)\b|消融"), 14),
    (re.compile(r"(?i)\b(?:result|comparison|visualization|performance|curve)\b|结果|对比|可视化|性能"), 7),
)
RESULTS_TABLE_POSITIVE_TERMS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"(?i)\b(?:state[ -]of[ -]the[ -]art|sota)\b|最先进"), 24),
    (re.compile(r"(?i)\b(?:comparison|compared|versus|vs\.)\b|对比|比较"), 14),
    (re.compile(r"(?i)\b(?:quantitative|performance|results?|benchmarks?)\b|定量|性能|结果|基准"), 8),
    (
        re.compile(
            r"(?i)\b(?:auroc|auc|aupro|pro|average precision|ap|f1(?:-max)?|accuracy|"
            r"precision|recall|dice|iou|map|top-?1)\b"
        ),
        7,
    ),
    (re.compile(r"(?i)\b(?:our|proposed)\s+(?:method|model|approach)\b|本文方法|所提方法"), 4),
)
RESULTS_TABLE_NEGATIVE_TERMS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"(?i)\b(?:ablation|component|variant|sensitivity)\b|消融|组件|敏感性"), 28),
    (re.compile(r"(?i)\b(?:hyper-?parameter|parameter settings?|number of|layers?)\b|超参数|参数设置"), 18),
    (re.compile(r"(?i)\b(?:runtime|latency|complexity|flops|throughput)\b|运行时间|复杂度|吞吐"), 15),
    (re.compile(r"(?i)\b(?:dataset statistics|data statistics)\b|数据集统计"), 18),
)


@dataclass(frozen=True)
class FrameworkFigureAsset:
    label: str
    caption: str
    source: str
    source_url: str
    image_bytes: bytes
    extension: str = "png"
    media_type: str = "image/png"
    page_number: int | None = None
    width: int | None = None
    height: int | None = None


@dataclass
class _HtmlFigure:
    image_url: str = ""
    caption: str = ""


class _ArxivFigureParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.figures: list[_HtmlFigure] = []
        self.current: _HtmlFigure | None = None
        self.figure_depth = 0
        self.caption_depth = 0
        self.caption_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = str(attributes.get("class") or "").split()
        if self.current is None and tag == "figure" and "ltx_figure" in classes:
            self.current = _HtmlFigure()
            self.figure_depth = 1
            self.caption_depth = 0
            self.caption_parts = []
            return
        if self.current is None:
            return
        if tag == "figure":
            self.figure_depth += 1
        if tag == "img" and not self.current.image_url and attributes.get("src"):
            self.current.image_url = urljoin(self.base_url, str(attributes["src"]))
        if tag == "figcaption":
            self.caption_depth = 1
        elif self.caption_depth:
            self.caption_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if self.caption_depth:
            self.caption_depth -= 1
        if tag == "figure":
            self.figure_depth -= 1
            if self.figure_depth == 0:
                self.current.caption = _normalize_caption("".join(self.caption_parts))
                self.figures.append(self.current)
                self.current = None
                self.caption_depth = 0
                self.caption_parts = []

    def handle_data(self, data: str) -> None:
        if self.current is not None and self.caption_depth:
            self.caption_parts.append(data)


def _normalize_caption(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def framework_caption_score(caption: str) -> int:
    normalized = _normalize_caption(caption)
    if not CAPTION_LABEL_PATTERN.search(normalized):
        return -100
    score = 0
    for pattern, weight in POSITIVE_CAPTION_TERMS:
        if pattern.search(normalized):
            score += weight
    for pattern, penalty in NEGATIVE_CAPTION_TERMS:
        if pattern.search(normalized):
            score -= penalty
    return score


def results_table_caption_score(caption: str) -> int:
    normalized = _normalize_caption(caption)
    label_match = TABLE_CAPTION_LABEL_PATTERN.search(normalized)
    if not label_match or label_match.start() > 32:
        return -100
    score = 0
    for pattern, weight in RESULTS_TABLE_POSITIVE_TERMS:
        if pattern.search(normalized):
            score += weight
    for pattern, penalty in RESULTS_TABLE_NEGATIVE_TERMS:
        if pattern.search(normalized):
            score -= penalty
    return score


def _figure_label(caption: str) -> str:
    match = CAPTION_LABEL_PATTERN.search(caption)
    return f"Figure {match.group(1)}" if match else "Framework figure"


def _table_label(caption: str) -> str:
    match = TABLE_CAPTION_LABEL_PATTERN.search(caption)
    return f"Table {match.group(1)}" if match else "SOTA results table"


def _bounded_response_bytes(response: requests.Response, max_bytes: int) -> bytes:
    content_length = int(response.headers.get("Content-Length") or 0)
    if content_length > max_bytes:
        raise ReaderError("框架图资源超过读取上限")
    chunks: list[bytes] = []
    received = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        received += len(chunk)
        if received > max_bytes:
            raise ReaderError("框架图资源超过读取上限")
        chunks.append(chunk)
    return b"".join(chunks)


def _download_public_bytes(url: str, *, max_bytes: int, accept: str) -> tuple[bytes, str, str]:
    current_url = url
    for _ in range(MAX_REDIRECTS + 1):
        if not _is_public_url(current_url):
            raise ReaderError("框架图地址不是可公开访问的安全 URL")
        response = requests.get(
            current_url,
            headers={"Accept": accept, "User-Agent": RESOURCE_USER_AGENT},
            timeout=max(settings.zotero.request_timeout_seconds, 5),
            stream=True,
            allow_redirects=False,
        )
        try:
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                if not location:
                    raise ReaderError("框架图下载重定向缺少目标地址")
                current_url = urljoin(current_url, location)
                continue
            response.raise_for_status()
            return (
                _bounded_response_bytes(response, max_bytes),
                response.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold(),
                current_url,
            )
        finally:
            response.close()
    raise ReaderError("框架图下载重定向次数过多")


def _image_format(image_bytes: bytes, content_type: str) -> tuple[str, str]:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "webp", "image/webp"
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "gif", "image/gif"
    raise ReaderError(f"框架图资源不是受支持的安全位图: {content_type or 'unknown'}")


def _image_dimensions(image_bytes: bytes, extension: str) -> tuple[int | None, int | None]:
    try:
        document = pymupdf.open(stream=image_bytes, filetype=extension)
        try:
            rect = document[0].rect
            return round(rect.width), round(rect.height)
        finally:
            document.close()
    except Exception:
        return None, None


def extract_arxiv_framework_figure(arxiv_id: str) -> FrameworkFigureAsset | None:
    html_url = f"https://arxiv.org/html/{arxiv_id}"
    html_bytes, _, resolved_html_url = _download_public_bytes(
        html_url,
        max_bytes=10 * 1024 * 1024,
        accept="text/html",
    )
    parser = _ArxivFigureParser(resolved_html_url)
    parser.feed(html_bytes.decode("utf-8", errors="replace"))
    ranked = sorted(
        (
            (framework_caption_score(figure.caption), index, figure)
            for index, figure in enumerate(parser.figures)
            if figure.image_url and figure.caption
        ),
        key=lambda entry: (entry[0], -entry[1]),
        reverse=True,
    )
    if not ranked or ranked[0][0] <= 0:
        return None
    _, _, figure = ranked[0]
    image_bytes, content_type, resolved_image_url = _download_public_bytes(
        figure.image_url,
        max_bytes=MAX_FIGURE_IMAGE_BYTES,
        accept="image/png,image/jpeg,image/webp,image/gif",
    )
    extension, media_type = _image_format(image_bytes, content_type)
    width, height = _image_dimensions(image_bytes, extension)
    return FrameworkFigureAsset(
        label=_figure_label(figure.caption),
        caption=figure.caption,
        source="arxiv-html",
        source_url=resolved_image_url,
        image_bytes=image_bytes,
        extension=extension,
        media_type=media_type,
        width=width,
        height=height,
    )


def _clip_to_page(rect: pymupdf.Rect, page_rect: pymupdf.Rect) -> pymupdf.Rect:
    clipped = rect & page_rect
    if clipped.is_empty or clipped.width < 40 or clipped.height < 40:
        return page_rect
    return clipped


def extract_framework_figure_from_pdf(
    pdf_bytes: bytes,
    source_url: str = "",
) -> FrameworkFigureAsset | None:
    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        best: tuple[int, int, str, pymupdf.Rect] | None = None
        for page_index in range(min(document.page_count, 100)):
            page = document[page_index]
            for block in page.get_text("blocks"):
                caption = _normalize_caption(str(block[4] or ""))
                score = framework_caption_score(caption)
                if score <= 0:
                    continue
                candidate = (score, page_index, caption, pymupdf.Rect(block[:4]))
                if best is None or candidate[0] > best[0]:
                    best = candidate
        if best is None:
            return None

        _, page_index, caption, caption_rect = best
        page = document[page_index]
        page_rect = page.rect
        image_rects: list[pymupdf.Rect] = []
        for image in page.get_image_info(xrefs=True):
            bbox = image.get("bbox")
            if not bbox:
                continue
            rect = pymupdf.Rect(bbox)
            if rect.width < page_rect.width * 0.2 or rect.height < page_rect.height * 0.08:
                continue
            if rect.y0 <= caption_rect.y1 + 24:
                image_rects.append(rect)
        if image_rects:
            clip = max(image_rects, key=lambda rect: rect.width * rect.height)
            clip = pymupdf.Rect(clip.x0 - 8, clip.y0 - 8, clip.x1 + 8, clip.y1 + 8)
        else:
            crop_height = min(page_rect.height * 0.62, max(caption_rect.y0, page_rect.height * 0.35))
            clip = pymupdf.Rect(
                page_rect.x0 + 18,
                max(page_rect.y0, caption_rect.y0 - crop_height),
                page_rect.x1 - 18,
                min(page_rect.y1, caption_rect.y1 + 8),
            )
        clip = _clip_to_page(clip, page_rect)
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=clip, alpha=False)
        return FrameworkFigureAsset(
            label=_figure_label(caption),
            caption=caption,
            source="pdf-caption-crop",
            source_url=source_url,
            image_bytes=pixmap.tobytes("png"),
            page_number=page_index + 1,
            width=pixmap.width,
            height=pixmap.height,
        )
    finally:
        document.close()


def _table_rect_near_caption(
    page: pymupdf.Page,
    caption_rect: pymupdf.Rect,
) -> pymupdf.Rect | None:
    try:
        detected = page.find_tables()
    except Exception as exc:
        logger.info("Unable to detect PDF table boundaries: %s", exc)
        return None

    page_rect = page.rect
    candidates: list[tuple[int, float, float, pymupdf.Rect]] = []
    for table in detected.tables:
        rect = pymupdf.Rect(table.bbox)
        if rect.width < page_rect.width * 0.25 or rect.height < page_rect.height * 0.018:
            continue
        if rect.y0 >= caption_rect.y1 - 4:
            direction = 0
            gap = max(rect.y0 - caption_rect.y1, 0)
        elif rect.y1 <= caption_rect.y0 + 4:
            direction = 1
            gap = max(caption_rect.y0 - rect.y1, 0)
        else:
            direction = 0
            gap = 0
        if gap > page_rect.height * 0.24:
            continue
        candidates.append((direction, gap, -(rect.width * rect.height), rect))
    if not candidates:
        return None
    return min(candidates, key=lambda entry: entry[:3])[3]


def _results_table_fallback_clip(
    page_rect: pymupdf.Rect,
    caption_rect: pymupdf.Rect,
) -> pymupdf.Rect:
    if caption_rect.width >= page_rect.width * 0.62:
        x0, x1 = page_rect.x0 + 18, page_rect.x1 - 18
    else:
        midpoint = (page_rect.x0 + page_rect.x1) / 2
        if caption_rect.x1 <= midpoint + 12:
            x0, x1 = page_rect.x0 + 18, midpoint - 6
        else:
            x0, x1 = midpoint + 6, page_rect.x1 - 18
    return pymupdf.Rect(
        x0,
        max(page_rect.y0, caption_rect.y0 - 8),
        x1,
        min(page_rect.y1 - 18, caption_rect.y1 + page_rect.height * 0.4),
    )


def extract_results_table_from_pdf(
    pdf_bytes: bytes,
    source_url: str = "",
) -> FrameworkFigureAsset | None:
    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        best: tuple[int, int, str, pymupdf.Rect] | None = None
        for page_index in range(min(document.page_count, 100)):
            page = document[page_index]
            for block in page.get_text("blocks"):
                caption = _normalize_caption(str(block[4] or ""))
                score = results_table_caption_score(caption)
                if score <= 0:
                    continue
                candidate = (score, page_index, caption, pymupdf.Rect(block[:4]))
                if best is None or candidate[0] > best[0]:
                    best = candidate
        if best is None:
            return None

        _, page_index, caption, caption_rect = best
        page = document[page_index]
        page_rect = page.rect
        table_rect = _table_rect_near_caption(page, caption_rect)
        if table_rect is not None:
            clip = pymupdf.Rect(
                min(caption_rect.x0, table_rect.x0) - 8,
                min(caption_rect.y0, table_rect.y0) - 8,
                max(caption_rect.x1, table_rect.x1) + 8,
                max(caption_rect.y1, table_rect.y1) + 8,
            )
        else:
            clip = _results_table_fallback_clip(page_rect, caption_rect)
        clip = _clip_to_page(clip, page_rect)
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=clip, alpha=False)
        return FrameworkFigureAsset(
            label=_table_label(caption),
            caption=caption,
            source="pdf-caption-crop",
            source_url=source_url,
            image_bytes=pixmap.tobytes("png"),
            page_number=page_index + 1,
            width=pixmap.width,
            height=pixmap.height,
        )
    finally:
        document.close()


def _pdf_figure_worker(pdf_bytes: bytes, source_url: str, result_queue: Any) -> None:
    try:
        asset = extract_framework_figure_from_pdf(pdf_bytes, source_url)
        result_queue.put(("ok", asdict(asset) if asset else None))
    except Exception as exc:
        result_queue.put(("error", str(exc)))


def extract_framework_figure_from_pdf_bounded(
    pdf_bytes: bytes,
    source_url: str = "",
) -> FrameworkFigureAsset | None:
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_pdf_figure_worker,
        args=(pdf_bytes, source_url, result_queue),
        daemon=True,
    )
    process.start()
    try:
        status, payload = result_queue.get(timeout=PDF_FIGURE_TIMEOUT_SECONDS)
    except queue.Empty as exc:
        process.terminate()
        process.join(timeout=5)
        raise ReaderError("PDF 框架图提取超时") from exc
    finally:
        result_queue.close()
    process.join(timeout=5)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
    if status != "ok":
        raise ReaderError(str(payload))
    return FrameworkFigureAsset(**payload) if payload else None


def _pdf_results_table_worker(pdf_bytes: bytes, source_url: str, result_queue: Any) -> None:
    try:
        asset = extract_results_table_from_pdf(pdf_bytes, source_url)
        result_queue.put(("ok", asdict(asset) if asset else None))
    except Exception as exc:
        result_queue.put(("error", str(exc)))


def extract_results_table_from_pdf_bounded(
    pdf_bytes: bytes,
    source_url: str = "",
) -> FrameworkFigureAsset | None:
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_pdf_results_table_worker,
        args=(pdf_bytes, source_url, result_queue),
        daemon=True,
    )
    process.start()
    try:
        status, payload = result_queue.get(timeout=PDF_FIGURE_TIMEOUT_SECONDS)
    except queue.Empty as exc:
        process.terminate()
        process.join(timeout=5)
        raise ReaderError("PDF SOTA 对比表提取超时") from exc
    finally:
        result_queue.close()
    process.join(timeout=5)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
    if status != "ok":
        raise ReaderError(str(payload))
    return FrameworkFigureAsset(**payload) if payload else None


def _safe_path_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(value))


def zotero_figure_root() -> Path:
    configured = settings.paths.zotero_content_cache_dir
    root = Path(configured) if configured else REPO_ROOT / "data" / "zotero_cache"
    if not root.is_absolute():
        root = REPO_ROOT / root
    return root


def _zotero_figure_directory(user_id: str, item_key: str) -> Path:
    return (
        zotero_figure_root()
        / _safe_path_segment(user_id)
        / "figures"
        / _safe_path_segment(item_key)
    )


def save_zotero_analysis_asset(
    user_id: str,
    item_key: str,
    asset: FrameworkFigureAsset,
    *,
    kind: str,
) -> dict[str, Any]:
    digest = hashlib.sha256(asset.image_bytes).hexdigest()[:24]
    directory = _zotero_figure_directory(user_id, item_key)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{kind}-{digest}.{asset.extension}"
    target = directory / filename
    if not target.exists():
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(asset.image_bytes)
        temporary.replace(target)
    for old_file in directory.glob(f"{kind}-*"):
        if old_file != target and old_file.is_file():
            old_file.unlink()
    return {
        "id": digest,
        "kind": kind,
        "label": asset.label,
        "caption": asset.caption,
        "source": asset.source,
        "source_url": asset.source_url,
        "page_number": asset.page_number,
        "width": asset.width,
        "height": asset.height,
        "media_type": asset.media_type,
        "filename": filename,
    }


def save_zotero_framework_figure(
    user_id: str,
    item_key: str,
    asset: FrameworkFigureAsset,
) -> dict[str, Any]:
    return save_zotero_analysis_asset(
        user_id,
        item_key,
        asset,
        kind=FRAMEWORK_FIGURE_KIND,
    )


def save_zotero_results_table(
    user_id: str,
    item_key: str,
    asset: FrameworkFigureAsset,
) -> dict[str, Any]:
    return save_zotero_analysis_asset(
        user_id,
        item_key,
        asset,
        kind=RESULTS_TABLE_KIND,
    )


def zotero_figure_path(user_id: str, item_key: str, filename: str) -> Path:
    if Path(filename).name != filename:
        raise ReaderError("无效的论文图表文件名")
    directory = _zotero_figure_directory(user_id, item_key).resolve()
    target = (directory / filename).resolve()
    if target.parent != directory:
        raise ReaderError("无效的论文图表路径")
    return target


def _cached_zotero_asset(
    user_id: str,
    item: dict[str, Any],
    kind: str,
) -> dict[str, Any] | None:
    for cached in item.get("analysis_figures") or []:
        if not isinstance(cached, dict) or cached.get("kind") != kind:
            continue
        filename = str(cached.get("filename") or "")
        if not filename:
            continue
        try:
            if zotero_figure_path(user_id, str(item["item_key"]), filename).is_file():
                return cached
        except ReaderError:
            continue
    return None


def _extract_asset_from_available_pdfs(
    *,
    item: dict[str, Any],
    children: list[dict[str, Any]],
    zotero_user_id: int,
    client: Any,
    reading_context: str,
    extractor: Callable[[bytes, str], FrameworkFigureAsset | None],
    asset_name: str,
) -> FrameworkFigureAsset | None:
    public_urls = [candidate.url for candidate in direct_document_candidates(item, children)]
    public_pdf_match = re.search(r"(?m)^公开 PDF 地址：(https?://\S+)", reading_context)
    if public_pdf_match:
        public_urls.append(public_pdf_match.group(1).strip())

    seen_urls: set[str] = set()
    for public_pdf_url in public_urls:
        if public_pdf_url in seen_urls:
            continue
        seen_urls.add(public_pdf_url)
        try:
            pdf_bytes = download_public_pdf_bytes(public_pdf_url)
            asset = extractor(pdf_bytes, public_pdf_url)
        except (ReaderError, requests.RequestException, ValueError) as exc:
            logger.info("Unable to extract %s from public PDF %s: %s", asset_name, public_pdf_url, exc)
            continue
        if asset:
            return asset

    for attachment in children:
        if attachment.get("item_type") != "attachment":
            continue
        content_type = str(attachment.get("content_type") or "").casefold()
        filename = str(attachment.get("filename") or "").casefold()
        if content_type != "application/pdf" and not filename.endswith(".pdf"):
            continue
        if str(attachment.get("link_mode") or "").casefold() in {"linked_file", "linked_url"}:
            continue
        try:
            pdf_bytes = client.download_attachment(zotero_user_id, str(attachment["item_key"]))
            asset = extractor(pdf_bytes, "zotero-attachment")
        except Exception as exc:
            logger.info(
                "Unable to extract %s from Zotero attachment %s: %s",
                asset_name,
                attachment.get("item_key"),
                exc,
            )
            continue
        if asset:
            return asset
    return None


def extract_and_save_zotero_framework_figure(
    *,
    user_id: str,
    zotero_user_id: int,
    item: dict[str, Any],
    children: list[dict[str, Any]],
    client: Any,
    reading_context: str,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    if not force_refresh:
        cached = _cached_zotero_asset(user_id, item, FRAMEWORK_FIGURE_KIND)
        if cached:
            return cached

    raw = _raw_data(item)
    arxiv_values: list[object] = [item.get("doi"), item.get("url"), raw.get("extra"), raw.get("url")]
    for child in children:
        child_raw = _raw_data(child)
        arxiv_values.extend([child.get("url"), child_raw.get("url")])
    arxiv_id = extract_arxiv_id(*arxiv_values)
    if arxiv_id:
        try:
            asset = extract_arxiv_framework_figure(arxiv_id)
        except (ReaderError, requests.RequestException, ValueError) as exc:
            logger.info("Unable to extract arXiv HTML framework figure %s: %s", arxiv_id, exc)
            asset = None
        if asset:
            return save_zotero_framework_figure(user_id, str(item["item_key"]), asset)

    asset = _extract_asset_from_available_pdfs(
        item=item,
        children=children,
        zotero_user_id=zotero_user_id,
        client=client,
        reading_context=reading_context,
        extractor=extract_framework_figure_from_pdf_bounded,
        asset_name="framework figure",
    )
    if asset:
        return save_zotero_framework_figure(user_id, str(item["item_key"]), asset)
    return None


def extract_and_save_zotero_results_table(
    *,
    user_id: str,
    zotero_user_id: int,
    item: dict[str, Any],
    children: list[dict[str, Any]],
    client: Any,
    reading_context: str,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    if not force_refresh:
        cached = _cached_zotero_asset(user_id, item, RESULTS_TABLE_KIND)
        if cached:
            return cached

    asset = _extract_asset_from_available_pdfs(
        item=item,
        children=children,
        zotero_user_id=zotero_user_id,
        client=client,
        reading_context=reading_context,
        extractor=extract_results_table_from_pdf_bounded,
        asset_name="SOTA results table",
    )
    if not asset:
        return None
    return save_zotero_results_table(user_id, str(item["item_key"]), asset)


def extract_and_save_zotero_analysis_assets(
    *,
    user_id: str,
    zotero_user_id: int,
    item: dict[str, Any],
    children: list[dict[str, Any]],
    client: Any,
    reading_context: str,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    cached_framework = _cached_zotero_asset(user_id, item, FRAMEWORK_FIGURE_KIND)
    cached_results = _cached_zotero_asset(user_id, item, RESULTS_TABLE_KIND)
    framework = extract_and_save_zotero_framework_figure(
        user_id=user_id,
        zotero_user_id=zotero_user_id,
        item=item,
        children=children,
        client=client,
        reading_context=reading_context,
        force_refresh=force_refresh,
    )
    results_table = extract_and_save_zotero_results_table(
        user_id=user_id,
        zotero_user_id=zotero_user_id,
        item=item,
        children=children,
        client=client,
        reading_context=reading_context,
        force_refresh=force_refresh,
    )

    assets = [framework or cached_framework, results_table or cached_results]
    known_kinds = {FRAMEWORK_FIGURE_KIND, RESULTS_TABLE_KIND}
    extras = [
        entry
        for entry in item.get("analysis_figures") or []
        if isinstance(entry, dict) and entry.get("kind") not in known_kinds
    ]
    return [asset for asset in assets if asset] + extras
