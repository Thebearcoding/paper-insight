"""Start the pdf2zh Celery worker with the doclayout model preloaded.

Three upstream quirks make the plain CLI unusable here:

* `pdf2zh --celery worker --loglevel=info --concurrency=1` never starts: the
  pdf2zh CLI uses a strict argparse parser that rejects Celery's own flags
  (`--loglevel`, `--concurrency`) with "unrecognized arguments".
* Running `celery -A pdf2zh.backend:celery_app worker` on its own leaves
  `pdf2zh.doclayout.ModelInstance.value` unset, and `translate_stream` calls
  `model.predict(...)` for every page, so each job would fail with
  `AttributeError: 'NoneType' object has no attribute 'predict'`.
* `pdf2zh.doclayout.OnnxModel.__init__` loads the model file three times over:
  `onnx.load(path)` into a protobuf (~140 MB for the 72 MB file), then
  `model.SerializeToString()` into a second copy (~70 MB), then
  `InferenceSession(bytes)` into a third. The protobuf and the serialized bytes
  are both dead by the end of `__init__`, but glibc keeps the freed arena, so the
  process sits at the peak forever — measured in this image: 492 MB RSS after
  init with the upstream loader, 389 MB with `LeanOnnxModel` (384 MB → 212 MB in
  an interpreter that has only onnx/onnxruntime imported).

The model is loaded in this parent process before Celery forks its children, so
the worker pool inherits one copy instead of loading one per child (which would
OOM a 1.5 GB container).
"""

import ast
import os

import onnxruntime
from babeldoc.assets.assets import get_doclayout_onnx_model_path
from pdf2zh.backend import celery_app
from pdf2zh.doclayout import ModelInstance, OnnxModel


class LeanOnnxModel(OnnxModel):
    """`OnnxModel` that builds the session straight from the model file.

    Upstream reads two things out of the ONNX protobuf — `stride` and `names`
    from `metadata_props` — and then throws the protobuf away. ONNX Runtime
    exposes exactly the same metadata on the session itself
    (`session.get_modelmeta().custom_metadata_map`), so the session can be
    created from the file path and neither the protobuf nor the serialized copy
    is ever allocated. Same model file, same providers, same `predict()`
    behaviour; only the loading path changes.

    `OnnxModel.load_available()`/`from_pretrained()` cannot be reused here:
    both hardcode the base class, so they would return an upstream `OnnxModel`.
    """

    def __init__(self, model_path: str):
        self.model_path = model_path
        self.model = onnxruntime.InferenceSession(model_path)
        metadata = self.model.get_modelmeta().custom_metadata_map
        self._stride = ast.literal_eval(metadata["stride"])
        self._names = ast.literal_eval(metadata["names"])


ModelInstance.value = LeanOnnxModel(get_doclayout_onnx_model_path())


def _worker_argv() -> list[str]:
    concurrency = os.environ.get("PDF2ZH_CELERY_CONCURRENCY", "1")
    # Celery 5.x parses this with click, so it must NOT contain the program name:
    # the first element is the subcommand.
    return [
        "worker",
        "--loglevel=info",
        f"--concurrency={concurrency}",
        # Single worker, single node: skip the gossip/mingle startup chatter.
        "--without-gossip",
        "--without-mingle",
    ]


if __name__ == "__main__":
    celery_app.start(argv=_worker_argv())
