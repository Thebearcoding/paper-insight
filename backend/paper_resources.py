from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urljoin, urlparse

import requests

from config import REPO_ROOT, settings
from utils import (
    MIN_EXTRACTED_PDF_TEXT_CHARS,
    PDF_HEADERS,
    ReaderError,
    cache_paper_content,
    extract_pdf_text,
    get_cached_paper_content,
    reader,
)


logger = logging.getLogger(__name__)
MAX_REDIRECTS = 5
MAX_REPOSITORIES = 3
MAX_README_CHARS = 16_000
REPOSITORY_CACHE_TTL_SECONDS = 24 * 60 * 60
RESOURCE_USER_AGENT = "Paper-Insight/1.0 scholarly resource resolver"

ARXIV_ID_PATTERN = re.compile(
    r"(?i)(?:arxiv\s*[:/]\s*|arxiv\.org/(?:abs|pdf|html)/|10\.48550/arxiv\.)"
    r"((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?)"
)
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", flags=re.IGNORECASE)
REPOSITORY_HOSTS = {"github.com", "gitlab.com", "bitbucket.org"}
CODE_CONTEXT_PATTERN = re.compile(
    r"(?i)\b(?:code|source|implementation|repository|available|release|project\s+page)\b|"
    r"代码|源码|实现|仓库|开源"
)
GITHUB_RESERVED_PATHS = {
    "about",
    "apps",
    "collections",
    "contact",
    "events",
    "explore",
    "features",
    "issues",
    "marketplace",
    "new",
    "notifications",
    "orgs",
    "pricing",
    "pulls",
    "search",
    "settings",
    "site",
    "sponsors",
    "topics",
    "trending",
}


@dataclass(frozen=True)
class DocumentCandidate:
    url: str
    source: str


@dataclass(frozen=True)
class ResolvedDocument:
    content: str
    url: str
    source: str


def _raw_data(record: dict[str, Any]) -> dict[str, Any]:
    raw = record.get("raw")
    if not isinstance(raw, dict):
        return {}
    data = raw.get("data")
    return data if isinstance(data, dict) else {}


def _record_text_fields(record: dict[str, Any]) -> list[str]:
    raw = _raw_data(record)
    values = [
        record.get("url"),
        record.get("abstract_note"),
        record.get("note"),
        record.get("annotation_text"),
        record.get("annotation_comment"),
        raw.get("url"),
        raw.get("abstractNote"),
        raw.get("extra"),
        raw.get("note"),
        raw.get("annotationText"),
        raw.get("annotationComment"),
    ]
    return [str(value) for value in values if value]


def extract_arxiv_id(*values: object) -> str | None:
    for value in values:
        match = ARXIV_ID_PATTERN.search(str(value or ""))
        if match:
            return match.group(1)
    return None


def _openreview_pdf_url(value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.hostname not in {"openreview.net", "www.openreview.net"}:
        return None
    paper_id = (parse_qs(parsed.query).get("id") or [""])[0].strip()
    if not paper_id:
        return None
    return f"https://openreview.net/pdf?id={quote(paper_id, safe='')}"


def _looks_like_pdf_url(value: str) -> bool:
    parsed = urlparse(value)
    path = parsed.path.casefold().rstrip("/")
    return path.endswith(".pdf") or path.endswith("/pdf") or "/pdf/" in path


def _canonical_document_candidate(value: str, source: str) -> DocumentCandidate | None:
    text = value.strip().rstrip(".,;:!?)]}")
    if not text:
        return None
    arxiv_id = extract_arxiv_id(text)
    if arxiv_id:
        return DocumentCandidate(f"https://arxiv.org/pdf/{arxiv_id}", "arxiv")
    openreview_url = _openreview_pdf_url(text)
    if openreview_url:
        return DocumentCandidate(openreview_url, "openreview")
    if _looks_like_pdf_url(text):
        return DocumentCandidate(text, source)
    return None


def direct_document_candidates(
    item: dict[str, Any],
    children: list[dict[str, Any]],
) -> list[DocumentCandidate]:
    candidates: list[DocumentCandidate] = []
    values: list[tuple[str, str]] = []
    for child in children:
        raw = _raw_data(child)
        for value in [child.get("url"), raw.get("url")]:
            if value:
                values.append((str(value), "zotero-attachment-url"))
    raw_item = _raw_data(item)
    for value in [item.get("url"), raw_item.get("url")]:
        if value:
            values.append((str(value), "zotero-item-url"))
    for text in [item.get("doi"), raw_item.get("DOI"), raw_item.get("extra")]:
        if text:
            arxiv_id = extract_arxiv_id(text)
            if arxiv_id:
                values.append((f"https://arxiv.org/pdf/{arxiv_id}", "arxiv"))
            for url in URL_PATTERN.findall(str(text)):
                values.append((url, "zotero-extra-url"))

    seen: set[str] = set()
    for value, source in values:
        candidate = _canonical_document_candidate(value, source)
        if candidate and candidate.url not in seen:
            seen.add(candidate.url)
            candidates.append(candidate)
    return candidates


def _doi(item: dict[str, Any]) -> str | None:
    raw = _raw_data(item)
    value = str(item.get("doi") or raw.get("DOI") or "").strip()
    if not value:
        return None
    value = re.sub(r"(?i)^https?://(?:dx\.)?doi\.org/", "", value)
    value = re.sub(r"(?i)^doi:\s*", "", value)
    return value.strip() or None


def _candidate_from_location(location: Any, source: str) -> DocumentCandidate | None:
    if not isinstance(location, dict):
        return None
    pdf_url = str(location.get("pdf_url") or location.get("url") or "").strip()
    if not pdf_url:
        return None
    candidate = _canonical_document_candidate(pdf_url, source)
    if candidate:
        return candidate
    parsed = urlparse(pdf_url)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        return DocumentCandidate(pdf_url, source)
    return None


def semantic_scholar_candidates(item: dict[str, Any]) -> list[DocumentCandidate]:
    raw = _raw_data(item)
    arxiv_id = extract_arxiv_id(item.get("doi"), item.get("url"), raw.get("extra"), raw.get("url"))
    doi = _doi(item)
    external_id = f"ARXIV:{arxiv_id}" if arxiv_id else f"DOI:{doi}" if doi else ""
    if not external_id:
        return []
    response = requests.get(
        f"https://api.semanticscholar.org/graph/v1/paper/{quote(external_id, safe='')}",
        params={"fields": "openAccessPdf"},
        headers={"User-Agent": RESOURCE_USER_AGENT},
        timeout=min(max(settings.zotero.request_timeout_seconds, 5), 15),
    )
    try:
        if response.status_code in {404, 429}:
            return []
        response.raise_for_status()
        payload = response.json()
    finally:
        response.close()
    if not isinstance(payload, dict):
        return []
    candidate = _candidate_from_location(payload.get("openAccessPdf"), "semantic-scholar")
    return [candidate] if candidate else []


def crossref_candidates(item: dict[str, Any]) -> list[DocumentCandidate]:
    doi = _doi(item)
    if not doi:
        return []
    response = requests.get(
        f"https://api.crossref.org/works/{quote(doi, safe='')}",
        headers={"User-Agent": RESOURCE_USER_AGENT},
        timeout=min(max(settings.zotero.request_timeout_seconds, 5), 15),
    )
    try:
        if response.status_code in {404, 429}:
            return []
        response.raise_for_status()
        payload = response.json()
    finally:
        response.close()
    if not isinstance(payload, dict):
        return []
    message = payload.get("message") if isinstance(payload, dict) else None
    links = message.get("link") if isinstance(message, dict) else None
    candidates: list[DocumentCandidate] = []
    for link in links if isinstance(links, list) else []:
        if not isinstance(link, dict):
            continue
        content_type = str(link.get("content-type") or "").casefold()
        url = str(link.get("URL") or "").strip()
        if "pdf" not in content_type or not url:
            continue
        candidate = _candidate_from_location({"url": url}, "crossref")
        if candidate:
            candidates.append(candidate)
    return candidates


def openalex_candidates(item: dict[str, Any]) -> list[DocumentCandidate]:
    doi = _doi(item)
    if not doi:
        return []
    response = requests.get(
        f"https://api.openalex.org/works/{quote(f'https://doi.org/{doi}', safe=':/')}",
        headers={"User-Agent": RESOURCE_USER_AGENT},
        timeout=min(max(settings.zotero.request_timeout_seconds, 5), 15),
    )
    try:
        if response.status_code in {404, 429}:
            return []
        response.raise_for_status()
        payload = response.json()
    finally:
        response.close()
    if not isinstance(payload, dict):
        return []
    locations = [payload.get("best_oa_location"), payload.get("primary_location")]
    if isinstance(payload.get("locations"), list):
        locations.extend(payload["locations"])
    candidates: list[DocumentCandidate] = []
    seen: set[str] = set()
    for location in locations:
        candidate = _candidate_from_location(location, "openalex")
        if candidate and candidate.url not in seen:
            seen.add(candidate.url)
            candidates.append(candidate)
    return candidates


def _is_public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        return False
    hostname = parsed.hostname.casefold()
    if hostname == "localhost" or hostname.endswith(".local"):
        return False
    try:
        default_port = 443 if parsed.scheme == "https" else 80
        addresses = {entry[4][0] for entry in socket.getaddrinfo(hostname, parsed.port or default_port)}
    except OSError:
        return False
    try:
        return bool(addresses) and all(ipaddress.ip_address(address).is_global for address in addresses)
    except ValueError:
        return False


def _download_pdf_text(url: str) -> str:
    current_url = url
    max_bytes = max(settings.zotero.max_attachment_mb, 1) * 1024 * 1024
    for _ in range(MAX_REDIRECTS + 1):
        if not _is_public_url(current_url):
            raise ReaderError("PDF 地址不是可公开访问的安全 URL")
        response = requests.get(
            current_url,
            headers={**PDF_HEADERS, "User-Agent": RESOURCE_USER_AGENT},
            timeout=max(settings.zotero.request_timeout_seconds, 5),
            stream=True,
            allow_redirects=False,
        )
        try:
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                if not location:
                    raise ReaderError("PDF 下载重定向缺少目标地址")
                current_url = urljoin(current_url, location)
                continue
            response.raise_for_status()
            content_length = int(response.headers.get("Content-Length") or 0)
            if content_length > max_bytes:
                raise ReaderError(f"公开 PDF 超过 {settings.zotero.max_attachment_mb} MB 的读取上限")
            chunks: list[bytes] = []
            received = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                received += len(chunk)
                if received > max_bytes:
                    raise ReaderError(f"公开 PDF 超过 {settings.zotero.max_attachment_mb} MB 的读取上限")
                chunks.append(chunk)
            content = b"".join(chunks)
            content_type = response.headers.get("Content-Type", "")
            if "pdf" not in content_type.casefold() and not content.startswith(b"%PDF"):
                raise ReaderError(f"公开地址返回的不是 PDF: {content_type or 'unknown'}")
            return extract_pdf_text(content, current_url)
        finally:
            response.close()
    raise ReaderError("PDF 下载重定向次数过多")


def _document_cache_id(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return f"zotero-public-{digest}"


def fetch_document_candidate(candidate: DocumentCandidate) -> ResolvedDocument:
    cache_id = _document_cache_id(candidate.url)
    cached = get_cached_paper_content(cache_id, candidate.url)
    if cached:
        return ResolvedDocument(cached, candidate.url, candidate.source)
    if not _is_public_url(candidate.url):
        raise ReaderError("论文资源地址不是可公开访问的安全 URL")
    if candidate.source in {"arxiv", "openreview"}:
        content = _download_pdf_text(candidate.url)
        extraction_source = "pdf-text"
    else:
        try:
            content = reader(candidate.url)
            if len(content.strip()) < MIN_EXTRACTED_PDF_TEXT_CHARS:
                raise ReaderError("Reader 返回的论文正文过短")
            extraction_source = "jina-reader"
        except ReaderError as reader_error:
            logger.info("Reader failed for %s, trying bounded PDF download: %s", candidate.url, reader_error)
            content = _download_pdf_text(candidate.url)
            extraction_source = "pdf-text"
    cache_paper_content(cache_id, candidate.url, content, source=extraction_source)
    return ResolvedDocument(content, candidate.url, candidate.source)


def resolve_public_document(
    item: dict[str, Any],
    children: list[dict[str, Any]],
) -> tuple[ResolvedDocument | None, list[str]]:
    errors: list[str] = []
    seen: set[str] = set()

    def try_candidates(candidates: list[DocumentCandidate]) -> ResolvedDocument | None:
        for candidate in candidates:
            if candidate.url in seen:
                continue
            seen.add(candidate.url)
            try:
                return fetch_document_candidate(candidate)
            except (ReaderError, requests.RequestException, ValueError) as exc:
                errors.append(f"{candidate.source}: {exc}")
                logger.info("Unable to read public paper resource %s: %s", candidate.url, exc)
        return None

    resolved = try_candidates(direct_document_candidates(item, children))
    if resolved:
        return resolved, errors

    providers: tuple[Callable[[dict[str, Any]], list[DocumentCandidate]], ...] = (
        semantic_scholar_candidates,
        crossref_candidates,
        openalex_candidates,
    )
    for provider in providers:
        try:
            candidates = provider(item)
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{provider.__name__}: {exc}")
            continue
        resolved = try_candidates(candidates)
        if resolved:
            return resolved, errors
    return None, errors


def normalize_repository_url(value: str) -> str | None:
    text = value.strip().rstrip(".,;:!?)]}")
    parsed = urlparse(text)
    host = (parsed.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    if parsed.scheme not in {"http", "https"} or host not in REPOSITORY_HOSTS:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    if host == "github.com":
        if parts[0].casefold() in GITHUB_RESERVED_PATHS:
            return None
        parts = parts[:2]
    elif host == "gitlab.com":
        if "-" in parts:
            parts = parts[: parts.index("-")]
    else:
        parts = parts[:2]
    if len(parts) < 2:
        return None
    parts[-1] = re.sub(r"(?i)\.git$", "", parts[-1])
    return f"https://{host}/{'/'.join(parts)}"


def discover_code_repositories(
    item: dict[str, Any],
    children: list[dict[str, Any]],
    full_text: str | None = None,
) -> list[str]:
    texts = [(text, False) for text in _record_text_fields(item)]
    for child in children:
        texts.extend((text, False) for text in _record_text_fields(child))
    if full_text:
        texts.append((full_text, True))
    repositories: list[str] = []
    seen: set[str] = set()
    for text, require_code_context in texts:
        for match in URL_PATTERN.finditer(text):
            if require_code_context:
                start = max(0, match.start() - 160)
                end = min(len(text), match.end() + 160)
                if not CODE_CONTEXT_PATTERN.search(text[start:end]):
                    continue
            normalized = normalize_repository_url(match.group(0))
            if normalized and normalized not in seen:
                seen.add(normalized)
                repositories.append(normalized)
                if len(repositories) >= MAX_REPOSITORIES:
                    return repositories
    return repositories


def _repository_cache_dir() -> Path:
    configured = settings.paths.zotero_content_cache_dir
    root = Path(configured) if configured else REPO_ROOT / "data" / "zotero_cache"
    if not root.is_absolute():
        root = REPO_ROOT / root
    return root / "repositories"


def _repository_cache_path(repository_url: str) -> Path:
    digest = hashlib.sha256(repository_url.encode("utf-8")).hexdigest()[:24]
    return _repository_cache_dir() / f"{digest}.txt"


def fetch_repository_readme(repository_url: str) -> str | None:
    parsed = urlparse(repository_url)
    host = (parsed.hostname or "").casefold()
    parts = [part for part in parsed.path.split("/") if part]
    if host != "github.com" or len(parts) != 2:
        return None
    cache_path = _repository_cache_path(repository_url)
    try:
        if cache_path.exists() and time.time() - cache_path.stat().st_mtime < REPOSITORY_CACHE_TTL_SECONDS:
            cached = cache_path.read_text(encoding="utf-8").strip()
            return cached or None
    except OSError:
        pass

    response = requests.get(
        f"https://api.github.com/repos/{quote(parts[0], safe='')}/{quote(parts[1], safe='')}/readme",
        headers={
            "Accept": "application/vnd.github.raw+json",
            "User-Agent": RESOURCE_USER_AGENT,
        },
        timeout=min(max(settings.zotero.request_timeout_seconds, 5), 15),
    )
    try:
        if response.status_code in {403, 404, 429}:
            return None
        response.raise_for_status()
        content = response.text.strip()[:MAX_README_CHARS]
    finally:
        response.close()
    if not content:
        return None
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_suffix(".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(cache_path)
    except OSError as exc:
        logger.info("Unable to cache repository README for %s: %s", repository_url, exc)
    return content


def build_repository_context(repository_urls: list[str]) -> str:
    if not repository_urls:
        return ""
    sections = ["已发现的公开源码仓库（来自论文材料中的明确链接）："]
    for repository_url in repository_urls[:MAX_REPOSITORIES]:
        sections.append(f"- {repository_url}")
        try:
            readme = fetch_repository_readme(repository_url)
        except requests.RequestException as exc:
            logger.info("Unable to fetch repository README for %s: %s", repository_url, exc)
            readme = None
        if readme:
            sections.append(f"\n仓库 README 摘录（{repository_url}）：\n{readme}")
    return "\n".join(sections)
