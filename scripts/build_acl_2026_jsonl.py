#!/usr/bin/env python3
"""Build import-ready ACL 2026 JSONL from ACL Anthology metadata."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import requests


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFERENCE_VENUE = "ACL 2026 Long"
PRIMARY_AREA = "Natural Language Processing"
USER_AGENT = "paper-online/0.1 (ACL 2026 metadata importer)"
DEFAULT_ANTHOLOGY_PREFIX = "2026.acl-long"
EXPECTED_COUNTS = {
    (2026, "long"): 2222,
    (2025, "long"): 1602,
    (2025, "short"): 97,
}


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return clean_text("".join(self.parts))


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def strip_html(fragment: str) -> str:
    parser = TextExtractor()
    parser.feed(fragment)
    return parser.text()


def fetch_text(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    response.raise_for_status()
    return response.text


def extract_acl_long_section(
    html_text: str,
    anthology_prefix: str = DEFAULT_ANTHOLOGY_PREFIX,
) -> str:
    section_id = anthology_prefix.replace(".", "")
    start = html_text.find(f"<div id={section_id}>")
    if start < 0:
        raise ValueError(f"ACL section {anthology_prefix} not found")
    end = len(html_text)
    for match in re.finditer(r"<div id=[\"']?([^\"' >]+)", html_text[start + 1 :]):
        candidate_id = match.group(1)
        if candidate_id != section_id and re.match(r"^\d{4}", candidate_id):
            end = start + 1 + match.start()
            break
    return html_text[start:end]


def split_bibtex_entries(bib_text: str) -> list[str]:
    return ["@" + entry for entry in re.split(r"(?m)^@", bib_text) if entry.strip()]


def extract_bibtex_field(entry: str, field: str) -> str:
    match = re.search(rf"(?ms)^\s*{re.escape(field)}\s*=\s*([\"{{])", entry)
    if not match:
        return ""

    opener = match.group(1)
    index = match.end()
    if opener == '"':
        chars: list[str] = []
        escaped = False
        while index < len(entry):
            char = entry[index]
            if char == '"' and not escaped:
                return "".join(chars)
            chars.append(char)
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            index += 1
        return "".join(chars)

    depth = 1
    chars = []
    while index < len(entry):
        char = entry[index]
        if char == "{":
            depth += 1
            chars.append(char)
        elif char == "}":
            depth -= 1
            if depth == 0:
                return "".join(chars)
            chars.append(char)
        else:
            chars.append(char)
        index += 1
    return "".join(chars)


def parse_acl_long_bibtex(
    bib_text: str,
    anthology_prefix: str = DEFAULT_ANTHOLOGY_PREFIX,
) -> dict[int, dict[str, str]]:
    papers: dict[int, dict[str, str]] = {}
    for entry in split_bibtex_entries(bib_text):
        if not entry.startswith("@inproceedings"):
            continue

        url = extract_bibtex_field(entry, "url")
        number_match = re.search(rf"/{re.escape(anthology_prefix)}\.(\d+)/", url)
        if not number_match:
            continue

        key_match = re.match(r"@inproceedings\{([^,]+),", entry)
        number = int(number_match.group(1))
        papers[number] = {
            "bibtex_key": key_match.group(1) if key_match else "",
            "url": url,
            "doi": extract_bibtex_field(entry, "doi"),
            "pages": extract_bibtex_field(entry, "pages"),
        }
    return papers


def _record_fragment(
    section: str,
    number: int,
    next_number: int | None,
    anthology_prefix: str = DEFAULT_ANTHOLOGY_PREFIX,
) -> str:
    marker = f"https://aclanthology.org/{anthology_prefix}.{number}.pdf"
    start = section.find(marker)
    if start < 0:
        raise ValueError(f"ACL paper {number} PDF marker not found")

    if next_number is None:
        end = len(section)
    else:
        next_marker = f"https://aclanthology.org/{anthology_prefix}.{next_number}.pdf"
        end = section.find(next_marker, start + len(marker))
        if end < 0:
            end = len(section)
    return section[start:end]


def parse_acl_long_html(
    section: str,
    numbers: list[int],
    anthology_prefix: str = DEFAULT_ANTHOLOGY_PREFIX,
) -> dict[int, dict[str, Any]]:
    parsed: dict[int, dict[str, Any]] = {}
    for index, number in enumerate(numbers):
        next_number = numbers[index + 1] if index + 1 < len(numbers) else None
        fragment = _record_fragment(section, number, next_number, anthology_prefix)

        title_match = re.search(
            rf"<strong><a[^>]+href=/{re.escape(anthology_prefix)}\.{number}/>(.*?)</a></strong>",
            fragment,
            flags=re.DOTALL,
        )
        if not title_match:
            raise ValueError(f"ACL paper {number} title not found")

        author_match = re.search(
            rf"</strong><br>(.*?)</span>\s*</div>",
            fragment,
            flags=re.DOTALL,
        )
        author_text = strip_html(author_match.group(1)) if author_match else ""
        authors = [clean_text(author) for author in author_text.split("|") if clean_text(author)]

        abstract = ""
        abstract_id = f"abstract-{anthology_prefix.replace('.', '--')}--{number}"
        abstract_match = re.search(
            rf"id=[\"']?{re.escape(abstract_id)}[\"']?[^>]*>.*?"
            r"<div class=\"card-body p-3 small\">(.*?)</div>\s*</div>",
            fragment,
            flags=re.DOTALL,
        )
        if abstract_match:
            abstract = strip_html(abstract_match.group(1))

        parsed[number] = {
            "title": strip_html(title_match.group(1)),
            "authors": authors,
            "abstract": abstract,
            "pdf": f"https://aclanthology.org/{anthology_prefix}.{number}.pdf",
        }
    return parsed


def build_acl_long_rows(
    event_html: str,
    bib_text: str,
    *,
    venue: str = CONFERENCE_VENUE,
    primary_area: str = PRIMARY_AREA,
    anthology_prefix: str = DEFAULT_ANTHOLOGY_PREFIX,
) -> list[dict[str, Any]]:
    bib_papers = parse_acl_long_bibtex(bib_text, anthology_prefix)
    numbers = sorted(bib_papers)
    section = extract_acl_long_section(event_html, anthology_prefix)
    html_papers = parse_acl_long_html(section, numbers, anthology_prefix)

    rows: list[dict[str, Any]] = []
    for sort_order, number in enumerate(numbers, start=1):
        paper_id = f"{anthology_prefix}.{number}"
        html_paper = html_papers[number]
        bib_paper = bib_papers[number]
        rows.append(
            {
                "id": paper_id,
                "content": {
                    "title": {"value": html_paper["title"]},
                    "abstract": {"value": html_paper["abstract"]},
                    "authors": {"value": html_paper["authors"]},
                    "keywords": {"value": []},
                    "pdf": {"value": html_paper["pdf"]},
                    "venue": {"value": venue},
                    "primary_area": {"value": primary_area},
                    "sort_order": {"value": sort_order},
                },
                "acl": {
                    "anthology_id": paper_id,
                    "url": bib_paper.get("url") or f"https://aclanthology.org/{paper_id}/",
                    "doi": bib_paper.get("doi"),
                    "pages": bib_paper.get("pages"),
                    "bibtex_key": bib_paper.get("bibtex_key"),
                },
            }
        )
    return rows


def write_jsonl(rows: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build import-ready ACL main-conference JSONL")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--track", choices=("long", "short"), default="long")
    parser.add_argument("--event-url")
    parser.add_argument("--bib-url")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--venue")
    parser.add_argument("--primary-area", default=PRIMARY_AREA)
    parser.add_argument("--expected-count", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    anthology_prefix = f"{args.year}.acl-{args.track}"
    event_url = args.event_url or f"https://aclanthology.org/events/acl-{args.year}/"
    bib_url = args.bib_url or f"https://aclanthology.org/volumes/{anthology_prefix}.bib"
    output = args.output or REPO_ROOT / "crawled_data" / f"acl_{args.year}" / f"{args.track}_papers.jsonl"
    venue = args.venue or f"ACL {args.year} {args.track.title()}"
    expected_count = args.expected_count
    if expected_count is None:
        expected_count = EXPECTED_COUNTS.get((args.year, args.track))
    event_html = fetch_text(event_url)
    bib_text = fetch_text(bib_url)
    rows = build_acl_long_rows(
        event_html,
        bib_text,
        venue=venue,
        primary_area=args.primary_area,
        anthology_prefix=anthology_prefix,
    )

    if expected_count is not None and len(rows) != expected_count:
        print(
            f"Error: expected {expected_count} {venue} papers, got {len(rows)}",
            file=sys.stderr,
        )
        return 1

    missing_abstracts = [row["id"] for row in rows if not row["content"]["abstract"]["value"]]
    write_jsonl(rows, output)
    print(f"Wrote {len(rows)} {venue} papers to {output}")
    if missing_abstracts:
        print(f"Papers without ACL Anthology abstract: {', '.join(missing_abstracts[:20])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
