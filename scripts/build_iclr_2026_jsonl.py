#!/usr/bin/env python3
"""Build import-ready ICLR JSONL from the official virtual-site data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests


REPO_ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "paper-online/0.1 (ICLR metadata importer)"
EXPECTED_COUNTS = {2025: 3799}


def fetch_json(url: str) -> Any:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
    response.raise_for_status()
    return response.json()


def openreview_id(event: dict[str, Any], year: int = 2026) -> str:
    candidate_urls = [event.get("paper_url"), event.get("url")]
    candidate_urls.extend(
        media.get("uri")
        for media in event.get("eventmedia") or []
        if isinstance(media, dict)
    )
    for candidate_url in candidate_urls:
        query_id = parse_qs(urlparse(str(candidate_url or "")).query).get("id", [])
        if query_id:
            return query_id[0]
    return f"iclr{year}-{event['id']}"


def venue_for_event(event: dict[str, Any], year: int = 2026) -> str:
    decision = " ".join(
        str(event.get(key) or "")
        for key in ("decision", "eventtype", "event_type")
    ).casefold()
    return f"ICLR {year} Oral" if "oral" in decision else f"ICLR {year} Poster"


def build_rows(
    events: list[dict[str, Any]],
    abstracts: dict[str, str] | None = None,
    *,
    year: int = 2026,
) -> list[dict[str, Any]]:
    abstracts = abstracts or {}
    rows_by_event: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for event in events:
        title = str(event.get("name") or "").strip()
        if not title or not event.get("id"):
            continue

        paper_id = openreview_id(event, year)
        venue = venue_for_event(event, year)
        event_key = str(event.get("uid") or event.get("sourceid") or paper_id)
        author_names = [
            str(author.get("fullname") or "").strip()
            for author in event.get("authors") or []
            if str(author.get("fullname") or "").strip()
        ]
        keywords = [str(value).strip() for value in event.get("keywords") or [] if str(value).strip()]
        pdf_url = str(event.get("paper_pdf_url") or "").strip()
        if not pdf_url and not paper_id.startswith(f"iclr{year}-"):
            pdf_url = f"https://openreview.net/pdf?id={paper_id}"

        row = {
            "id": paper_id,
            "content": {
                "title": {"value": title},
                "authors": {"value": author_names},
                "abstract": {
                    "value": str(
                        abstracts.get(str(event["id"])) or event.get("abstract") or ""
                    ).strip()
                },
                "keywords": {"value": keywords},
                "pdf": {"value": pdf_url or None},
                "venue": {"value": venue},
                "primary_area": {"value": str(event.get("topic") or "Machine Learning").strip()},
                "sort_order": {"value": len(order) + 1},
            },
        }

        if event_key not in rows_by_event:
            order.append(event_key)
            rows_by_event[event_key] = row
            continue

        current = rows_by_event[event_key]
        current_id = str(current["id"])
        synthetic_prefix = f"{year}-Oral--"
        if current_id.startswith(synthetic_prefix) and not paper_id.startswith(synthetic_prefix):
            row["content"]["sort_order"]["value"] = current["content"]["sort_order"]["value"]
            current = row
            rows_by_event[event_key] = current
        if venue.endswith("Oral"):
            current["content"]["venue"]["value"] = venue

    return [rows_by_event[event_key] for event_key in order]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ICLR JSONL from the official virtual site")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--events-url")
    parser.add_argument("--abstracts-url")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-count", type=int)
    args = parser.parse_args()

    events_url = args.events_url or f"https://iclr.cc/static/virtual/data/iclr-{args.year}-orals-posters.json"
    output = args.output or REPO_ROOT / "crawled_data" / f"iclr_{args.year}" / "official_papers.jsonl"
    events_payload = fetch_json(events_url)
    abstracts = fetch_json(args.abstracts_url) if args.abstracts_url else {}
    events = events_payload.get("results") if isinstance(events_payload, dict) else None
    if not isinstance(events, list) or not isinstance(abstracts, dict):
        raise ValueError("Unexpected ICLR virtual-site payload")

    rows = build_rows(events, abstracts, year=args.year)
    expected_count = args.expected_count
    if expected_count is None:
        expected_count = EXPECTED_COUNTS.get(args.year)
    if expected_count is not None and len(rows) != expected_count:
        raise ValueError(f"Expected {expected_count} ICLR {args.year} papers, got {len(rows)}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    missing_abstracts = sum(not row["content"]["abstract"]["value"] for row in rows)
    print(f"Wrote {len(rows)} ICLR {args.year} papers to {output}")
    print(f"Papers without abstract: {missing_abstracts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
