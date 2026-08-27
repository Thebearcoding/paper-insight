from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_iclr_2026_jsonl import build_rows


def test_build_rows_supports_iclr_2025_event_embedded_abstracts():
    rows = build_rows(
        [
            {
                "id": 1,
                "name": "A 2025 ICLR Paper",
                "abstract": "Official abstract.",
                "authors": [{"fullname": "Ada Lovelace"}],
                "decision": "Accept (Oral)",
                "paper_url": "https://openreview.net/forum?id=paper-id",
                "paper_pdf_url": None,
                "keywords": ["representation learning"],
                "topic": "Deep Learning",
            }
        ],
        year=2025,
    )

    assert rows[0]["id"] == "paper-id"
    assert rows[0]["content"]["venue"]["value"] == "ICLR 2025 Oral"
    assert rows[0]["content"]["abstract"]["value"] == "Official abstract."
    assert rows[0]["content"]["pdf"]["value"] == "https://openreview.net/pdf?id=paper-id"


def test_build_rows_deduplicates_oral_schedule_entries_by_uid():
    common = {
        "name": "One Accepted Paper",
        "abstract": "Abstract.",
        "authors": [{"fullname": "Ada Lovelace"}],
        "decision": "Accept (Oral)",
        "paper_pdf_url": None,
        "keywords": [],
        "topic": "Machine Learning",
        "uid": "stable-event-uid",
    }
    rows = build_rows(
        [
            {
                **common,
                "id": 10,
                "event_type": "Poster",
                "paper_url": "https://openreview.net/forum?id=real-paper-id",
            },
            {
                **common,
                "id": 11,
                "event_type": "Oral",
                "paper_url": "https://openreview.net/forum?id=2025-Oral--synthetic",
            },
        ],
        year=2025,
    )

    assert len(rows) == 1
    assert rows[0]["id"] == "real-paper-id"
    assert rows[0]["content"]["venue"]["value"] == "ICLR 2025 Oral"
