#!/usr/bin/env python3
"""Build import-ready ICML 2025 JSONL from the official PMLR bibliography."""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

import requests

from build_acl_2026_jsonl import extract_bibtex_field, split_bibtex_entries, strip_html


REPO_ROOT = Path(__file__).resolve().parent.parent
BIB_URL = "https://proceedings.mlr.press/v267/assets/bib/bibliography.bib"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "crawled_data" / "icml_2025" / "pmlr_papers.jsonl"
USER_AGENT = "paper-online/0.1 (ICML 2025 metadata importer)"


def clean_tex(value: str) -> str:
    cleaned = html.unescape(value or "").replace("~", " ")
    cleaned = re.sub(r"\\['\"`^~=.uvHckbdtr]\{?([A-Za-z])\}?", r"\1", cleaned)
    for _ in range(3):
        cleaned = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1", cleaned)
    cleaned = re.sub(r"\\([{}_%&#$])", r"\1", cleaned)
    cleaned = cleaned.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_authors(value: str) -> list[str]:
    authors: list[str] = []
    for raw_author in re.split(r"\s+and\s+", value.strip()):
        author = clean_tex(raw_author)
        if not author:
            continue
        if "," in author:
            last, first = (part.strip() for part in author.split(",", 1))
            author = f"{first} {last}".strip()
        authors.append(author)
    return authors


def build_rows(bib_text: str) -> list[dict]:
    rows: list[dict] = []
    for entry in split_bibtex_entries(bib_text):
        if not entry.casefold().startswith("@inproceedings"):
            continue
        key_match = re.match(r"@inproceedings\{([^,]+),", entry, flags=re.IGNORECASE)
        if not key_match:
            continue
        paper_id = key_match.group(1).strip()
        title = clean_tex(extract_bibtex_field(entry, "title"))
        if not title:
            continue
        abstract = clean_tex(strip_html(extract_bibtex_field(entry, "abstract")))
        rows.append(
            {
                "id": paper_id,
                "content": {
                    "title": {"value": title},
                    "authors": {"value": parse_authors(extract_bibtex_field(entry, "author"))},
                    "abstract": {"value": abstract},
                    "keywords": {"value": []},
                    "pdf": {"value": extract_bibtex_field(entry, "pdf").strip() or None},
                    "venue": {"value": "ICML 2025"},
                    "primary_area": {"value": "Machine Learning"},
                    "sort_order": {"value": len(rows) + 1},
                },
                "pmlr": {"url": extract_bibtex_field(entry, "url").strip()},
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ICML 2025 JSONL from PMLR")
    parser.add_argument("--bib-url", default=BIB_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    response = requests.get(args.bib_url, headers={"User-Agent": USER_AGENT}, timeout=120)
    response.raise_for_status()
    rows = build_rows(response.text)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    missing_abstracts = sum(not row["content"]["abstract"]["value"] for row in rows)
    print(f"Wrote {len(rows)} ICML 2025 papers to {args.output}")
    print(f"Papers without abstract: {missing_abstracts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
