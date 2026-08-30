from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import paper_resources


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.closed = False

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP {self.status_code}")

    def close(self):
        self.closed = True


def test_direct_document_candidates_normalizes_arxiv_and_openreview_urls():
    arxiv = paper_resources.direct_document_candidates(
        {
            "doi": "10.48550/arXiv.2503.06661",
            "url": "https://arxiv.org/abs/2503.06661",
            "raw": {"data": {}},
        },
        [],
    )
    openreview = paper_resources.direct_document_candidates(
        {"url": "https://openreview.net/forum?id=paper-123", "raw": {"data": {}}},
        [],
    )

    assert arxiv == [
        paper_resources.DocumentCandidate("https://arxiv.org/pdf/2503.06661", "arxiv")
    ]
    assert openreview == [
        paper_resources.DocumentCandidate("https://openreview.net/pdf?id=paper-123", "openreview")
    ]


def test_direct_document_candidates_converts_cvf_open_access_page_to_pdf():
    candidates = paper_resources.direct_document_candidates(
        {
            "url": (
                "https://openaccess.thecvf.com/content/ICCV2025/html/"
                "Gong_FE-CLIP_Frequency_Enhanced_CLIP_Model_ICCV_2025_paper.html"
            ),
            "raw": {"data": {}},
        },
        [],
    )

    assert candidates == [
        paper_resources.DocumentCandidate(
            "https://openaccess.thecvf.com/content/ICCV2025/papers/"
            "Gong_FE-CLIP_Frequency_Enhanced_CLIP_Model_ICCV_2025_paper.pdf",
            "cvf-openaccess",
        )
    ]


def test_discover_code_repositories_uses_explicit_material_and_normalizes_subpaths():
    repositories = paper_resources.discover_code_repositories(
        {
            "abstract_note": (
                "Code is available at https://github.com/Mwxinnn/AA-CLIP. "
                "Issues: https://github.com/Mwxinnn/AA-CLIP/issues/1"
            ),
            "raw": {"data": {}},
        },
        [],
        "Mirror: https://gitlab.com/research/group/repository/-/tree/main",
    )

    assert repositories == [
        "https://github.com/Mwxinnn/AA-CLIP",
        "https://gitlab.com/research/group/repository",
    ]


def test_full_text_repository_requires_nearby_code_evidence():
    repositories = paper_resources.discover_code_repositories(
        {"raw": {"data": {}}},
        [],
        (
            "A baseline appears in the references at https://github.com/example/baseline. "
            + ("unrelated discussion " * 30)
            + "Our implementation and code are available at https://github.com/authors/new-method."
        ),
    )

    assert repositories == ["https://github.com/authors/new-method"]


def test_semantic_scholar_candidate_reads_open_access_pdf(monkeypatch):
    response = FakeResponse({"openAccessPdf": {"url": "https://example.org/paper.pdf"}})
    monkeypatch.setattr(paper_resources.requests, "get", lambda *args, **kwargs: response)

    candidates = paper_resources.semantic_scholar_candidates(
        {"doi": "10.1000/example", "raw": {"data": {}}}
    )

    assert candidates == [
        paper_resources.DocumentCandidate("https://example.org/paper.pdf", "semantic-scholar")
    ]
    assert response.closed is True


def test_open_access_candidate_accepts_download_url_without_pdf_suffix():
    candidate = paper_resources._candidate_from_location(
        {"pdf_url": "https://repository.example.edu/download?id=123"},
        "openalex",
    )

    assert candidate == paper_resources.DocumentCandidate(
        "https://repository.example.edu/download?id=123",
        "openalex",
    )


def test_arxiv_html_extractor_keeps_article_text_only():
    parser = paper_resources._ArxivHtmlExtractor()
    parser.feed(
        """
        <html><body><nav>Navigation</nav>
        <article class="ltx_document">
          <h1>Paper Title</h1>
          <p>Method <math alttext="x + y"><mi>x</mi></math> result.</p>
          <script>ignore me</script>
        </article>
        <footer>Footer</footer></body></html>
        """
    )

    content = parser.text()
    assert "Paper Title" in content
    assert "x + y" in content
    assert "Navigation" not in content
    assert "ignore me" not in content
    assert "Footer" not in content


def test_arxiv_candidate_prefers_html_before_pdf(monkeypatch):
    monkeypatch.setattr(paper_resources, "get_cached_paper_content", lambda *args: None)
    monkeypatch.setattr(paper_resources, "_is_public_url", lambda url: True)
    monkeypatch.setattr(
        paper_resources,
        "_download_arxiv_html_text",
        lambda url: "A complete arXiv HTML paper body.",
    )
    monkeypatch.setattr(
        paper_resources,
        "_download_pdf_text",
        lambda url: (_ for _ in ()).throw(AssertionError("PDF fallback should not run")),
    )
    cached = []
    monkeypatch.setattr(
        paper_resources,
        "cache_paper_content",
        lambda paper_id, url, content, source: cached.append((url, content, source)),
    )

    document = paper_resources.fetch_document_candidate(
        paper_resources.DocumentCandidate("https://arxiv.org/pdf/2503.06661", "arxiv")
    )

    assert document.content == "A complete arXiv HTML paper body."
    assert cached == [
        (
            "https://arxiv.org/pdf/2503.06661",
            "A complete arXiv HTML paper body.",
            "arxiv-html",
        )
    ]


def test_bounded_pdf_extraction_propagates_parse_failure():
    with pytest.raises(paper_resources.ReaderError, match="PDF"):
        paper_resources.extract_pdf_text_bounded(b"not-a-pdf", "invalid.pdf")


def test_crossref_candidate_only_accepts_pdf_links(monkeypatch):
    response = FakeResponse(
        {
            "message": {
                "link": [
                    {"URL": "https://example.org/article.xml", "content-type": "application/xml"},
                    {"URL": "https://example.org/article.pdf", "content-type": "application/pdf"},
                ]
            }
        }
    )
    monkeypatch.setattr(paper_resources.requests, "get", lambda *args, **kwargs: response)

    candidates = paper_resources.crossref_candidates(
        {"doi": "10.1000/example", "raw": {"data": {}}}
    )

    assert candidates == [
        paper_resources.DocumentCandidate("https://example.org/article.pdf", "crossref")
    ]
