"""Run pdf2zh as one supervised process tree: Flask HTTP API + Celery worker.

The entrypoint starts this file and nothing else; this file owns the rest of the
container's Python side:

* It imports pdf2zh, babeldoc, onnxruntime and cv2 first — 128MB of anonymous
  memory in this image (measured 2026-09-18) — and only then forks. The Flask
  server is forked out of that state and the Celery pool forks out of it too, so
  all of them map the same physical pages rather than importing their own copy of
  the stack. Until then the entrypoint started `server.py` and `worker.py` as
  independent processes and each paid for its own copy: measured 128MB private
  anonymous in the Flask process, which is why the container sat at 381MB of
  cgroup usage while idle. Forked children share it instead (an idle child's PSS
  is ~66MB for the same 128MB, and the Flask child still shares most of it after
  serving traffic).
* It supervises the Flask child: if the HTTP side dies this process exits, so
  compose restarts the container instead of leaving an API that answers 502
  while Celery still reports healthy.

Four upstream quirks make the plain CLI unusable here, plus one that breaks the
HTTP contract outright:

* `pdf2zh --celery worker --loglevel=info --concurrency=1` never starts: the
  pdf2zh CLI uses a strict argparse parser that rejects Celery's own flags
  (`--loglevel`, `--concurrency`) with "unrecognized arguments".
* Running `celery -A pdf2zh.backend:celery_app worker` on its own leaves
  `pdf2zh.doclayout.ModelInstance.value` unset, and `translate_stream` passes it
  straight to the layout model, so each job would fail with
  `AttributeError: 'NoneType' object has no attribute 'predict'`.
* `pdf2zh.doclayout.OnnxModel.__init__` loads the model file three times over:
  `onnx.load(path)` into a protobuf (~140 MB for the 72 MB file), then
  `model.SerializeToString()` into a second copy (~70 MB), then
  `InferenceSession(bytes)` into a third. The protobuf and the serialized bytes
  are both dead by the end of `__init__`, but glibc keeps the freed arena, so the
  process sits at the peak forever — measured in this image: 492 MB RSS after
  init with the upstream loader, 389 MB with `LeanOnnxModel` (384 MB → 212 MB in
  an interpreter that has only onnx/onnxruntime imported).
* ONNX Runtime's CPU memory arena only ever grows. A session that has to serve
  pages of many different shapes keeps every shape's buffers alive until the
  process dies: measured in this image at `imgsz=1024`, four `session.run()`
  calls take the session from 271 MB to 483 MB RSS and it never comes back down.
  `LeanOnnxModel` therefore builds its session with the arena (and the allocator
  memory pattern, which exists to serve the arena) switched off; the same four
  runs then sit at 283 MB with a 353 MB high-water mark instead of 491 MB. The
  cost is a few extra `malloc`/`free` pairs per page, which is noise next to the
  inference itself.
* `pdf2zh/backend.py` builds Celery from
  `ConfigManager.get("CELERY_RESULT", "redis://127.0.0.1:6379/0")`, but that
  default is unreachable: the image already ships a `config.json` containing
  `"CELERY_RESULT": null`, and `ConfigManager.get` returns any stored value
  verbatim instead of falling back. Celery then runs with `result_backend = None`,
  i.e. `DisabledBackend`, and `/v1/translate/<id>` dies with
  `AttributeError: 'DisabledBackend' object has no attribute '_get_task_meta_for'`
  → Flask 500 on every progress poll. This file sets the backend to the broker URL
  before forking, so the Flask child reads the same one the worker writes to.

Two Celery knobs in `_worker_argv` are deliberate rather than defaults:

* `--concurrency=1`: one translation already fans out to several LLM calls per
  batch internally, and every extra child that translates holds its own copy of
  the doclayout session.
* `--max-tasks-per-child=1` is what makes the lazy model loading below pay off.
  A child exits after each task, so the pages its session allocated go back to
  the kernel instead of staying resident for the container's lifetime.

The doclayout session costs ~80MB of anonymous memory (measured: RssAnon 128MB
after importing the stack, 209MB with the session built, back to 131MB after
dropping it and calling malloc_trim). This file used to build it at import time,
which kept those pages resident around the clock for a service that translates a
handful of papers. Reading `ModelInstance.value` builds it instead: the only
reader in this container is `translate_task` in upstream's `pdf2zh/backend.py`
(its `model=ModelInstance.value` argument), i.e. the forked task child that is
recycled right after the task. (`pdf2zh/gui.py` and `pdf2zh/pdf2zh.py` read it as
well, but the GUI and the CLI do not run here.) Cost: 1.84s of session setup per
translation, which is noise next to the per-page LLM calls that dominate a
translation.
"""

import ast
import os
import runpy
import signal
import sys
import threading
import traceback

import onnxruntime
from babeldoc.assets.assets import get_doclayout_onnx_model_path
from pdf2zh.backend import celery_app
from pdf2zh.doclayout import ModelInstance, OnnxModel
from pdf2zh.pdfinterp import PDFPageInterpreterEx

from pdfinterp_compat import patch_pdf_interpreter_colorspaces

# pdf2zh 1.9.4 expects these attributes on the interpreter, but current
# pdfminer keeps them on graphicstate. Install before Celery forks its worker.
patch_pdf_interpreter_colorspaces(PDFPageInterpreterEx)

SERVER_SCRIPT = "/opt/pdf2zh-patch/server.py"

# pdf2zh 的 config.json 把 CELERY_RESULT 固化成 null（构建期就被写进镜像层），而
# ConfigManager.get 只要 key 已存在就直接返回它、不再回退到上游传入的默认值，于是
# celery_app.conf.result_backend 是 None，Celery 退化成 DisabledBackend：
# `/v1/translate/<id>` 读 result.state 会抛 AttributeError → Flask 500，前端永远拿
# 不到进度，也就永远拿不到产物。broker 和结果本来就在同一个容器内 Redis 上，这里
# 显式补上。必须在 fork 之前设好——Flask 子进程要用同一个后端读进度。
if not celery_app.conf.result_backend:
    celery_app.conf.result_backend = (
        celery_app.conf.broker_url or "redis://127.0.0.1:6379/0"
    )


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

    The session is built with ONNX Runtime's CPU arena disabled — see the module
    docstring for the measurements. `enable_mem_pattern` only exists to serve the
    arena, so it goes off with it.
    """

    def __init__(self, model_path: str):
        self.model_path = model_path
        options = onnxruntime.SessionOptions()
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        self.model = onnxruntime.InferenceSession(model_path, options)
        metadata = self.model.get_modelmeta().custom_metadata_map
        self._stride = ast.literal_eval(metadata["stride"])
        self._names = ast.literal_eval(metadata["names"])


# 这个进程里唯一一份 doclayout session。父进程从不读它，fork 出来的 task 子进程
# 各自持有一份（子进程退出时随之还给内核，见 --max-tasks-per-child）。
_session: LeanOnnxModel | None = None


class _LazyModelValue:
    """`ModelInstance.value` that builds the session on first read.

    `ModelInstance` is a plain namespace (`class ModelInstance: value = None`),
    so putting a descriptor in that class attribute is enough to defer the load:
    every read goes through `__get__`, which builds the session once and then
    hands back the real `LeanOnnxModel`. Nothing sees a wrapper object, so
    upstream's own attribute and `isinstance` expectations still hold — a proxy
    that forwards attributes would have to reimplement `OnnxModel.stride`'s
    property lookup and would break the moment upstream touched a private one.
    """

    def __get__(self, instance: object, owner: type | None = None) -> LeanOnnxModel:
        global _session
        if _session is None:
            _session = LeanOnnxModel(get_doclayout_onnx_model_path())
        return _session


ModelInstance.value = _LazyModelValue()


def _fork_flask_server() -> int:
    """Fork `server.py` out of this process's already-imported state.

    Forking *before* `celery_app.start()` matters for two reasons: the child
    inherits an interpreter that has only been imported into — no broker
    connection, no Celery pool — and the memory it shares with the parent is
    exactly the 128MB import stack, because nothing else has been allocated yet.

    The import stack does leave native threads behind. Measured in this image via
    `/proc/self/task`: 2 before any import (the sitecustomize chain), 3 after
    `cv2`, 4 after `pdf2zh.backend` — while `threading.enumerate()` still reports
    only `MainThread`. So the child is forked from a process with idle native
    pools, which is what Celery's own prefork pool does for every task child, and
    it does so from a busier state (after `celery_app.start()`) than this one.
    Nothing in the child touches those pools: every module it needs is already in
    `sys.modules`, and from here it only serves HTTP.

    Because of those threads Python 3.12 prints its own DeprecationWarning at the
    `fork()` below — "This process is multi-threaded, use of fork() may lead to
    deadlocks in the child" — once per container start. It is expected here, and
    it is the same fork the pool performs; if a future Python turns it into an
    error, the fallback is the old layout (entrypoint starts `server.py` as a
    second process) and paying for a second copy of the import stack.
    """
    pid = os.fork()
    if pid != 0:
        return pid

    try:
        runpy.run_path(SERVER_SCRIPT, run_name="__main__")
    except BaseException:  # noqa: BLE001 —— 子进程里的任何异常都要留下痕迹再退
        traceback.print_exc()
    finally:
        os._exit(0)


def _exit_when_flask_dies(pid: int) -> None:
    """Take the whole container down when the HTTP side stops.

    The entrypoint only knows about this process now, so keeping the old
    "either role dies, the container restarts" rule is our job. SIGTERM (rather
    than an immediate exit) gives Celery its warm shutdown, which is what stops a
    running task from being cut in half.
    """
    _, status = os.waitpid(pid, 0)
    print(
        f"pdf2zh Flask 子进程退出（status={status}），worker 随之退出以便容器重启",
        file=sys.stderr,
    )
    os.kill(os.getpid(), signal.SIGTERM)


def _worker_argv() -> list[str]:
    concurrency = os.environ.get("PDF2ZH_CELERY_CONCURRENCY", "1")
    # Celery 5.x parses this with click, so it must NOT contain the program name:
    # the first element is the subcommand.
    return [
        "worker",
        "--loglevel=info",
        f"--concurrency={concurrency}",
        # 翻译结束后回收子进程：doclayout session 是在子进程里按需建的（~80MB 匿名
        # 内存），只有子进程退出才会还给内核。代价是每篇翻译多花约 2s 建 session，
        # 而一篇翻译的墙钟时间由逐页 LLM 调用主导。
        "--max-tasks-per-child=1",
        # Single worker, single node: skip the gossip/mingle startup chatter.
        "--without-gossip",
        "--without-mingle",
    ]


if __name__ == "__main__":
    flask_pid = _fork_flask_server()
    threading.Thread(
        target=_exit_when_flask_dies,
        args=(flask_pid,),
        daemon=True,
        name="flask-supervisor",
    ).start()
    celery_app.start(argv=_worker_argv())
