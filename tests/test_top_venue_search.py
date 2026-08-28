import sys
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import top_venue_search


class FakeResponse:
    def __init__(self, payload, *, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = text
        self.reason = "OK" if self.ok else "Error"

    def json(self):
        return self._payload


def _fake_settings():
    return SimpleNamespace(
        openalex=SimpleNamespace(
            api_key=None,
            base_url="https://api.openalex.org",
            timeout_seconds=7,
            cache_ttl_seconds=1800,
        )
    )


def _dblp_payload():
    return {
        "result": {
            "hits": {
                "@total": "5",
                "hit": [
                    {
                        "@id": "1",
                        "info": {
                            "authors": {"author": [{"text": "Ada Lovelace"}]},
                            "title": "Wavelet Defect Detection.",
                            "venue": ["CVPR", "CoRR"],
                            "year": "2025",
                            "key": "conf/cvpr/example",
                            "doi": "10.1109/CVPR.2025.1",
                            "ee": "https://doi.org/10.1109/CVPR.2025.1",
                            "url": "https://dblp.org/rec/conf/cvpr/example",
                        },
                    },
                    {
                        "@id": "2",
                        "info": {
                            "title": "A workshop paper.",
                            "venue": "CVPR Workshops",
                            "year": "2025",
                        },
                    },
                    {
                        "@id": "3",
                        "info": {
                            "authors": {"author": {"text": "Alan Turing"}},
                            "title": "Learning Representations for Inspection.",
                            "venue": "ICML",
                            "year": "2025",
                            "key": "conf/icml/example",
                            "ee": "https://proceedings.mlr.press/v267/example.html",
                        },
                    },
                    {
                        "@id": "4",
                        "info": {
                            "title": "A journal paper.",
                            "venue": "Pattern Recognition",
                            "year": "2025",
                        },
                    },
                    {
                        "@id": "5",
                        "info": {
                            "title": "An old CVPR paper.",
                            "venue": "CVPR",
                            "year": "2020",
                        },
                    },
                ],
            }
        }
    }


def _openalex_work():
    return {
        "id": "https://openalex.org/W123",
        "doi": "https://doi.org/10.1109/cvpr.2025.1",
        "display_name": "Wavelet Defect Detection",
        "publication_year": 2025,
        "publication_date": "2025-06-01",
        "type": "conference-paper",
        "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
        "primary_location": {
            "landing_page_url": "https://doi.org/10.1109/cvpr.2025.1",
            "pdf_url": None,
            "source": None,
            "raw_source_name": "2025 IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR)",
        },
        "best_oa_location": {
            "landing_page_url": "https://arxiv.org/abs/2501.01234",
            "pdf_url": "https://arxiv.org/pdf/2501.01234",
            "source": {"display_name": "arXiv"},
        },
        "open_access": {"is_oa": True, "oa_url": "https://arxiv.org/abs/2501.01234"},
        "topics": [{"display_name": "Anomaly Detection"}],
        "keywords": [{"display_name": "Defect detection"}],
        "cited_by_count": 42,
        "abstract_inverted_index": {
            "Industrial": [0],
            "inspection": [1],
        },
    }


def test_query_expansion_and_strict_venue_matching():
    assert top_venue_search.expand_search_query("CLIP 缺陷检测") == "CLIP defect detection"
    assert top_venue_search.dblp_search_terms("缺陷检测") == "defect|anomaly detection"
    assert top_venue_search.canonical_dblp_venue("CVPR") == "CVPR"
    assert top_venue_search.canonical_dblp_venue("CVPR Workshops") is None
    assert top_venue_search.canonical_dblp_venue("ACL (1)") == "ACL"
    assert top_venue_search.canonical_openalex_venue(
        "2025 IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR)"
    ) == "CVPR"
    assert top_venue_search.canonical_openalex_venue(
        "2025 IEEE/CVF Conference on Computer Vision and Pattern Recognition Workshops (CVPRW)"
    ) is None


def test_dblp_search_filters_top_venues_and_enriches_from_openalex(monkeypatch):
    calls = []

    def fake_get(url, *, params, headers, timeout):
        calls.append((url, params, headers, timeout))
        if url == top_venue_search.DBLP_SEARCH_URL:
            return FakeResponse(_dblp_payload())
        return FakeResponse({"results": [_openalex_work()]})

    monkeypatch.setattr(top_venue_search, "settings", _fake_settings())
    monkeypatch.setattr(top_venue_search.requests, "get", fake_get)
    top_venue_search.clear_search_cache()

    first = top_venue_search.search_top_venue_papers(
        "缺陷检测",
        from_year=2022,
        to_year=2026,
        page=1,
        per_page=8,
    )
    second = top_venue_search.search_top_venue_papers(
        "缺陷检测",
        from_year=2022,
        to_year=2026,
        page=1,
        per_page=8,
    )

    assert first["total"] == 2
    assert first["effective_query"] == "defect detection"
    assert first["venues"] == list(top_venue_search.TOP_VENUE_LABELS)
    assert first["papers"][0]["venue"] == "CVPR"
    assert first["papers"][0]["abstract"] == "Industrial inspection"
    assert first["papers"][0]["online"]["top_venue"] == "CVPR"
    assert first["papers"][0]["online"]["cited_by_count"] == 42
    assert first["papers"][1]["venue"] == "ICML"
    assert first["papers"][1]["online"]["provider"] == "DBLP"
    assert second["cached"] is True
    assert len(calls) == 2
    dblp_call = calls[0]
    assert dblp_call[1]["q"].startswith("defect|anomaly detection ")
    assert "CVPR$" in dblp_call[1]["q"]
    assert dblp_call[1]["h"] == "1000"
    openalex_call = calls[1]
    assert openalex_call[1]["filter"] == "doi:10.1109/cvpr.2025.1"


def test_dblp_failure_falls_back_to_strict_openalex_results(monkeypatch):
    top_work = _openalex_work()
    workshop_work = {
        **_openalex_work(),
        "id": "https://openalex.org/W999",
        "doi": "https://doi.org/10.1109/cvprw.2025.9",
        "primary_location": {
            **_openalex_work()["primary_location"],
            "raw_source_name": "2025 IEEE/CVF Conference on Computer Vision and Pattern Recognition Workshops (CVPRW)",
        },
    }

    def fake_get(url, *, params, headers, timeout):
        del params, headers, timeout
        if url == top_venue_search.DBLP_SEARCH_URL:
            return FakeResponse({}, status_code=503)
        return FakeResponse({"results": [top_work, workshop_work]})

    monkeypatch.setattr(top_venue_search, "settings", _fake_settings())
    monkeypatch.setattr(top_venue_search.requests, "get", fake_get)
    top_venue_search.clear_search_cache()

    result = top_venue_search.search_top_venue_papers(
        "graph learning",
        from_year=2022,
        to_year=2026,
    )

    assert result["provider"] == "OpenAlex"
    assert result["total"] == 1
    assert result["papers"][0]["online"]["top_venue"] == "CVPR"
