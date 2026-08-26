from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "build_ijcai_2025_jsonl.py"
SPEC = importlib.util.spec_from_file_location("build_ijcai_2025_jsonl", SCRIPT_PATH)
assert SPEC and SPEC.loader
ijcai = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ijcai
SPEC.loader.exec_module(ijcai)


LIST_SAMPLE = """
<div class="section_title"><h3>Main Track</h3>
  <div class="subsection_title">Agent-based and Multi-agent Systems</div>
  <div class="paper_wrapper">
    <div class="details"><a href="/proceedings/2025/1">Details</a></div>
  </div>
</div>
<div class="section_title"><h3>Demo Track</h3>
  <div class="paper_wrapper">
    <div class="details"><a href="/proceedings/2025/2">Details</a></div>
  </div>
</div>
"""


DETAIL_SAMPLE = """
<html><head>
<meta name="citation_title" content="Synthesising Minimum Cost Dynamic Norms">
<meta name="citation_author" content="Natasha Alechina">
<meta name="citation_author" content="Brian Logan">
<meta name="citation_firstpage" content="3">
<meta name="citation_lastpage" content="11">
<meta name="citation_pdf_url" content="https://www.ijcai.org/proceedings/2025/0001.pdf">
<meta name="citation_doi" content="10.24963/ijcai.2025/1">
</head><body>
<hr>
<div class="row">
  <div class="col-md-12">This is the official abstract.</div>
  <div class="col-md-12"><div class="keywords">
    <div class="title">Keywords:</div>
    <div class="topic">Multi-agent Systems</div>
    <div class="topic">Normative systems</div>
  </div></div>
</div>
</body></html>
"""


def test_parse_list_preserves_track_and_official_order():
    links = ijcai.parse_ijcai_list(LIST_SAMPLE)

    assert len(links) == 2
    assert links[0].order == 1
    assert links[0].section == "Main Track"
    assert links[0].subsection == "Agent-based and Multi-agent Systems"
    assert links[1].section == "Demo Track"
    assert links[1].subsection is None


def test_parse_detail_and_build_record():
    link = ijcai.parse_ijcai_list(LIST_SAMPLE)[0]
    detail = ijcai.parse_ijcai_detail(link, DETAIL_SAMPLE)

    assert detail["title"] == "Synthesising Minimum Cost Dynamic Norms"
    assert detail["authors"] == ["Natasha Alechina", "Brian Logan"]
    assert detail["abstract"] == "This is the official abstract."
    assert detail["keywords"] == ["Multi-agent Systems", "Normative systems"]
    assert detail["pages"] == "3-11"

    record = ijcai.build_jsonl_record(detail)
    assert record["id"] == "ijcai2025-10-24963-ijcai-2025-1"
    assert record["content"]["venue"]["value"] == "IJCAI 2025"
    assert record["content"]["primary_area"]["value"] == "Agent-based and Multi-agent Systems"
    assert record["content"]["sort_order"]["value"] == 1
