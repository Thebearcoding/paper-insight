#!/usr/bin/env python3
"""Build import-ready IJCAI 2025 JSONL from the official proceedings site."""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from dblp_openalex import (  # noqa: E402
    clean_text,
    load_cache,
    normalize_doi,
    paper_id_from_doi,
    save_cache,
    write_jsonl,
)


CONFERENCE_ID = "ijcai_2025"
CONFERENCE_VENUE = "IJCAI 2025"
IJCAI_BASE_URL = "https://www.ijcai.org"
DEFAULT_LIST_URL = f"{IJCAI_BASE_URL}/proceedings/2025/"
DEFAULT_OUTPUT = REPO_ROOT / "crawled_data" / CONFERENCE_ID / "main_papers.jsonl"
DEFAULT_CACHE = REPO_ROOT / "crawled_data" / CONFERENCE_ID / "ijcai_cache.json"
USER_AGENT = "paper-online/0.1 (IJCAI 2025 official proceedings importer)"


@dataclass(frozen=True)
class IjcaiPaperLink:
    order: int
    detail_url: str
    section: str | None
    subsection: str | None


class IjcaiListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[IjcaiPaperLink] = []
        self._seen: set[str] = set()
        self._section: str | None = None
        self._subsection: str | None = None
        self._capture: str | None = None
        self._capture_tag: str | None = None
        self._capture_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        classes = set((attrs_dict.get("class") or "").split())
        if tag == "h3":
            self._begin_capture("section", tag)
        elif tag == "div" and "subsection_title" in classes:
            self._begin_capture("subsection", tag)

        if tag != "a":
            return
        href = attrs_dict.get("href") or ""
        absolute_url = urljoin(IJCAI_BASE_URL, href)
        if not absolute_url.startswith(f"{IJCAI_BASE_URL}/proceedings/2025/"):
            return
        paper_number = absolute_url.removeprefix(f"{IJCAI_BASE_URL}/proceedings/2025/")
        if not paper_number.isdigit() or absolute_url in self._seen:
            return
        self._seen.add(absolute_url)
        self.links.append(
            IjcaiPaperLink(
                order=len(self.links) + 1,
                detail_url=absolute_url,
                section=self._section,
                subsection=self._subsection,
            )
        )

    def handle_endtag(self, tag: str) -> None:
        if self._capture and tag == self._capture_tag:
            value = clean_text(" ".join(self._capture_parts)) or None
            if self._capture == "section":
                self._section = value
                self._subsection = None
            else:
                self._subsection = value
            self._capture = None
            self._capture_tag = None
            self._capture_parts = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._capture_parts.append(data)

    def _begin_capture(self, capture: str, tag: str) -> None:
        self._capture = capture
        self._capture_tag = tag
        self._capture_parts = []


class IjcaiDetailParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.abstract_parts: list[str] = []
        self.keywords: list[str] = []
        self._after_rule = False
        self._abstract_depth = 0
        self._topic_depth = 0
        self._topic_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "meta":
            name = attrs_dict.get("name")
            content = attrs_dict.get("content")
            if name and content:
                self.meta.setdefault(name, []).append(clean_text(content))
            return
        if tag == "hr":
            self._after_rule = True
            return
        if tag != "div":
            return

        classes = set((attrs_dict.get("class") or "").split())
        if self._topic_depth:
            self._topic_depth += 1
            return
        if "topic" in classes:
            self._topic_depth = 1
            self._topic_parts = []
            return
        if self._abstract_depth:
            self._abstract_depth += 1
            return
        if self._after_rule and "col-md-12" in classes:
            self._abstract_depth = 1
            self._after_rule = False

    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self._topic_depth:
            self._topic_depth -= 1
            if self._topic_depth == 0:
                keyword = clean_text(" ".join(self._topic_parts))
                if keyword and keyword not in self.keywords:
                    self.keywords.append(keyword)
                self._topic_parts = []
            return
        if self._abstract_depth:
            self._abstract_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._topic_depth:
            self._topic_parts.append(data)
        elif self._abstract_depth:
            self.abstract_parts.append(data)


def parse_ijcai_list(html_text: str) -> list[IjcaiPaperLink]:
    parser = IjcaiListParser()
    parser.feed(html_text)
    return parser.links


def _first_meta(parser: IjcaiDetailParser, name: str) -> str:
    values = parser.meta.get(name) or []
    return clean_text(values[0]) if values else ""


def parse_ijcai_detail(link: IjcaiPaperLink, html_text: str) -> dict[str, Any]:
    parser = IjcaiDetailParser()
    parser.feed(html_text)
    doi = normalize_doi(_first_meta(parser, "citation_doi"))
    first_page = _first_meta(parser, "citation_firstpage")
    last_page = _first_meta(parser, "citation_lastpage")
    pages = f"{first_page}-{last_page}" if first_page and last_page else first_page
    return {
        "order": link.order,
        "detail_url": link.detail_url,
        "id": paper_id_from_doi(CONFERENCE_ID, doi) if doi else "",
        "doi": doi,
        "title": _first_meta(parser, "citation_title"),
        "authors": [
            clean_text(author)
            for author in parser.meta.get("citation_author") or []
            if clean_text(author)
        ],
        "abstract": clean_text(" ".join(parser.abstract_parts)),
        "keywords": parser.keywords,
        "pdf": _first_meta(parser, "citation_pdf_url"),
        "pages": pages,
        "section": link.section or "",
        "subsection": link.subsection or "",
    }


def fetch_text(session: requests.Session, url: str) -> str:
    last_error: Exception | None = None
    for attempt in range(1, 5):
        try:
            response = session.get(url, timeout=(10, 45))
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 4:
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(2**attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}") from last_error


def fetch_detail_records(
    links: list[IjcaiPaperLink],
    cache: dict[str, dict[str, Any]],
    cache_path: Path,
    *,
    workers: int = 8,
    batch_size: int = 100,
) -> dict[str, dict[str, Any]]:
    missing = [link for link in links if link.detail_url not in cache]
    if not missing:
        return cache

    def fetch_one(link: IjcaiPaperLink) -> tuple[str, dict[str, Any]]:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        return link.detail_url, parse_ijcai_detail(link, fetch_text(session, link.detail_url))

    errors: list[str] = []
    completed = 0
    for start in range(0, len(missing), max(1, batch_size)):
        chunk = missing[start : start + max(1, batch_size)]
        executor = ThreadPoolExecutor(max_workers=max(1, workers))
        futures = {executor.submit(fetch_one, link): link for link in chunk}
        try:
            try:
                for future in as_completed(futures, timeout=240):
                    link = futures[future]
                    try:
                        url, detail = future.result()
                    except Exception as exc:
                        errors.append(f"{link.detail_url}: {exc}")
                        continue
                    cache[url] = detail
                    completed += 1
            except FuturesTimeoutError:
                for future, link in futures.items():
                    if not future.done():
                        future.cancel()
                        errors.append(f"{link.detail_url}: timed out")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        save_cache(cache_path, cache)
        print(f"Fetched IJCAI details for {completed}/{len(missing)} missing paper(s)", flush=True)

    if errors:
        raise RuntimeError(
            f"Failed to fetch {len(errors)} IJCAI detail page(s):\n" + "\n".join(errors[:10])
        )
    return cache


def build_jsonl_record(detail: dict[str, Any]) -> dict[str, Any]:
    required = ("id", "doi", "title", "authors", "pdf")
    missing = [field for field in required if not detail.get(field)]
    if missing:
        raise ValueError(f"IJCAI detail is missing {', '.join(missing)}: {detail.get('detail_url')}")
    primary_area = detail.get("subsection") or detail.get("section") or "Artificial Intelligence"
    return {
        "id": detail["id"],
        "forum": detail["detail_url"],
        "domain": CONFERENCE_VENUE,
        "content": {
            "title": {"value": detail["title"]},
            "authors": {"value": detail["authors"]},
            "keywords": {"value": detail.get("keywords") or []},
            "abstract": {"value": detail.get("abstract") or ""},
            "primary_area": {"value": primary_area},
            "venue": {"value": CONFERENCE_VENUE},
            "pdf": {"value": detail["pdf"]},
            "doi": {"value": detail["doi"]},
            "pages": {"value": detail.get("pages") or ""},
            "section": {"value": detail.get("section") or ""},
            "subsection": {"value": detail.get("subsection") or ""},
            "source": {"value": "IJCAI official proceedings"},
            "sort_order": {"value": detail["order"]},
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-url", default=DEFAULT_LIST_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--skip-details", action="store_true")
    parser.add_argument("--expected-count", type=int, default=1280)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    links = parse_ijcai_list(fetch_text(session, args.list_url))
    if len(links) != args.expected_count:
        print(f"Error: expected {args.expected_count} IJCAI papers, got {len(links)}", file=sys.stderr)
        return 1

    cache = load_cache(args.cache)
    if not args.skip_details:
        cache = fetch_detail_records(links, cache, args.cache, workers=args.workers)
    missing_urls = [link.detail_url for link in links if link.detail_url not in cache]
    if missing_urls:
        print(f"Error: cache is missing {len(missing_urls)} IJCAI detail page(s)", file=sys.stderr)
        return 1

    records = [build_jsonl_record(cache[link.detail_url]) for link in links]
    write_jsonl(args.output, records)
    with_abstract = sum(bool(record["content"]["abstract"]["value"]) for record in records)
    with_keywords = sum(bool(record["content"]["keywords"]["value"]) for record in records)
    print(f"Wrote {len(records)} IJCAI 2025 papers to {args.output}")
    print(f"Abstracts: {with_abstract}/{len(records)}")
    print(f"Keywords: {with_keywords}/{len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
