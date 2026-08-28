from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import openalex_search


OPENALEX_WORK = {
    "id": "https://openalex.org/W123",
    "doi": "https://doi.org/10.1234/example",
    "display_name": "A Recent Defect Detection Paper",
    "publication_year": 2025,
    "publication_date": "2025-06-01",
    "type": "article",
    "authorships": [
        {"author": {"display_name": "Ada Lovelace"}},
        {"raw_author_name": "Alan Turing"},
    ],
    "primary_location": {
        "landing_page_url": "https://doi.org/10.1234/example",
        "pdf_url": "https://dl.acm.org/doi/pdf/10.1234/example",
        "source": {"display_name": "Example Conference"},
    },
    "best_oa_location": {
        "pdf_url": "https://arxiv.org/pdf/2501.01234",
    },
    "open_access": {"is_oa": True},
    "topics": [
        {
            "display_name": "Industrial Defect Detection",
            "subfield": {"display_name": "Computer Vision"},
        }
    ],
    "keywords": [{"display_name": "Anomaly detection"}],
    "cited_by_count": 42,
    "abstract_inverted_index": {"detects": [2], "This": [0], "paper": [1]},
}


class FakeResponse:
    status_code = 200
    ok = True
    text = ""
    reason = "OK"

    def json(self):
        return {"meta": {"count": 12}, "results": [OPENALEX_WORK]}


def _fake_settings(*, api_key="test-key", cache_ttl_seconds=1800):
    return SimpleNamespace(
        openalex=SimpleNamespace(
            api_key=api_key,
            base_url="https://api.openalex.org",
            timeout_seconds=9,
            cache_ttl_seconds=cache_ttl_seconds,
        )
    )


def test_normalize_openalex_work_builds_non_persistent_paper_payload():
    paper = openalex_search.normalize_openalex_work(OPENALEX_WORK)

    assert paper is not None
    assert paper["id"] == "openalex:W123"
    assert paper["abstract"] == "This paper detects"
    assert paper["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert paper["keywords"][:2] == ["Anomaly detection", "Industrial Defect Detection"]
    assert paper["pdf"] == "https://arxiv.org/pdf/2501.01234"
    assert paper["venue"] == "Example Conference"
    assert paper["primary_area"] == "Computer Vision"
    assert paper["online"]["cited_by_count"] == 42


def test_search_recent_papers_uses_year_filter_sort_key_and_memory_cache(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(openalex_search, "settings", _fake_settings())
    monkeypatch.setattr(openalex_search.requests, "get", fake_get)
    openalex_search.clear_search_cache()

    first = openalex_search.search_recent_papers(
        " defect   detection ",
        from_year=2022,
        to_year=2026,
        page=1,
        per_page=8,
        sort="newest",
        today=date(2026, 8, 28),
    )
    second = openalex_search.search_recent_papers(
        "defect detection",
        from_year=2022,
        to_year=2026,
        page=1,
        per_page=8,
        sort="newest",
        today=date(2026, 8, 28),
    )

    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://api.openalex.org/works"
    assert kwargs["params"]["api_key"] == "test-key"
    assert kwargs["params"]["sort"] == "publication_date:desc,relevance_score:desc"
    assert "from_publication_date:2022-01-01" in kwargs["params"]["filter"]
    assert "to_publication_date:2026-08-28" in kwargs["params"]["filter"]
    assert "is_retracted:false" in kwargs["params"]["filter"]
    assert first["cached"] is False
    assert first["total"] == 12
    assert first["pages"] == 2
    assert second["cached"] is True


def test_search_recent_papers_works_without_api_key(monkeypatch):
    captured_params = {}

    def fake_get(_url, **kwargs):
        captured_params.update(kwargs["params"])
        return FakeResponse()

    monkeypatch.setattr(
        openalex_search,
        "settings",
        _fake_settings(api_key=None, cache_ttl_seconds=0),
    )
    monkeypatch.setattr(openalex_search.requests, "get", fake_get)

    openalex_search.search_recent_papers(
        "agents",
        from_year=2024,
        to_year=2025,
        sort="relevance",
    )

    assert "api_key" not in captured_params
