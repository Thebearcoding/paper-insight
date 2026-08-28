from __future__ import annotations

import copy
import html
import logging
import math
import re
import threading
import time
from collections import OrderedDict
from datetime import date
from typing import Any

import requests

from config import settings
from online_query import clean_text, dblp_search_terms, expand_search_query, openalex_query_variants
from openalex_search import OPENALEX_SELECT_FIELDS, normalize_openalex_work


logger = logging.getLogger(__name__)

TOP_VENUE_RESULT_WINDOW = 1_000
TOP_VENUE_LABELS = (
    "AAAI",
    "IJCAI",
    "KDD",
    "SIGIR",
    "ACL",
    "CVPR",
    "ICCV",
    "NeurIPS",
    "ICML",
    "ICLR",
    "CHI",
)
DBLP_VENUE_QUERY = (
    "AAAI$|IJCAI$|KDD$|SIGIR$|ACL$|CVPR$|ICCV$|"
    "NeurIPS$|NIPS$|ICML$|ICLR$|CHI$"
)
DBLP_SEARCH_URL = "https://dblp.org/search/publ/api"
DBLP_MAX_HITS = 1_000
DBLP_MIN_INTERVAL_SECONDS = 1.0
OPENALEX_ENRICHMENT_BATCH_SIZE = 50
OPENALEX_FALLBACK_MAX_REQUESTS = 4
OPENALEX_FALLBACK_PAGE_SIZE = 200
_CACHE_MAX_ITEMS = 128

_cache: OrderedDict[tuple[object, ...], tuple[float, dict[str, Any]]] = OrderedDict()
_cache_lock = threading.RLock()
_request_lock = threading.Lock()
_last_dblp_request_at = 0.0


class TopVenueSearchError(Exception):
    pass


class TopVenueRateLimitError(TopVenueSearchError):
    pass


def _strip_markup(value: object) -> str:
    unescaped = html.unescape(str(value or ""))
    return clean_text(re.sub(r"<[^>]+>", " ", unescaped))


def canonical_dblp_venue(value: object) -> str | None:
    venue = clean_text(value)
    if not venue:
        return None
    normalized = venue.casefold()
    exact = {
        "aaai": "AAAI",
        "ijcai": "IJCAI",
        "kdd": "KDD",
        "sigir": "SIGIR",
        "cvpr": "CVPR",
        "iccv": "ICCV",
        "neurips": "NeurIPS",
        "nips": "NeurIPS",
        "icml": "ICML",
        "iclr": "ICLR",
        "chi": "CHI",
    }
    if normalized in exact:
        return exact[normalized]
    if re.fullmatch(r"acl(?: \(\d+\))?", normalized):
        return "ACL"
    return None


def canonical_openalex_venue(value: object) -> str | None:
    venue = _strip_markup(value)
    if not venue:
        return None
    normalized = venue.casefold()
    if any(
        marker in normalized
        for marker in (
            "workshop",
            "workshops",
            "companion",
            "extended abstract",
            "findings of",
        )
    ):
        return None
    if re.fullmatch(r"proceedings of the aaai conference on artificial intelligence", normalized):
        return "AAAI"
    if re.fullmatch(
        r"(?:proceedings of the (?:\d+(?:st|nd|rd|th) )?)?"
        r"international joint conference on artificial intelligence",
        normalized,
    ):
        return "IJCAI"
    if re.fullmatch(
        r"proceedings of the (?:\d+(?:st|nd|rd|th) )?"
        r"acm sigkdd conference on knowledge discovery and data mining",
        normalized,
    ):
        return "KDD"
    if re.fullmatch(
        r"proceedings of the (?:\d+(?:st|nd|rd|th) )?international acm sigir "
        r"conference on research and development in information retrieval",
        normalized,
    ):
        return "SIGIR"
    if re.match(
        r"^proceedings of the (?:\d+(?:st|nd|rd|th) )?annual meeting of the "
        r"association for computational linguistics(?: \(volume 1: long papers\))?$",
        normalized,
    ) or normalized == "annual meeting of the association for computational linguistics":
        return "ACL"
    if re.fullmatch(
        r"20\d{2} ieee/cvf conference on computer vision and pattern recognition \(cvpr\)",
        normalized,
    ):
        return "CVPR"
    if re.fullmatch(
        r"20\d{2} ieee/cvf international conference on computer vision \(iccv\)",
        normalized,
    ):
        return "ICCV"
    if re.match(r"^advances in neural information processing systems(?: \d+)?$", normalized):
        return "NeurIPS"
    if re.fullmatch(
        r"proceedings of the (?:\d+(?:st|nd|rd|th) )?international conference on machine learning",
        normalized,
    ):
        return "ICML"
    if normalized == "international conference on learning representations":
        return "ICLR"
    if re.fullmatch(
        r"proceedings of the (?:\d+(?:st|nd|rd|th) )?chi conference on human factors in computing systems",
        normalized,
    ):
        return "CHI"
    return None


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _dblp_authors(info: dict[str, Any]) -> list[str]:
    authors = info.get("authors")
    if not isinstance(authors, dict):
        return []
    values: list[str] = []
    for raw_author in _as_list(authors.get("author")):
        if isinstance(raw_author, dict):
            name = _strip_markup(raw_author.get("text"))
        else:
            name = _strip_markup(raw_author)
        if name and name not in values:
            values.append(name)
    return values


def _normalize_doi(value: object) -> str:
    doi = clean_text(value)
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    return doi.strip().casefold()


def _first_link(value: object) -> str:
    for entry in _as_list(value):
        url = clean_text(entry)
        if url.startswith(("https://", "http://")):
            return url
    return ""


def _parse_dblp_candidates(payload: dict[str, Any], from_year: int, to_year: int) -> list[dict[str, Any]]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    hits = result.get("hits") if isinstance(result.get("hits"), dict) else {}
    raw_hits = _as_list(hits.get("hit"))
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, object]] = set()
    for rank, hit in enumerate(raw_hits):
        if not isinstance(hit, dict):
            continue
        info = hit.get("info") if isinstance(hit.get("info"), dict) else {}
        venue = next(
            (
                canonical
                for raw_venue in _as_list(info.get("venue"))
                if (canonical := canonical_dblp_venue(raw_venue)) is not None
            ),
            None,
        )
        if venue is None:
            continue
        try:
            year = int(info.get("year"))
        except (TypeError, ValueError):
            continue
        if not from_year <= year <= to_year:
            continue
        title = _strip_markup(info.get("title"))
        if not title:
            continue
        doi = _normalize_doi(info.get("doi"))
        dedupe_key: tuple[str, object] = (
            "doi" if doi else "title",
            doi or (title.casefold(), year),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        record_key = clean_text(info.get("key")) or clean_text(hit.get("@id"))
        dblp_url = _first_link(info.get("url"))
        if not dblp_url and record_key:
            dblp_url = f"https://dblp.org/rec/{record_key}"
        candidates.append(
            {
                "key": record_key or f"{venue}-{year}-{rank}",
                "title": title,
                "authors": _dblp_authors(info),
                "venue": venue,
                "year": year,
                "doi": doi,
                "ee": _first_link(info.get("ee")),
                "dblp_url": dblp_url,
                "rank": rank,
            }
        )
    return candidates


def _request_dblp(query: str) -> dict[str, Any]:
    global _last_dblp_request_at
    with _request_lock:
        elapsed = time.monotonic() - _last_dblp_request_at
        if elapsed < DBLP_MIN_INTERVAL_SECONDS:
            time.sleep(DBLP_MIN_INTERVAL_SECONDS - elapsed)
        try:
            response = requests.get(
                DBLP_SEARCH_URL,
                params={
                    "q": f"{dblp_search_terms(query)} {DBLP_VENUE_QUERY}",
                    "f": "0",
                    "h": str(DBLP_MAX_HITS),
                    "format": "json",
                },
                headers={
                    "Accept": "application/json",
                    "User-Agent": "paper-insight/0.1 (mailto:paper-insight@athebear.me)",
                },
                timeout=max(settings.openalex.timeout_seconds, 20),
            )
        except requests.RequestException as exc:
            raise TopVenueSearchError(f"DBLP request failed: {exc}") from exc
        finally:
            _last_dblp_request_at = time.monotonic()
    if response.status_code in {429, 503}:
        raise TopVenueRateLimitError("DBLP search is temporarily rate limited")
    if not response.ok:
        raise TopVenueSearchError(
            f"DBLP returned {response.status_code}: {clean_text(response.text)[:300]}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise TopVenueSearchError("DBLP returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise TopVenueSearchError("DBLP returned an invalid payload")
    return payload


def _request_openalex(params: dict[str, str]) -> dict[str, Any]:
    if settings.openalex.api_key:
        params = {**params, "api_key": settings.openalex.api_key}
    try:
        response = requests.get(
            f"{settings.openalex.base_url}/works",
            params=params,
            headers={"User-Agent": "paper-insight/0.1 (top-conference enrichment)"},
            timeout=settings.openalex.timeout_seconds,
        )
    except requests.RequestException as exc:
        raise TopVenueSearchError(f"OpenAlex request failed: {exc}") from exc
    if response.status_code == 429:
        raise TopVenueRateLimitError("OpenAlex search quota is temporarily exhausted")
    if not response.ok:
        raise TopVenueSearchError(
            f"OpenAlex returned {response.status_code}: {clean_text(response.text)[:300]}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise TopVenueSearchError("OpenAlex returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise TopVenueSearchError("OpenAlex returned an invalid payload")
    return payload


def _enrich_dblp_candidates(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    dois = list(dict.fromkeys(candidate["doi"] for candidate in candidates if candidate["doi"]))
    enriched: dict[str, dict[str, Any]] = {}
    for start in range(0, len(dois), OPENALEX_ENRICHMENT_BATCH_SIZE):
        batch = dois[start : start + OPENALEX_ENRICHMENT_BATCH_SIZE]
        try:
            payload = _request_openalex(
                {
                    "filter": f"doi:{'|'.join(batch)}",
                    "per_page": str(len(batch)),
                    "select": OPENALEX_SELECT_FIELDS,
                }
            )
        except (requests.RequestException, TopVenueSearchError) as exc:
            logger.warning("OpenAlex top-venue enrichment failed: %s", exc)
            break
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            doi = _normalize_doi(item.get("doi"))
            if doi:
                enriched[doi] = item
    return enriched


def _paper_from_dblp_candidate(
    candidate: dict[str, Any],
    enriched_item: dict[str, Any] | None,
) -> dict[str, Any]:
    paper = normalize_openalex_work(enriched_item) if enriched_item else None
    doi_url = f"https://doi.org/{candidate['doi']}" if candidate["doi"] else ""
    external_url = candidate["ee"] or doi_url or candidate["dblp_url"]
    if paper is None:
        paper = {
            "id": f"dblp:{candidate['key']}",
            "title": candidate["title"],
            "abstract": "",
            "authors": candidate["authors"],
            "keywords": [],
            "pdf": None,
            "venue": candidate["venue"],
            "primary_area": None,
            "code_status": "unknown",
            "online": {
                "provider": "DBLP",
                "work_id": candidate["key"],
                "url": external_url,
                "provider_url": candidate["dblp_url"] or None,
                "dblp_url": candidate["dblp_url"] or None,
                "pdf_url": None,
                "doi": doi_url or None,
                "publication_year": candidate["year"],
                "publication_date": None,
                "cited_by_count": 0,
                "is_oa": False,
                "work_type": "Conference",
                "top_venue": candidate["venue"],
            },
        }
        return paper

    paper["id"] = f"dblp:{candidate['key']}"
    paper["title"] = candidate["title"]
    paper["authors"] = paper.get("authors") or candidate["authors"]
    paper["venue"] = candidate["venue"]
    online = paper["online"]
    online.update(
        {
            "provider": "DBLP + OpenAlex",
            "work_id": candidate["key"],
            "url": external_url or online.get("url"),
            "provider_url": candidate["dblp_url"] or None,
            "dblp_url": candidate["dblp_url"] or None,
            "doi": doi_url or online.get("doi"),
            "top_venue": candidate["venue"],
            "work_type": "Conference",
        }
    )
    return paper


def _source_names(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for location_key in ("primary_location", "best_oa_location"):
        location = item.get(location_key)
        if not isinstance(location, dict):
            continue
        source = location.get("source")
        if isinstance(source, dict):
            value = clean_text(source.get("display_name"))
            if value and value not in values:
                values.append(value)
        raw_source_name = clean_text(location.get("raw_source_name"))
        if raw_source_name and raw_source_name not in values:
            values.append(raw_source_name)
    return values


def _openalex_fallback_papers(
    query: str,
    *,
    from_year: int,
    to_year: int,
    sort: str,
) -> list[dict[str, Any]]:
    current_date = date.today()
    to_date = current_date if to_year >= current_date.year else date(to_year, 12, 31)
    variants = openalex_query_variants(query)
    collected: dict[str, tuple[int, int, dict[str, Any]]] = {}
    requests_used = 0
    for variant_index, variant in enumerate(variants):
        if requests_used >= OPENALEX_FALLBACK_MAX_REQUESTS:
            break
        payload = _request_openalex(
            {
                "search": variant,
                "filter": (
                    f"from_publication_date:{from_year}-01-01,"
                    f"to_publication_date:{to_date.isoformat()},"
                    "type:conference-paper,is_retracted:false,is_paratext:false"
                ),
                "sort": "relevance_score:desc",
                "page": "1",
                "per_page": str(OPENALEX_FALLBACK_PAGE_SIZE),
                "select": OPENALEX_SELECT_FIELDS,
            }
        )
        requests_used += 1
        for rank, item in enumerate(payload.get("results") or []):
            if not isinstance(item, dict):
                continue
            top_venue = next(
                (
                    label
                    for source_name in _source_names(item)
                    if (label := canonical_openalex_venue(source_name)) is not None
                ),
                None,
            )
            if top_venue is None:
                continue
            paper = normalize_openalex_work(item)
            if paper is None:
                continue
            paper["venue"] = top_venue
            paper["online"].update(
                {
                    "provider": "OpenAlex",
                    "provider_url": paper["online"].get("openalex_url"),
                    "top_venue": top_venue,
                    "work_type": "Conference",
                }
            )
            collected.setdefault(paper["id"], (variant_index, rank, paper))

    values = list(collected.values())
    if sort == "newest":
        values.sort(
            key=lambda entry: (
                entry[2]["online"].get("publication_date") or "",
                -(entry[0] * OPENALEX_FALLBACK_PAGE_SIZE + entry[1]),
            ),
            reverse=True,
        )
    elif sort == "cited":
        values.sort(
            key=lambda entry: (
                entry[2]["online"].get("cited_by_count") or 0,
                -(entry[0] * OPENALEX_FALLBACK_PAGE_SIZE + entry[1]),
            ),
            reverse=True,
        )
    else:
        values.sort(key=lambda entry: (entry[0], entry[1]))
    return [entry[2] for entry in values]


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
    return copy.deepcopy(value)


def _set_cached(key: tuple[object, ...], value: dict[str, Any]) -> None:
    if settings.openalex.cache_ttl_seconds <= 0:
        return
    with _cache_lock:
        _cache[key] = (time.monotonic(), copy.deepcopy(value))
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX_ITEMS:
            _cache.popitem(last=False)


def clear_search_cache() -> None:
    global _last_dblp_request_at
    with _cache_lock:
        _cache.clear()
    _last_dblp_request_at = 0.0


def search_top_venue_papers(
    query: str,
    *,
    from_year: int,
    to_year: int,
    page: int = 1,
    per_page: int = 8,
    sort: str = "relevance",
) -> dict[str, Any]:
    normalized_query = clean_text(query)
    if not normalized_query:
        raise ValueError("query is required")
    if sort not in {"relevance", "newest", "cited"}:
        raise ValueError(f"unsupported sort: {sort}")
    if from_year > to_year:
        raise ValueError("from_year must not exceed to_year")
    if page < 1 or per_page < 1:
        raise ValueError("page and per_page must be positive")
    offset = (page - 1) * per_page
    if offset >= TOP_VENUE_RESULT_WINDOW:
        raise ValueError("top-venue search supports the first 1,000 results")

    effective_query = expand_search_query(normalized_query)
    cache_key = (effective_query.casefold(), from_year, to_year, sort)
    dataset = _get_cached(cache_key)
    cached = dataset is not None
    if dataset is None:
        try:
            candidates = _parse_dblp_candidates(
                _request_dblp(effective_query),
                from_year,
                to_year,
            )
            if candidates:
                enriched = _enrich_dblp_candidates(candidates)
                provider = "DBLP + OpenAlex" if enriched else "DBLP"
                papers = [
                    _paper_from_dblp_candidate(candidate, enriched.get(candidate["doi"]))
                    for candidate in candidates
                ]
            else:
                provider = "OpenAlex"
                papers = _openalex_fallback_papers(
                    effective_query,
                    from_year=from_year,
                    to_year=to_year,
                    sort=sort,
                )
            if sort == "newest":
                papers.sort(
                    key=lambda paper: (
                        paper["online"].get("publication_year") or 0,
                        paper["online"].get("publication_date") or "",
                    ),
                    reverse=True,
                )
            elif sort == "cited":
                papers.sort(
                    key=lambda paper: paper["online"].get("cited_by_count") or 0,
                    reverse=True,
                )
        except (requests.RequestException, TopVenueSearchError) as exc:
            logger.warning("DBLP top-venue search failed, using OpenAlex fallback: %s", exc)
            provider = "OpenAlex"
            papers = _openalex_fallback_papers(
                effective_query,
                from_year=from_year,
                to_year=to_year,
                sort=sort,
            )
        dataset = {
            "papers": papers[:TOP_VENUE_RESULT_WINDOW],
            "provider": provider,
            "effective_query": effective_query,
        }
        _set_cached(cache_key, dataset)

    all_papers = dataset["papers"]
    total = len(all_papers)
    return {
        "papers": copy.deepcopy(all_papers[offset : offset + per_page]),
        "total": total,
        "page": page,
        "pages": math.ceil(total / per_page) if total else 1,
        "provider": dataset["provider"],
        "cached": cached,
        "year_range": {"from": from_year, "to": to_year},
        "venue_scope": "top",
        "venues": list(TOP_VENUE_LABELS),
        "effective_query": dataset["effective_query"],
    }
