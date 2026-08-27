#!/usr/bin/env python3
"""Build import-ready AAAI proceedings JSONL from DBLP and Crossref."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from dblp_openalex import (  # noqa: E402
    build_crossref_record,
    fetch_crossref_journal_metadata,
    load_cache,
    load_text,
    parse_dblp_proceedings,
    write_jsonl,
)


PRIMARY_AREA = "Artificial Intelligence"
USER_AGENT = "paper-online/0.1 (AAAI proceedings importer)"
AAAI_ISSN = "2374-3468"


@dataclass(frozen=True)
class ConferenceConfig:
    id: str
    venue: str
    dblp_url: str
    doi_prefix: str
    year: int
    expected_count: int


CONFERENCES = {
    "aaai_2026": ConferenceConfig(
        id="aaai_2026",
        venue="AAAI 2026",
        dblp_url="https://dblp.org/db/conf/aaai/aaai2026.xml",
        doi_prefix="10.1609/aaai.v40",
        year=2026,
        expected_count=4920,
    ),
    "aaai_2025": ConferenceConfig(
        id="aaai_2025",
        venue="AAAI 2025",
        dblp_url="https://dblp.org/db/conf/aaai/aaai2025.xml",
        doi_prefix="10.1609/aaai.v39",
        year=2025,
        expected_count=3486,
    ),
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build AAAI JSONL from DBLP + Crossref")
    parser.add_argument("--conference", choices=sorted(CONFERENCES), default="aaai_2026")
    parser.add_argument("--dblp-source", help="Override the DBLP XML URL or local file")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--crossref-cache", type=Path)
    parser.add_argument("--mailto", help="Optional email for the Crossref polite pool")
    parser.add_argument("--skip-crossref", action="store_true")
    parser.add_argument("--expected-count", type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = CONFERENCES[args.conference]
    data_dir = REPO_ROOT / "crawled_data" / config.id
    dblp_source = args.dblp_source or config.dblp_url
    output = args.output or data_dir / "main_papers.jsonl"
    cache_path = args.crossref_cache or data_dir / "crossref_cache.json"
    expected_count = args.expected_count if args.expected_count is not None else config.expected_count
    papers = parse_dblp_proceedings(
        load_text(dblp_source, user_agent=USER_AGENT),
        conference_id=config.id,
        doi_prefix=config.doi_prefix,
    )
    if expected_count is not None and len(papers) != expected_count:
        print(f"Error: expected {expected_count} {config.venue} papers, got {len(papers)}", file=sys.stderr)
        return 1

    cache = load_cache(cache_path)
    if not args.skip_crossref:
        cache = fetch_crossref_journal_metadata(
            [paper.doi for paper in papers],
            cache,
            cache_path,
            issn=AAAI_ISSN,
            from_date=f"{config.year}-01-01",
            until_date=f"{config.year}-12-31",
            user_agent=USER_AGENT,
            mailto=args.mailto,
        )
    records = [
        build_crossref_record(
            paper,
            cache.get(paper.doi, {}),
            venue=config.venue,
            primary_area=PRIMARY_AREA,
        )
        for paper in papers
    ]
    write_jsonl(output, records)

    with_abstract = sum(bool(record["content"]["abstract"]["value"]) for record in records)
    with_pdf = sum(bool(record["content"]["pdf"]["value"]) for record in records)
    print(f"Wrote {len(records)} {config.venue} papers to {output}")
    print(f"Abstracts: {with_abstract}/{len(records)}")
    print(f"PDF URLs: {with_pdf}/{len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
