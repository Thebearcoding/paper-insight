from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from dblp_openalex import (
    abstract_from_crossref,
    abstract_from_openalex,
    build_crossref_record,
    build_record,
    parse_dblp_proceedings,
    pdf_from_crossref,
    pdf_from_openalex,
    plain_text_from_markup,
)


DBLP_SAMPLE = """<bht>
<h2>Technical Tracks 1</h2>
<dblpcites>
  <r><inproceedings key="conf/aaai/Example26">
    <author>Ada Lovelace</author><author>Alan Turing</author>
    <title>A Formal AAAI Paper.</title><pages>1-9</pages>
    <ee>https://doi.org/10.1609/aaai.v40i1.36958</ee>
  </inproceedings></r>
</dblpcites>
</bht>"""


def test_parse_dblp_proceedings_builds_stable_official_paper():
    papers = parse_dblp_proceedings(
        DBLP_SAMPLE,
        conference_id="aaai_2026",
        doi_prefix="10.1609/aaai.v40",
    )

    assert len(papers) == 1
    assert papers[0].id == "aaai2026-10-1609-aaai-v40i1-36958"
    assert papers[0].authors == ["Ada Lovelace", "Alan Turing"]
    assert papers[0].section == "Technical Tracks 1"


def test_openalex_helpers_reconstruct_abstract_and_official_pdf():
    item = {
        "abstract_inverted_index": {"Paper": [1], "Formal": [0]},
        "primary_location": {"pdf_url": "https://ojs.aaai.org/paper.pdf"},
    }

    assert abstract_from_openalex(item) == "Formal Paper"
    assert pdf_from_openalex(item) == "https://ojs.aaai.org/paper.pdf"


def test_build_record_uses_dblp_acceptance_and_openalex_content():
    paper = parse_dblp_proceedings(
        DBLP_SAMPLE,
        conference_id="aaai_2026",
        doi_prefix="10.1609/aaai.v40",
    )[0]
    record = build_record(
        paper,
        {
            "title": "A Formal AAAI Paper",
            "abstract_inverted_index": {"Accepted": [0], "work": [1]},
            "keywords": [{"display_name": "Artificial intelligence"}],
            "primary_location": {"pdf_url": "https://ojs.aaai.org/paper.pdf"},
        },
        venue="AAAI 2026",
        primary_area="Artificial Intelligence",
    )

    assert record["content"]["venue"]["value"] == "AAAI 2026"
    assert record["content"]["abstract"]["value"] == "Accepted work"
    assert record["content"]["pdf"]["value"].startswith("https://ojs.aaai.org/")
    assert record["content"]["doi"]["value"] == "10.1609/aaai.v40i1.36958"


def test_crossref_helpers_clean_jats_abstract_and_choose_pdf():
    item = {
        "abstract": "<jats:p>A &amp; B <jats:italic>method</jats:italic>.</jats:p>",
        "link": [
            {
                "URL": "https://ojs.aaai.org/paper.pdf",
                "content-type": "application/pdf",
                "intended-application": "text-mining",
            }
        ],
    }

    assert abstract_from_crossref(item) == "A & B method."
    assert pdf_from_crossref(item) == "https://ojs.aaai.org/paper.pdf"


def test_crossref_markup_is_converted_to_plain_text():
    assert plain_text_from_markup("<scp>LiveRAG:</scp> Q&amp;A <i>Dataset</i>") == "LiveRAG: Q&A Dataset"


def test_build_crossref_record_uses_formal_metadata():
    paper = parse_dblp_proceedings(
        DBLP_SAMPLE,
        conference_id="aaai_2026",
        doi_prefix="10.1609/aaai.v40",
    )[0]
    record = build_crossref_record(
        paper,
        {
            "title": ["A Formal AAAI Paper"],
            "abstract": "<jats:p>Accepted work</jats:p>",
            "subject": ["Artificial Intelligence"],
            "link": [{"URL": "https://ojs.aaai.org/paper.pdf", "content-type": "application/pdf"}],
        },
        venue="AAAI 2026",
        primary_area="Artificial Intelligence",
    )

    assert record["content"]["abstract"]["value"] == "Accepted work"
    assert record["content"]["keywords"]["value"] == ["Artificial Intelligence"]
    assert record["content"]["source"]["value"] == "DBLP + Crossref"
