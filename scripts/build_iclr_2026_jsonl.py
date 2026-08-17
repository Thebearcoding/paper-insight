#!/usr/bin/env python3
"""Build import-ready ICLR 2026 JSONL from the official virtual-site data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests


REPO_ROOT = Path(__file__).resolve().parent.parent
EVENTS_URL = "https://iclr.cc/static/virtual/data/iclr-2026-orals-posters.json"
ABSTRACTS_URL = "https://iclr.cc/static/virtual/data/iclr-2026-abstracts.json"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "crawled_data" / "iclr_2026" / "official_papers.jsonl"
USER_AGENT = "paper-online/0.1 (ICLR 2026 metadata importer)"


def fetch_json(url: str) -> Any:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
    response.raise_for_status()
    return response.json()


def openreview_id(event: dict[str, Any]) -> str:
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
    return f"iclr2026-{event['id']}"


def venue_for_event(event: dict[str, Any]) -> str:
    decision = " ".join(
        str(event.get(key) or "")
        for key in ("decision", "eventtype", "event_type")
    ).casefold()
    return "ICLR 2026 Oral" if "oral" in decision else "ICLR 2026 Poster"


def build_rows(
    events: list[dict[str, Any]],
    abstracts: dict[str, str],
) -> list[dict[str, Any]]:
    rows_by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for event in events:
        title = str(event.get("name") or "").strip()
        if not title or not event.get("id"):
            continue

        paper_id = openreview_id(event)
        venue = venue_for_event(event)
        author_names = [
            str(author.get("fullname") or "").strip()
            for author in event.get("authors") or []
            if str(author.get("fullname") or "").strip()
        ]
        keywords = [str(value).strip() for value in event.get("keywords") or [] if str(value).strip()]
        pdf_url = str(event.get("paper_pdf_url") or "").strip()
        if not pdf_url and not paper_id.startswith("iclr2026-"):
            pdf_url = f"https://openreview.net/pdf?id={paper_id}"

        row = {
            "id": paper_id,
            "content": {
                "title": {"value": title},
                "authors": {"value": author_names},
                "abstract": {"value": str(abstracts.get(str(event["id"])) or "").strip()},
                "keywords": {"value": keywords},
                "pdf": {"value": pdf_url or None},
                "venue": {"value": venue},
                "primary_area": {"value": str(event.get("topic") or "Machine Learning").strip()},
                "sort_order": {"value": len(order) + 1},
            },
        }

        if paper_id not in rows_by_id:
            order.append(paper_id)
            rows_by_id[paper_id] = row
        elif venue.endswith("Oral"):
            row["content"]["sort_order"]["value"] = rows_by_id[paper_id]["content"]["sort_order"]["value"]
            rows_by_id[paper_id] = row

    return [rows_by_id[paper_id] for paper_id in order]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ICLR 2026 JSONL from the official virtual site")
    parser.add_argument("--events-url", default=EVENTS_URL)
    parser.add_argument("--abstracts-url", default=ABSTRACTS_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    events_payload = fetch_json(args.events_url)
    abstracts = fetch_json(args.abstracts_url)
    events = events_payload.get("results") if isinstance(events_payload, dict) else None
    if not isinstance(events, list) or not isinstance(abstracts, dict):
        raise ValueError("Unexpected ICLR virtual-site payload")

    rows = build_rows(events, abstracts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    missing_abstracts = sum(not row["content"]["abstract"]["value"] for row in rows)
    print(f"Wrote {len(rows)} ICLR 2026 papers to {args.output}")
    print(f"Papers without abstract: {missing_abstracts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
