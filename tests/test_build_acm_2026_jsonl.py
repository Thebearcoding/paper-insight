from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "build_acm_2026_jsonl.py"
SPEC = importlib.util.spec_from_file_location("build_acm_2026_jsonl", SCRIPT_PATH)
assert SPEC and SPEC.loader
acm = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = acm
SPEC.loader.exec_module(acm)


def crossref_item(doi: str, *, page: str, authors: bool = True):
    item = {
        "DOI": doi,
        "title": [f"Paper {doi}"],
        "page": page,
        "container-title": ["Formal proceedings"],
    }
    if authors:
        item["author"] = [{"given": "Ada", "family": "Lovelace"}]
    return item


def test_kdd_items_sort_by_volume_then_page():
    config = acm.CONFERENCES["kdd_2026"]
    metadata = {
        "v2": crossref_item("10.1145/3770855.3817000", page="10-20"),
        "v1-late": crossref_item("10.1145/3770854.3816000", page="90-99"),
        "v1-early": crossref_item("10.1145/3770854.3815000", page="2-9"),
    }

    items = acm.sorted_crossref_items(config, metadata)

    assert [item["DOI"] for item in items] == [
        "10.1145/3770854.3815000",
        "10.1145/3770854.3816000",
        "10.1145/3770855.3817000",
    ]


def test_crossref_item_uses_openalex_author_fallback():
    config = acm.CONFERENCES["sigir_2026"]
    item = crossref_item("10.1145/3805712.3808630", page="3124-3129", authors=False)
    openalex_item = {
        "authorships": [{"author": {"display_name": "Benjamin Clavie"}}],
    }

    paper = acm.crossref_item_to_paper(config, item, openalex_item)

    assert paper.authors == ["Benjamin Clavie"]
    assert paper.section == "Main Proceedings"
    assert paper.id == "sigir2026-10-1145-3805712-3808630"


def test_formal_record_without_publisher_authors_is_preserved():
    config = acm.CONFERENCES["kdd_2026"]
    item = crossref_item("10.1145/3770855.3819039", page="10924-10935", authors=False)

    paper = acm.crossref_item_to_paper(config, item, {})

    assert paper.authors == []
    assert paper.title


def test_crossref_title_markup_is_removed():
    config = acm.CONFERENCES["sigir_2026"]
    item = crossref_item("10.1145/3805712.3808585", page="1-10")
    item["title"] = ["<i>LiveRAG:</i> A Q&amp;A Dataset"]

    paper = acm.crossref_item_to_paper(config, item)

    assert paper.title == "LiveRAG: A Q&A Dataset"


def test_2025_acm_conferences_use_formal_proceedings_counts():
    assert acm.CONFERENCES["kdd_2025"].expected_count == 844
    assert acm.CONFERENCES["sigir_2025"].expected_count == 540
    assert acm.CONFERENCES["chi_2025"].expected_count == 1249
    assert acm.CONFERENCES["kdd_2025"].doi_sections[0][0] == "10.1145/3690624"
