from pathlib import Path
from types import SimpleNamespace
import sys

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docker" / "pdf2zh"))

from bilingual_layout import patch_bilingual_translation, side_by_side_pdf


def _paired_pdf() -> bytes:
    with pymupdf.open() as doc:
        for label, width, height in [
            ("English one", 300, 400), ("Chinese one", 240, 360),
            ("English two", 260, 350), ("Chinese two", 280, 420),
        ]:
            page = doc.new_page(width=width, height=height)
            page.insert_text((25, 80), label)
        doc[1].insert_link({
            "kind": pymupdf.LINK_URI, "from": pymupdf.Rect(25, 65, 120, 85),
            "uri": "https://example.com/paper",
        })
        doc[0].insert_link({
            "kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(25, 100, 120, 120),
            "page": 3, "to": pymupdf.Point(29, 48),
        })
        doc.set_toc([[1, "First pair", 1], [1, "Second pair", 4]])
        return doc.tobytes()


def test_paired_pages_keep_text_and_geometry_on_the_correct_side():
    result = side_by_side_pdf(_paired_pdf())
    with pymupdf.open(stream=result, filetype="pdf") as doc:
        assert len(doc) == 2
        assert doc[0].rect == pymupdf.Rect(0, 0, 552, 400)
        assert doc[1].rect == pymupdf.Rect(0, 0, 552, 420)
        assert doc.get_toc() == [[1, "First pair", 1], [1, "Second pair", 2]]
        for index, (left_width, number) in enumerate([(300, "one"), (260, "two")]):
            english = doc[index].search_for(f"English {number}")[0]
            chinese = doc[index].search_for(f"Chinese {number}")[0]
            assert english.x0 == pytest.approx(25)
            assert english.x1 < left_width
            assert chinese.x0 == pytest.approx(left_width + 12 + 25)
            assert english.y0 == pytest.approx(chinese.y0)


def test_hyperlinks_and_internal_destinations_follow_the_paired_pages():
    with pymupdf.open(stream=side_by_side_pdf(_paired_pdf()), filetype="pdf") as doc:
        links = doc[0].get_links()
        assert len(links) == 2
        uri = next(link for link in links if link["kind"] == pymupdf.LINK_URI)
        assert uri["uri"] == "https://example.com/paper"
        assert uri["from"] == pymupdf.Rect(337, 65, 432, 85)
        internal = next(link for link in links if link["kind"] == pymupdf.LINK_GOTO)
        assert internal["page"] == 1
        assert internal["to"] == pymupdf.Point(301, 48)


def test_unpaired_pdf_is_rejected():
    with pymupdf.open() as doc:
        doc.new_page().insert_text((20, 20), "Unpaired page")
        with pytest.raises(ValueError, match="page pairs"):
            side_by_side_pdf(doc.tobytes())


def test_blank_page_pairs_are_preserved():
    with pymupdf.open() as blank:
        blank.new_page()
        blank.new_page()
        with pymupdf.open(stream=side_by_side_pdf(blank.tobytes()), filetype="pdf") as paired:
            assert len(paired) == 1
            assert paired[0].get_text() == ""


def test_translation_patch_preserves_mono_and_forwards_arguments_once():
    calls = []

    def translate(*args, **kwargs):
        calls.append((args, kwargs))
        return b"original-mono", _paired_pdf()

    backend = SimpleNamespace(translate_stream=translate)
    patch_bilingual_translation(backend)
    patched = backend.translate_stream
    patch_bilingual_translation(backend)
    assert backend.translate_stream is patched
    mono, dual = backend.translate_stream(b"input", lang_out="zh")
    assert mono == b"original-mono"
    assert calls == [((b"input",), {"lang_out": "zh"})]
    with pymupdf.open(stream=dual, filetype="pdf") as doc:
        assert len(doc) == 2


def test_layout_patch_is_loaded_by_the_worker_and_included_in_the_image():
    root = Path(__file__).resolve().parents[1] / "docker" / "pdf2zh"
    worker = (root / "worker.py").read_text()
    assert "patch_bilingual_translation(pdf2zh_backend)" in worker
    assert "docker/pdf2zh/bilingual_layout.py" in (root / "Dockerfile").read_text()
