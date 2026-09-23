from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docker" / "pdf2zh"))

from pdfinterp_compat import patch_pdf_interpreter_colorspaces


def test_pdfinterp_colorspaces_follow_graphic_state_across_forms():
    class Interpreter:
        def __init__(self):
            self.graphicstate = SimpleNamespace(ncs="gray", scs="gray")

    patch_pdf_interpreter_colorspaces(Interpreter)
    parent = Interpreter()
    child = Interpreter()
    child.graphicstate.ncs = "CMYK"
    child.graphicstate.scs = "RGB"

    # pdf2zh's Form XObject handler reads the child's attributes into its
    # parent; the values must then stay synchronized with pdfminer's state.
    parent.ncs = child.ncs
    parent.scs = child.scs
    assert (parent.graphicstate.ncs, parent.graphicstate.scs) == ("CMYK", "RGB")
    parent.graphicstate.ncs = "RGB"
    assert parent.ncs == "RGB"
    assert child.ncs == "CMYK"

    patch_pdf_interpreter_colorspaces(Interpreter)
    assert parent.ncs == "RGB"


def test_pdfinterp_patch_is_in_worker_and_image():
    root = Path(__file__).resolve().parents[1] / "docker" / "pdf2zh"
    worker = (root / "worker.py").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "patch_pdf_interpreter_colorspaces(PDFPageInterpreterEx)" in worker
    assert "docker/pdf2zh/pdfinterp_compat.py" in dockerfile
