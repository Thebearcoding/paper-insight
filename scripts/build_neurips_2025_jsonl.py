#!/usr/bin/env python3
"""Build import-ready NeurIPS 2025 JSONL from the official proceedings."""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests


REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://proceedings.neurips.cc"
INDEX_URL = f"{BASE_URL}/paper/2025"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "crawled_data" / "neurips_2025" / "proceedings_papers.jsonl"
DEFAULT_CACHE_PATH = REPO_ROOT / "crawled_data" / "neurips_2025" / "abstract_cache.json"
USER_AGENT = "paper-online/0.1 (NeurIPS 2025 metadata importer)"
DETAIL_PATTERN = re.compile(r"/hash/([0-9a-f]+)-Abstract-([^.]+)\.html$")


class IndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self.current: dict[str, str] | None = None
        self.capture: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "li" and values.get("data-track"):
            self.current = {"track": values["data-track"] or "conference", "title": "", "authors": "", "href": ""}
        elif self.current and tag == "a" and values.get("title") == "paper title":
            self.current["href"] = values.get("href") or ""
            self.capture = "title"
        elif self.current and tag == "span" and "paper-authors" in classes:
            self.capture = "authors"

    def handle_endtag(self, tag: str) -> None:
        if tag in {"a", "span"}:
            self.capture = None
        elif tag == "li" and self.current:
            if self.current["title"] and self.current["href"]:
                self.rows.append(self.current)
            self.current = None
            self.capture = None

    def handle_data(self, data: str) -> None:
        if self.current and self.capture:
            self.current[self.capture] += data


class AbstractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set((dict(attrs).get("class") or "").split())
        if tag == "p" and "paper-abstract" in classes:
            self.depth = 1
        elif self.depth:
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


_thread_local = threading.local()


def session() -> requests.Session:
    current = getattr(_thread_local, "session", None)
    if current is None:
        current = requests.Session()
        current.headers.update({"User-Agent": USER_AGENT})
        _thread_local.session = current
    return current


def fetch_text(url: str, timeout: int = 60) -> str:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    return response.text


def fetch_abstract(href: str) -> tuple[str, str]:
    url = urljoin(BASE_URL, href)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = session().get(url, timeout=45)
            response.raise_for_status()
            parser = AbstractParser()
            parser.feed(response.text)
            return href, parser.text()
        except Exception as exc:
            last_error = exc
            time.sleep(attempt + 1)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def load_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def save_cache(path: Path, cache: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(path)


def venue_for_track(track: str) -> tuple[str, str]:
    if track == "datasets_and_benchmarks_track":
        return "NeurIPS 2025 Datasets and Benchmarks", "Datasets and Benchmarks"
    if track == "position_paper_track":
        return "NeurIPS 2025 Position Paper", "Position Paper"
    return "NeurIPS 2025", "Machine Learning"


def build_rows(index_html: str, abstracts: dict[str, str]) -> list[dict[str, Any]]:
    parser = IndexParser()
    parser.feed(index_html)
    rows: list[dict[str, Any]] = []
    for item in parser.rows:
        match = DETAIL_PATTERN.search(item["href"])
        if not match:
            continue
        paper_id, suffix = match.groups()
        venue, primary_area = venue_for_track(item["track"])
        pdf_url = f"{BASE_URL}/paper_files/paper/2025/file/{paper_id}-Paper-{suffix}.pdf"
        authors = [value.strip() for value in item["authors"].split(",") if value.strip()]
        rows.append(
            {
                "id": paper_id,
                "content": {
                    "title": {"value": re.sub(r"\s+", " ", item["title"]).strip()},
                    "authors": {"value": authors},
                    "abstract": {"value": abstracts.get(item["href"], "")},
                    "keywords": {"value": []},
                    "pdf": {"value": pdf_url},
                    "venue": {"value": venue},
                    "primary_area": {"value": primary_area},
                    "sort_order": {"value": len(rows) + 1},
                },
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Build NeurIPS 2025 JSONL from official proceedings")
    parser.add_argument("--index-url", default=INDEX_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--skip-abstracts", action="store_true")
    args = parser.parse_args()

    index_html = fetch_text(args.index_url, timeout=120)
    index_parser = IndexParser()
    index_parser.feed(index_html)
    if len(index_parser.rows) < 5000:
        raise ValueError(f"Unexpected NeurIPS paper count: {len(index_parser.rows)}")

    cache = load_cache(args.cache)
    if not args.skip_abstracts:
        pending = [row["href"] for row in index_parser.rows if row["href"] not in cache]
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=max(args.workers, 1)) as executor:
            futures = {executor.submit(fetch_abstract, href): href for href in pending}
            for completed, future in enumerate(as_completed(futures), 1):
                href = futures[future]
                try:
                    fetched_href, abstract = future.result()
                    cache[fetched_href] = abstract
                except Exception as exc:
                    failures.append(f"{href}: {exc}")
                if completed % 100 == 0:
                    save_cache(args.cache, cache)
                    print(f"Fetched abstracts for {completed}/{len(pending)} pending paper(s)", flush=True)
        save_cache(args.cache, cache)
        if failures:
            print(f"Abstract fetch failures: {len(failures)}")
            for failure in failures[:10]:
                print(f"  {failure}")

    rows = build_rows(index_html, cache)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    missing_abstracts = sum(not row["content"]["abstract"]["value"] for row in rows)
    print(f"Wrote {len(rows)} NeurIPS 2025 papers to {args.output}")
    print(f"Papers without abstract: {missing_abstracts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
