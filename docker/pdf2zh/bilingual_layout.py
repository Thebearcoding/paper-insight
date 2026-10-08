"""Compose pdf2zh's alternating original/translation pages side by side."""

from functools import wraps

import pymupdf

_GUTTER = 12


def side_by_side_pdf(pdf: bytes) -> bytes:
    """Keep each original on the left and its translation on the right.

    Embed the source pages as PDF objects rather than rendering images, so text
    stays searchable and figures/formulas keep their original resolution.
    """
    with pymupdf.open(stream=pdf, filetype="pdf") as source, pymupdf.open() as output:
        if not len(source) or len(source) % 2:
            raise ValueError("Bilingual PDF must contain original/translation page pairs")

        for index in range(0, len(source), 2):
            left, right = source[index], source[index + 1]
            right_x = left.rect.width + _GUTTER
            page = output.new_page(
                width=right_x + right.rect.width,
                height=max(left.rect.height, right.rect.height),
            )
            if left.get_contents():
                page.show_pdf_page(left.rect, source, index)
            if right.get_contents():
                page.show_pdf_page(
                    pymupdf.Rect(right_x, 0, right_x + right.rect.width, right.rect.height),
                    source, index + 1,
                )
            page.draw_line(
                (left.rect.width + _GUTTER / 2, 0),
                (left.rect.width + _GUTTER / 2, page.rect.height),
                color=(0.85, 0.85, 0.85), width=0.5,
            )

        # show_pdf_page preserves page contents but does not copy annotations.
        # Preserve hyperlinks and remap internal destinations to the paired pages.
        for index, source_page in enumerate(source):
            offset = source[index - 1].rect.width + _GUTTER if index % 2 else 0
            for original in source_page.get_links():
                link = {key: value for key, value in original.items() if key not in {"xref", "id"}}
                link["from"] = pymupdf.Rect(link["from"]) + (offset, 0, offset, 0)
                if link["kind"] in {pymupdf.LINK_GOTO, pymupdf.LINK_NAMED} and link.get("page", -1) >= 0:
                    target = link["page"]
                    target_offset = source[target - 1].rect.width + _GUTTER if target % 2 else 0
                    point = pymupdf.Point(link.get("to", (0, 0)))
                    link["page"] = target // 2
                    link["to"] = pymupdf.Point(point.x + target_offset, point.y)
                    link["kind"] = pymupdf.LINK_GOTO
                    link.pop("nameddest", None)
                output[index // 2].insert_link(link)

        output.set_metadata(source.metadata)
        bookmarks = source.get_toc()
        for bookmark in bookmarks:
            if bookmark[2] > 0:
                bookmark[2] = (bookmark[2] - 1) // 2 + 1
        output.set_toc(bookmarks)
        return output.tobytes(deflate=True, garbage=3, use_objstms=1)


def patch_bilingual_translation(backend) -> None:
    """Compose the dual result once, before Celery stores it in Redis."""
    original = backend.translate_stream
    if getattr(original, "_side_by_side", False):
        return

    @wraps(original)
    def translate_stream(*args, **kwargs):
        mono, dual = original(*args, **kwargs)
        return mono, side_by_side_pdf(dual)

    translate_stream._side_by_side = True
    backend.translate_stream = translate_stream
