"""Start the pdf2zh Celery worker with the doclayout model preloaded.

Two upstream quirks make the plain CLI unusable here:

* `pdf2zh --celery worker --loglevel=info --concurrency=1` never starts: the
  pdf2zh CLI uses a strict argparse parser that rejects Celery's own flags
  (`--loglevel`, `--concurrency`) with "unrecognized arguments".
* Running `celery -A pdf2zh.backend:celery_app worker` on its own leaves
  `pdf2zh.doclayout.ModelInstance.value` unset, and `translate_stream` calls
  `model.predict(...)` for every page, so each job would fail with
  `AttributeError: 'NoneType' object has no attribute 'predict'`.

The model is loaded in this parent process before Celery forks its children, so
the worker pool inherits one copy instead of loading one per child (which would
OOM a 1.5 GB container).
"""

import os

from pdf2zh.backend import celery_app
from pdf2zh.doclayout import ModelInstance, OnnxModel

ModelInstance.value = OnnxModel.load_available()


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
