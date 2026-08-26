from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import requests


OPENALEX_WORKS_URL = "https://api.openalex.org/works"
CROSSREF_WORKS_URL = "https://api.crossref.org/journals/{issn}/works"
CROSSREF_QUERY_URL = "https://api.crossref.org/works"


@dataclass(frozen=True)
class DblpPaper:
    id: str
    doi: str
    title: str
    authors: list[str]
    pages: str | None
    dblp_key: str | None
    section: str | None


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def plain_text_from_markup(value: object) -> str:
    return clean_text(re.sub(r"<[^>]+>", "", html.unescape(str(value or ""))))


def normalize_doi(value: object) -> str:
    doi = clean_text(value)
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    return doi.casefold()


def paper_id_from_doi(conference_id: str, doi: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize_doi(doi)).strip("-")
    return f"{conference_id.replace('_', '')}-{slug}"[:180]


def parse_dblp_proceedings(
    xml_text: str,
    *,
    conference_id: str,
    doi_prefix: str | None = None,
) -> list[DblpPaper]:
    root = ET.fromstring(xml_text)
    papers: list[DblpPaper] = []
    current_section: str | None = None
    normalized_prefix = normalize_doi(doi_prefix) if doi_prefix else ""

    for child in root:
        if child.tag == "h2":
            current_section = clean_text("".join(child.itertext())) or None
            continue
        if child.tag != "dblpcites":
            continue
        for node in child.iter("inproceedings"):
            doi = next(
                (
                    normalized
                    for entry in node.findall("ee")
                    if (normalized := normalize_doi(entry.text)).startswith("10.")
                ),
                "",
            )
            if not doi or (normalized_prefix and not doi.startswith(normalized_prefix)):
                continue
            title_node = node.find("title")
            title = clean_text("".join(title_node.itertext())) if title_node is not None else ""
            authors = [clean_text(author.text) for author in node.findall("author")]
            authors = [author for author in authors if author]
            if not title or not authors:
                continue
            papers.append(
                DblpPaper(
                    id=paper_id_from_doi(conference_id, doi),
                    doi=doi,
                    title=title,
                    authors=authors,
                    pages=clean_text(node.findtext("pages")) or None,
                    dblp_key=node.attrib.get("key"),
                    section=current_section,
                )
            )
    return papers


def load_text(source: str, *, user_agent: str) -> str:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        response = requests.get(source, headers={"User-Agent": user_agent}, timeout=90)
        response.raise_for_status()
        return response.text
    return Path(source).read_text(encoding="utf-8")


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"OpenAlex cache must be a JSON object: {path}")
    return {
        normalize_doi(doi): item
        for doi, item in raw.items()
        if normalize_doi(doi) and isinstance(item, dict)
    }


def save_cache(path: Path, cache: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(sorted(cache.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def fetch_crossref_journal_metadata(
    dois: list[str],
    cache: dict[str, dict[str, Any]],
    cache_path: Path,
    *,
    issn: str,
    from_date: str,
    until_date: str,
    user_agent: str,
    mailto: str | None = None,
    rows: int = 1000,
) -> dict[str, dict[str, Any]]:
    requested = {normalize_doi(doi) for doi in dois if normalize_doi(doi)}
    missing = requested.difference(cache)
    if not missing:
        return cache

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    cursor = "*"
    fetched = 0

    while missing:
        params = {
            "filter": f"from-pub-date:{from_date},until-pub-date:{until_date}",
            "select": "DOI,title,abstract,link,subject,volume,issue,published",
            "rows": str(max(1, min(rows, 1000))),
            "cursor": cursor,
        }
        if mailto:
            params["mailto"] = mailto
        payload = _request_json(
            session,
            CROSSREF_WORKS_URL.format(issn=issn),
            params,
            provider="Crossref",
        )
        message = payload.get("message") or {}
        items = message.get("items") or []
        if not items:
            break
        for item in items:
            if not isinstance(item, dict):
                continue
            doi = normalize_doi(item.get("DOI"))
            if doi in requested:
                cache[doi] = item
                missing.discard(doi)
        fetched += len(items)
        save_cache(cache_path, cache)
        print(f"Scanned {fetched} Crossref record(s); matched {len(requested) - len(missing)}/{len(requested)} DOI(s)")

        next_cursor = clean_text(message.get("next-cursor"))
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

    for doi in missing:
        cache.setdefault(doi, {})
    save_cache(cache_path, cache)
    return cache


def fetch_crossref_proceedings_metadata(
    cache: dict[str, dict[str, Any]],
    cache_path: Path,
    *,
    container_title: str,
    doi_prefixes: tuple[str, ...],
    from_date: str,
    until_date: str,
    user_agent: str,
    mailto: str | None = None,
    rows: int = 1000,
    max_pages: int = 10,
    expected_count: int | None = None,
    max_scans: int = 5,
) -> dict[str, dict[str, Any]]:
    """Fetch one proceedings volume by exact child DOI prefixes.

    Crossref's container-title query is ranked rather than an exact filter. The
    DOI prefixes owned by the parent proceedings therefore remain the source of
    truth, while the query only narrows the result window.
    """
    prefixes = tuple(normalize_doi(prefix).rstrip(".") + "." for prefix in doi_prefixes)
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    page_size = max(1, min(rows, 1000))

    for scan in range(1, max_scans + 1):
        before_scan = sum(doi.startswith(prefixes) and bool(item) for doi, item in cache.items())
        scan_matches: set[str] = set()
        for page in range(max_pages):
            params = {
                "query.container-title": container_title,
                "filter": (
                    f"from-pub-date:{from_date},until-pub-date:{until_date},"
                    "type:proceedings-article"
                ),
                "select": (
                    "DOI,title,container-title,author,abstract,link,subject,page,"
                    "published,publisher,event"
                ),
                "rows": str(page_size),
                "offset": str(page * page_size),
                "sort": "score",
                "order": "desc",
            }
            if mailto:
                params["mailto"] = mailto
            payload = _request_json(
                session,
                CROSSREF_QUERY_URL,
                params,
                provider="Crossref",
            )
            items = (payload.get("message") or {}).get("items") or []
            page_matches = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                doi = normalize_doi(item.get("DOI"))
                if doi.startswith(prefixes):
                    scan_matches.add(doi)
                    cache[doi] = item
                    page_matches += 1

            save_cache(cache_path, cache)
            total_matches = sum(
                doi.startswith(prefixes) and bool(item) for doi, item in cache.items()
            )
            print(
                f"Crossref scan {scan}, page {page + 1}: "
                f"matched {page_matches} record(s), {total_matches} unique total"
            )
            if len(items) < page_size or (scan_matches and page_matches == 0):
                break
        else:
            raise RuntimeError(
                f"Crossref proceedings scan reached max_pages={max_pages} before an empty match page"
            )

        after_scan = sum(doi.startswith(prefixes) and bool(item) for doi, item in cache.items())
        if expected_count is not None and after_scan >= expected_count:
            break
        if expected_count is None and after_scan == before_scan:
            break

    return {
        doi: item
        for doi, item in cache.items()
        if doi.startswith(prefixes) and item
    }


def fetch_openalex_metadata(
    dois: list[str],
    cache: dict[str, dict[str, Any]],
    cache_path: Path,
    *,
    user_agent: str,
    batch_size: int = 50,
    mailto: str | None = None,
    sleep_seconds: float = 0.2,
) -> dict[str, dict[str, Any]]:
    normalized_dois = list(dict.fromkeys(normalize_doi(doi) for doi in dois if normalize_doi(doi)))
    missing = [doi for doi in normalized_dois if doi not in cache]
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})

    for index in range(0, len(missing), max(1, batch_size)):
        batch = missing[index : index + max(1, batch_size)]
        params = {
            "filter": "doi:" + "|".join(batch),
            "select": (
                "doi,title,abstract_inverted_index,keywords,locations,primary_location,"
                "open_access,ids,authorships"
            ),
            "per-page": str(len(batch)),
        }
        if mailto:
            params["mailto"] = mailto
        payload = _request_openalex(session, params)
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            doi = normalize_doi(item.get("doi"))
            if doi:
                cache[doi] = item
        for doi in batch:
            cache.setdefault(doi, {})
        save_cache(cache_path, cache)
        print(f"Fetched OpenAlex metadata for {min(index + len(batch), len(missing))}/{len(missing)} missing DOI(s)")
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return cache


def _request_openalex(session: requests.Session, params: dict[str, str]) -> dict[str, Any]:
    return _request_json(session, OPENALEX_WORKS_URL, params, provider="OpenAlex")


def _request_json(
    session: requests.Session,
    url: str,
    params: dict[str, str],
    *,
    provider: str,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            response = session.get(url, params=params, timeout=90)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 5:
                retry_after = response.headers.get("Retry-After")
                try:
                    retry_seconds = min(float(retry_after or 0), 60.0)
                except ValueError:
                    retry_seconds = 0
                time.sleep(max(retry_seconds, float(2**attempt)))
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError(f"{provider} returned a non-object JSON response")
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < 5:
                time.sleep(2**attempt)
    raise RuntimeError(f"{provider} request failed after retries: {last_error}") from last_error


def abstract_from_openalex(item: dict[str, Any]) -> str:
    inverted = item.get("abstract_inverted_index")
    if not isinstance(inverted, dict) or not inverted:
        return ""
    positioned_words: list[tuple[int, str]] = []
    for word, positions in inverted.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            continue
        positioned_words.extend(
            (position, word)
            for position in positions
            if isinstance(position, int) and position >= 0
        )
    return " ".join(word for _position, word in sorted(positioned_words)).strip()


def keywords_from_openalex(item: dict[str, Any]) -> list[str]:
    values = [
        clean_text(keyword.get("display_name"))
        for keyword in item.get("keywords") or []
        if isinstance(keyword, dict)
    ]
    return list(dict.fromkeys(value for value in values if value))[:10]


def authors_from_openalex(item: dict[str, Any]) -> list[str]:
    authors = []
    for authorship in item.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author") or {}
        name = clean_text(author.get("display_name")) if isinstance(author, dict) else ""
        if name:
            authors.append(name)
    return list(dict.fromkeys(authors))


def authors_from_crossref(item: dict[str, Any]) -> list[str]:
    authors = []
    for author in item.get("author") or []:
        if not isinstance(author, dict):
            continue
        name = clean_text(author.get("name"))
        if not name:
            name = clean_text(" ".join(filter(None, [author.get("given"), author.get("family")])))
        if name:
            authors.append(name)
    return list(dict.fromkeys(authors))


def pdf_from_openalex(item: dict[str, Any]) -> str:
    candidates = [item.get("primary_location"), *(item.get("locations") or [])]
    for location in candidates:
        if not isinstance(location, dict):
            continue
        pdf_url = clean_text(location.get("pdf_url"))
        if pdf_url.startswith(("https://", "http://")):
            return pdf_url
    return ""


def abstract_from_crossref(item: dict[str, Any]) -> str:
    value = clean_text(item.get("abstract"))
    if not value:
        return ""
    return plain_text_from_markup(value)


def keywords_from_crossref(item: dict[str, Any]) -> list[str]:
    values = [clean_text(subject) for subject in item.get("subject") or []]
    return list(dict.fromkeys(value for value in values if value))[:10]


def pdf_from_crossref(item: dict[str, Any]) -> str:
    links = [link for link in item.get("link") or [] if isinstance(link, dict)]
    links.sort(
        key=lambda link: (
            clean_text(link.get("content-type")).casefold() != "application/pdf",
            clean_text(link.get("intended-application")).casefold() != "text-mining",
        )
    )
    for link in links:
        url = clean_text(link.get("URL"))
        if url.startswith(("https://", "http://")):
            return url
    return ""


def build_record(
    paper: DblpPaper,
    openalex_item: dict[str, Any],
    *,
    venue: str,
    primary_area: str,
    source_label: str = "DBLP + OpenAlex",
) -> dict[str, Any]:
    return _assemble_record(
        paper,
        title=clean_text(openalex_item.get("title")) or paper.title,
        abstract=abstract_from_openalex(openalex_item),
        keywords=keywords_from_openalex(openalex_item),
        pdf=pdf_from_openalex(openalex_item),
        venue=venue,
        primary_area=primary_area,
        source_label=source_label,
    )


def build_crossref_record(
    paper: DblpPaper,
    crossref_item: dict[str, Any],
    *,
    venue: str,
    primary_area: str,
    source_label: str = "DBLP + Crossref",
) -> dict[str, Any]:
    crossref_titles = crossref_item.get("title") or []
    crossref_title = plain_text_from_markup(crossref_titles[0]) if crossref_titles else ""
    return _assemble_record(
        paper,
        title=crossref_title or paper.title,
        abstract=abstract_from_crossref(crossref_item),
        keywords=keywords_from_crossref(crossref_item),
        pdf=pdf_from_crossref(crossref_item),
        venue=venue,
        primary_area=primary_area,
        source_label=source_label,
    )


def build_crossref_openalex_record(
    paper: DblpPaper,
    crossref_item: dict[str, Any],
    openalex_item: dict[str, Any],
    *,
    venue: str,
    primary_area: str,
    source_label: str = "Crossref + OpenAlex",
) -> dict[str, Any]:
    crossref_titles = crossref_item.get("title") or []
    crossref_title = plain_text_from_markup(crossref_titles[0]) if crossref_titles else ""
    crossref_keywords = keywords_from_crossref(crossref_item)
    openalex_keywords = keywords_from_openalex(openalex_item)
    keywords = list(dict.fromkeys([*crossref_keywords, *openalex_keywords]))[:10]
    return _assemble_record(
        paper,
        title=crossref_title or clean_text(openalex_item.get("title")) or paper.title,
        abstract=abstract_from_crossref(crossref_item) or abstract_from_openalex(openalex_item),
        keywords=keywords,
        pdf=pdf_from_openalex(openalex_item) or pdf_from_crossref(crossref_item),
        venue=venue,
        primary_area=primary_area,
        source_label=source_label,
    )


def _assemble_record(
    paper: DblpPaper,
    *,
    title: str,
    abstract: str,
    keywords: list[str],
    pdf: str,
    venue: str,
    primary_area: str,
    source_label: str,
) -> dict[str, Any]:
    return {
        "id": paper.id,
        "forum": f"https://doi.org/{paper.doi}",
        "domain": venue,
        "content": {
            "title": {"value": title},
            "authors": {"value": paper.authors},
            "keywords": {"value": keywords},
            "abstract": {"value": abstract},
            "primary_area": {"value": primary_area},
            "venue": {"value": venue},
            "pdf": {"value": pdf},
            "doi": {"value": paper.doi},
            "pages": {"value": paper.pages or ""},
            "section": {"value": paper.section or ""},
            "source": {"value": source_label},
            "dblp_key": {"value": paper.dblp_key or ""},
        },
    }


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
