#!/usr/bin/env python3
"""Build import-ready AAAI 2026 JSONL from DBLP and Crossref."""

from __future__ import annotations

import argparse
import sys
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


CONFERENCE_ID = "aaai_2026"
CONFERENCE_VENUE = "AAAI 2026"
PRIMARY_AREA = "Artificial Intelligence"
DBLP_URL = "https://dblp.org/db/conf/aaai/aaai2026.xml"
DOI_PREFIX = "10.1609/aaai.v40"
DEFAULT_OUTPUT = REPO_ROOT / "crawled_data" / CONFERENCE_ID / "main_papers.jsonl"
DEFAULT_CACHE = REPO_ROOT / "crawled_data" / CONFERENCE_ID / "crossref_cache.json"
USER_AGENT = "paper-online/0.1 (AAAI 2026 proceedings importer)"
AAAI_ISSN = "2374-3468"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build AAAI 2026 JSONL from DBLP + OpenAlex")
    parser.add_argument("--dblp-source", default=DBLP_URL, help="DBLP XML URL or local file")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--crossref-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--mailto", help="Optional email for the Crossref polite pool")
    parser.add_argument("--skip-crossref", action="store_true")
    parser.add_argument("--expected-count", type=int, default=4920)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    papers = parse_dblp_proceedings(
        load_text(args.dblp_source, user_agent=USER_AGENT),
        conference_id=CONFERENCE_ID,
        doi_prefix=DOI_PREFIX,
    )
    if args.expected_count is not None and len(papers) != args.expected_count:
        print(f"Error: expected {args.expected_count} AAAI papers, got {len(papers)}", file=sys.stderr)
        return 1

    cache = load_cache(args.crossref_cache)
    if not args.skip_crossref:
        cache = fetch_crossref_journal_metadata(
            [paper.doi for paper in papers],
            cache,
            args.crossref_cache,
            issn=AAAI_ISSN,
            from_date="2026-01-01",
            until_date="2026-12-31",
            user_agent=USER_AGENT,
            mailto=args.mailto,
        )
    records = [
        build_crossref_record(
            paper,
            cache.get(paper.doi, {}),
            venue=CONFERENCE_VENUE,
            primary_area=PRIMARY_AREA,
        )
        for paper in papers
    ]
    write_jsonl(args.output, records)

    with_abstract = sum(bool(record["content"]["abstract"]["value"]) for record in records)
    with_pdf = sum(bool(record["content"]["pdf"]["value"]) for record in records)
    print(f"Wrote {len(records)} AAAI 2026 papers to {args.output}")
    print(f"Abstracts: {with_abstract}/{len(records)}")
    print(f"PDF URLs: {with_pdf}/{len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
