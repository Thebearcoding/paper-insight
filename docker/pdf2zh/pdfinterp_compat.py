"""Compatibility shim for pdf2zh 1.9.4 with recent pdfminer.six.

pdf2zh's PDFPageInterpreterEx reads and assigns ``self.ncs``/``self.scs``
when processing Form XObjects, while current pdfminer stores both color spaces
on ``self.graphicstate``. Forwarding the old attributes preserves the active
PDF graphics state, including updates made by pdfminer's CS/cs operators.
"""


def patch_pdf_interpreter_colorspaces(interpreter_type: type) -> None:
    for name in ("ncs", "scs"):
        if hasattr(interpreter_type, name):
            continue

        def get_colorspace(self, _name=name):
            return getattr(self.graphicstate, _name)

        def set_colorspace(self, value, _name=name):
            setattr(self.graphicstate, _name, value)

        setattr(interpreter_type, name, property(get_colorspace, set_colorspace))
