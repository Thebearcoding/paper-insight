#!/usr/bin/env python3
"""Build import-ready KDD/SIGIR 2026 JSONL from formal ACM proceedings."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from dblp_openalex import (  # noqa: E402
    DblpPaper,
    authors_from_crossref,
    authors_from_openalex,
    build_crossref_openalex_record,
    clean_text,
    fetch_crossref_proceedings_metadata,
    fetch_openalex_metadata,
    load_cache,
    normalize_doi,
    paper_id_from_doi,
    write_jsonl,
)


USER_AGENT = "paper-online/0.1 (ACM 2026 proceedings importer)"


@dataclass(frozen=True)
class ConferenceConfig:
    id: str
    venue: str
    primary_area: str
    container_title: str
    doi_sections: tuple[tuple[str, str], ...]
    expected_count: int


CONFERENCES = {
    "kdd_2026": ConferenceConfig(
        id="kdd_2026",
        venue="KDD 2026",
        primary_area="Knowledge Discovery and Data Mining",
        container_title=(
            "Proceedings of the 32nd ACM SIGKDD Conference on Knowledge "
            "Discovery and Data Mining"
        ),
        doi_sections=(
            ("10.1145/3770854", "Volume 1"),
            ("10.1145/3770855", "Volume 2"),
        ),
        expected_count=1472,
    ),
    "sigir_2026": ConferenceConfig(
        id="sigir_2026",
        venue="SIGIR 2026",
        primary_area="Information Retrieval",
        container_title=(
            "Proceedings of the 49th International ACM SIGIR Conference on "
            "Research and Development in Information Retrieval"
        ),
        doi_sections=(("10.1145/3805712", "Main Proceedings"),),
        expected_count=686,
    ),
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conference", required=True, choices=sorted(CONFERENCES))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--crossref-cache", type=Path)
    parser.add_argument("--openalex-cache", type=Path)
    parser.add_argument("--mailto", help="Optional email for provider polite pools")
    parser.add_argument("--skip-crossref", action="store_true")
    parser.add_argument("--skip-openalex", action="store_true")
    parser.add_argument("--expected-count", type=int)
    return parser.parse_args(argv)


def _first_page(value: object) -> int:
    match = re.search(r"\d+", clean_text(value))
    return int(match.group()) if match else 10**12


def _section_for_doi(config: ConferenceConfig, doi: str) -> tuple[int, str]:
    normalized = normalize_doi(doi)
    for index, (prefix, section) in enumerate(config.doi_sections):
        if normalized.startswith(normalize_doi(prefix).rstrip(".") + "."):
            return index, section
    raise ValueError(f"DOI is outside configured proceedings: {doi}")


def crossref_item_to_paper(
    config: ConferenceConfig,
    item: dict[str, Any],
    openalex_item: dict[str, Any] | None = None,
) -> DblpPaper:
    doi = normalize_doi(item.get("DOI"))
    titles = item.get("title") or []
    title = clean_text(titles[0]) if titles else ""
    authors = authors_from_crossref(item) or authors_from_openalex(openalex_item or {})
    if not doi or not title:
        raise ValueError(f"Incomplete formal proceedings metadata for DOI {doi or '<missing>'}")
    _, section = _section_for_doi(config, doi)
    return DblpPaper(
        id=paper_id_from_doi(config.id, doi),
        doi=doi,
        title=title,
        authors=authors,
        pages=clean_text(item.get("page")) or None,
        dblp_key=None,
        section=section,
    )


def sorted_crossref_items(
    config: ConferenceConfig,
    metadata: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    items = list(metadata.values())
    return sorted(
        items,
        key=lambda item: (
            _section_for_doi(config, normalize_doi(item.get("DOI")))[0],
            _first_page(item.get("page")),
            clean_text((item.get("title") or [""])[0]).casefold(),
        ),
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = CONFERENCES[args.conference]
    data_dir = REPO_ROOT / "crawled_data" / config.id
    output = args.output or data_dir / "main_papers.jsonl"
    crossref_cache_path = args.crossref_cache or data_dir / "crossref_cache.json"
    openalex_cache_path = args.openalex_cache or data_dir / "openalex_cache.json"
    expected_count = args.expected_count if args.expected_count is not None else config.expected_count

    crossref_cache = load_cache(crossref_cache_path)
    prefixes = tuple(prefix for prefix, _section in config.doi_sections)
    if not args.skip_crossref:
        metadata = fetch_crossref_proceedings_metadata(
            crossref_cache,
            crossref_cache_path,
            container_title=config.container_title,
            doi_prefixes=prefixes,
            from_date="2026-01-01",
            until_date="2026-12-31",
            user_agent=USER_AGENT,
            mailto=args.mailto,
            expected_count=expected_count,
        )
    else:
        normalized_prefixes = tuple(normalize_doi(prefix).rstrip(".") + "." for prefix in prefixes)
        metadata = {
            doi: item
            for doi, item in crossref_cache.items()
            if doi.startswith(normalized_prefixes) and item
        }

    if len(metadata) != expected_count:
        print(
            f"Error: expected {expected_count} {config.venue} papers, got {len(metadata)}",
            file=sys.stderr,
        )
        return 1

    openalex_cache = load_cache(openalex_cache_path)
    if not args.skip_openalex:
        openalex_cache = fetch_openalex_metadata(
            list(metadata),
            openalex_cache,
            openalex_cache_path,
            user_agent=USER_AGENT,
            mailto=args.mailto,
        )

    records = []
    for order, item in enumerate(sorted_crossref_items(config, metadata), start=1):
        doi = normalize_doi(item.get("DOI"))
        paper = crossref_item_to_paper(config, item, openalex_cache.get(doi))
        record = build_crossref_openalex_record(
            paper,
            item,
            openalex_cache.get(doi, {}),
            venue=config.venue,
            primary_area=config.primary_area,
            source_label="ACM/Crossref + OpenAlex",
        )
        record["content"]["sort_order"] = {"value": order}
        records.append(record)

    write_jsonl(output, records)
    with_abstract = sum(bool(record["content"]["abstract"]["value"]) for record in records)
    with_pdf = sum(bool(record["content"]["pdf"]["value"]) for record in records)
    with_authors = sum(bool(record["content"]["authors"]["value"]) for record in records)
    print(f"Wrote {len(records)} {config.venue} papers to {output}")
    print(f"Authors: {with_authors}/{len(records)}")
    print(f"Abstracts: {with_abstract}/{len(records)}")
    print(f"PDF URLs: {with_pdf}/{len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
