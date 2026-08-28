from __future__ import annotations

import copy
import math
import re
import threading
import time
from collections import OrderedDict
from datetime import date
from typing import Any
from urllib.parse import urlparse

import requests

from config import settings


OPENALEX_RESULT_WINDOW = 10_000
OPENALEX_SELECT_FIELDS = (
    "id,doi,display_name,publication_year,publication_date,type,authorships,"
    "primary_location,best_oa_location,open_access,topics,keywords,"
    "cited_by_count,abstract_inverted_index,relevance_score,ids"
)
SORT_VALUES = {
    "relevance": "relevance_score:desc",
    "newest": "publication_date:desc,relevance_score:desc",
    "cited": "cited_by_count:desc,relevance_score:desc",
}
_CACHE_MAX_ITEMS = 256
_cache: OrderedDict[tuple[object, ...], tuple[float, dict[str, Any]]] = OrderedDict()
_cache_lock = threading.RLock()
_request_lock = threading.Lock()


class OpenAlexSearchError(Exception):
    pass


class OpenAlexRateLimitError(OpenAlexSearchError):
    pass


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def reconstruct_abstract(inverted_index: object) -> str:
    if not isinstance(inverted_index, dict):
        return ""

    positioned_words: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int) and position >= 0:
                positioned_words.append((position, clean_text(word)))
    positioned_words.sort(key=lambda item: item[0])
    return clean_text(" ".join(word for _position, word in positioned_words if word))


def _location_value(location: object, key: str) -> str:
    if not isinstance(location, dict):
        return ""
    value = clean_text(location.get(key))
    return value if value.startswith(("https://", "http://")) else ""


def _pdf_url(item: dict[str, Any]) -> str:
    open_access = item.get("open_access") if isinstance(item.get("open_access"), dict) else {}
    candidates = [
        _location_value(item.get("best_oa_location"), "pdf_url"),
        _location_value(item.get("primary_location"), "pdf_url"),
        clean_text(open_access.get("oa_url")),
    ]
    urls = list(
        dict.fromkeys(
            url
            for url in candidates
            if url.startswith(("https://", "http://"))
        )
    )

    def rank(url: str) -> tuple[int, int]:
        parsed = urlparse(url)
        host = parsed.netloc.casefold()
        path = parsed.path.casefold()
        if host.endswith("arxiv.org"):
            return (0, len(url))
        if path.endswith(".pdf") or "/pdf" in path:
            return (1, len(url))
        if host.endswith("dl.acm.org"):
            return (3, len(url))
        return (2, len(url))

    urls.sort(key=rank)
    return urls[0] if urls else ""


def _source_name(item: dict[str, Any]) -> str:
    for location_key in ("primary_location", "best_oa_location"):
        location = item.get(location_key)
        if not isinstance(location, dict):
            continue
        source = location.get("source")
        if isinstance(source, dict):
            display_name = clean_text(source.get("display_name"))
            if display_name:
                return display_name
        raw_source_name = clean_text(location.get("raw_source_name"))
        if raw_source_name:
            return raw_source_name
    return "Preprint" if clean_text(item.get("type")).casefold() == "preprint" else "Online"


def _authors(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for authorship in item.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author")
        display_name = clean_text(author.get("display_name")) if isinstance(author, dict) else ""
        name = display_name or clean_text(authorship.get("raw_author_name"))
        if name and name not in values:
            values.append(name)
    return values


def _keywords(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for group_name in ("keywords", "topics"):
        for entry in item.get(group_name) or []:
            if not isinstance(entry, dict):
                continue
            display_name = clean_text(entry.get("display_name"))
            if display_name and display_name.casefold() not in {
                value.casefold() for value in values
            }:
                values.append(display_name)
            if len(values) >= 8:
                return values
    return values


def _primary_area(item: dict[str, Any]) -> str:
    topics = item.get("topics") or []
    if not topics or not isinstance(topics[0], dict):
        return ""
    topic = topics[0]
    for key in ("subfield", "field", "domain"):
        group = topic.get(key)
        if isinstance(group, dict):
            display_name = clean_text(group.get("display_name"))
            if display_name:
                return display_name
    return clean_text(topic.get("display_name"))


def normalize_openalex_work(item: dict[str, Any]) -> dict[str, Any] | None:
    openalex_url = clean_text(item.get("id"))
    work_id = openalex_url.rstrip("/").rsplit("/", 1)[-1]
    title = clean_text(item.get("display_name"))
    if not work_id or not title:
        return None

    doi = clean_text(item.get("doi"))
    primary_url = _location_value(item.get("primary_location"), "landing_page_url")
    external_url = doi if doi.startswith(("https://", "http://")) else primary_url
    external_url = external_url or openalex_url
    pdf_url = _pdf_url(item)
    publication_year = item.get("publication_year")
    publication_date = clean_text(item.get("publication_date"))
    cited_by_count = item.get("cited_by_count")
    open_access = item.get("open_access") if isinstance(item.get("open_access"), dict) else {}

    try:
        normalized_year = int(publication_year) if publication_year is not None else None
    except (TypeError, ValueError):
        normalized_year = None
    try:
        normalized_citations = max(int(cited_by_count or 0), 0)
    except (TypeError, ValueError):
        normalized_citations = 0

    return {
        "id": f"openalex:{work_id}",
        "title": title,
        "abstract": reconstruct_abstract(item.get("abstract_inverted_index")),
        "authors": _authors(item),
        "keywords": _keywords(item),
        "pdf": pdf_url or None,
        "venue": _source_name(item),
        "primary_area": _primary_area(item) or None,
        "code_status": "unknown",
        "online": {
            "provider": "OpenAlex",
            "work_id": work_id,
            "url": external_url,
            "openalex_url": openalex_url,
            "pdf_url": pdf_url or None,
            "doi": doi or None,
            "publication_year": normalized_year,
            "publication_date": publication_date or None,
            "cited_by_count": normalized_citations,
            "is_oa": bool(open_access.get("is_oa")),
            "work_type": clean_text(item.get("type")) or None,
        },
    }


def _get_cached(key: tuple[object, ...]) -> dict[str, Any] | None:
    ttl = settings.openalex.cache_ttl_seconds
    if ttl <= 0:
        return None
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached is None:
            return None
        stored_at, value = cached
        if now - stored_at >= ttl:
            del _cache[key]
            return None
        _cache.move_to_end(key)
    result = copy.deepcopy(value)
    result["cached"] = True
    return result


def _set_cached(key: tuple[object, ...], value: dict[str, Any]) -> None:
    if settings.openalex.cache_ttl_seconds <= 0:
        return
    with _cache_lock:
        _cache[key] = (time.monotonic(), copy.deepcopy(value))
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX_ITEMS:
            _cache.popitem(last=False)


def clear_search_cache() -> None:
    with _cache_lock:
        _cache.clear()


def search_recent_papers(
    query: str,
    *,
    from_year: int,
    to_year: int,
    page: int = 1,
    per_page: int = 8,
    sort: str = "relevance",
    today: date | None = None,
) -> dict[str, Any]:
    normalized_query = clean_text(query)
    if not normalized_query:
        raise ValueError("query is required")
    if sort not in SORT_VALUES:
        raise ValueError(f"unsupported sort: {sort}")
    if from_year > to_year:
        raise ValueError("from_year must not exceed to_year")
    if page < 1 or per_page < 1:
        raise ValueError("page and per_page must be positive")
    if (page - 1) * per_page >= OPENALEX_RESULT_WINDOW:
        raise ValueError("OpenAlex supports the first 10,000 search results")

    cache_key = (
        normalized_query.casefold(),
        from_year,
        to_year,
        page,
        per_page,
        sort,
    )
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    with _request_lock:
        cached = _get_cached(cache_key)
        if cached is not None:
            return cached

        current_date = today or date.today()
        to_date = current_date if to_year >= current_date.year else date(to_year, 12, 31)
        filters = (
            f"from_publication_date:{from_year}-01-01,"
            f"to_publication_date:{to_date.isoformat()},"
            "type:article|preprint|conference-paper,"
            "is_retracted:false,is_paratext:false,has_abstract:true"
        )
        params = {
            "search": normalized_query,
            "filter": filters,
            "sort": SORT_VALUES[sort],
            "page": str(page),
            "per_page": str(per_page),
            "select": OPENALEX_SELECT_FIELDS,
        }
        if settings.openalex.api_key:
            params["api_key"] = settings.openalex.api_key

        try:
            response = requests.get(
                f"{settings.openalex.base_url}/works",
                params=params,
                headers={"User-Agent": "paper-insight/0.1 (online recent-paper search)"},
                timeout=settings.openalex.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise OpenAlexSearchError(f"OpenAlex request failed: {exc}") from exc

        if response.status_code == 429:
            raise OpenAlexRateLimitError("OpenAlex search quota is temporarily exhausted")
        if not response.ok:
            detail = clean_text(response.text)[:300]
            raise OpenAlexSearchError(
                f"OpenAlex returned {response.status_code}: {detail or response.reason}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise OpenAlexSearchError("OpenAlex returned invalid JSON") from exc

        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        try:
            total = max(int(meta.get("count") or 0), 0)
        except (TypeError, ValueError):
            total = 0
        papers = [
            normalized
            for item in payload.get("results") or []
            if isinstance(item, dict)
            and (normalized := normalize_openalex_work(item)) is not None
        ]
        accessible_pages = math.ceil(OPENALEX_RESULT_WINDOW / per_page)
        result = {
            "papers": papers,
            "total": total,
            "page": page,
            "pages": min(math.ceil(total / per_page), accessible_pages) if total else 1,
            "provider": "OpenAlex",
            "cached": False,
            "year_range": {"from": from_year, "to": to_year},
            "venue_scope": "all",
            "effective_query": normalized_query,
        }
        _set_cached(cache_key, result)
        return result
