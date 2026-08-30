from __future__ import annotations

import base64
import sys
from pathlib import Path

import pymupdf
import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import paper_figures
from utils import ReaderError


ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_framework_caption_score_prefers_pipeline_over_visualization():
    pipeline = paper_figures.framework_caption_score(
        "Figure 3: Overview of the proposed two-stage training pipeline."
    )
    visualization = paper_figures.framework_caption_score(
        "Figure 5: Visualization of anomaly localization results."
    )

    assert pipeline > 0
    assert pipeline > visualization


def test_extract_arxiv_framework_figure_selects_method_pipeline(monkeypatch):
    html = b"""
    <article class="ltx_document">
      <figure class="ltx_figure">
        <img src="tsne.png">
        <figcaption>Figure 2: t-SNE Visualization of Text Features.</figcaption>
      </figure>
      <figure class="ltx_figure">
        <img src="main.png">
        <figcaption>Figure 3: The Two-Stage Training Pipeline of the proposed method.</figcaption>
      </figure>
    </article>
    """

    def fake_download(url: str, *, max_bytes: int, accept: str):
        if url.endswith("2503.06661"):
            return html, "text/html", "https://arxiv.org/html/2503.06661"
        assert url == "https://arxiv.org/html/main.png"
        return ONE_PIXEL_PNG, "image/png", url

    monkeypatch.setattr(paper_figures, "_download_public_bytes", fake_download)

    asset = paper_figures.extract_arxiv_framework_figure("2503.06661")

    assert asset is not None
    assert asset.label == "Figure 3"
    assert "Training Pipeline" in asset.caption
    assert asset.source == "arxiv-html"
    assert asset.media_type == "image/png"


def test_extract_framework_figure_from_pdf_uses_caption_page():
    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    page.draw_rect(pymupdf.Rect(70, 120, 530, 430), color=(0, 0, 0), fill=(0.95, 0.95, 0.95))
    page.insert_textbox(
        pymupdf.Rect(70, 460, 530, 520),
        "Figure 1: Overview of the proposed architecture and training pipeline.",
        fontsize=12,
    )
    pdf_bytes = document.tobytes()
    document.close()

    asset = paper_figures.extract_framework_figure_from_pdf(pdf_bytes, "https://example.org/paper.pdf")

    assert asset is not None
    assert asset.label == "Figure 1"
    assert asset.page_number == 1
    assert asset.image_bytes.startswith(b"\x89PNG")
    assert asset.width and asset.height


def test_zotero_figure_path_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_figures, "zotero_figure_root", lambda: tmp_path)

    with pytest.raises(ReaderError):
        paper_figures.zotero_figure_path("user", "item", "../secret.png")


def test_force_refresh_skips_existing_zotero_framework_figure(tmp_path, monkeypatch):
    cached_file = tmp_path / "framework-cached.png"
    cached_file.write_bytes(ONE_PIXEL_PNG)
    monkeypatch.setattr(paper_figures, "zotero_figure_path", lambda *_args: cached_file)
    item = {
        "item_key": "PAPER1",
        "analysis_figures": [
            {
                "id": "cached",
                "kind": "framework",
                "filename": cached_file.name,
                "label": "Figure 1",
            }
        ],
        "raw": {"data": {}},
    }

    cached = paper_figures.extract_and_save_zotero_framework_figure(
        user_id="user",
        zotero_user_id=123,
        item=item,
        children=[],
        client=object(),
        reading_context="",
    )
    refreshed = paper_figures.extract_and_save_zotero_framework_figure(
        user_id="user",
        zotero_user_id=123,
        item=item,
        children=[],
        client=object(),
        reading_context="",
        force_refresh=True,
    )

    assert cached == item["analysis_figures"][0]
    assert refreshed is None
