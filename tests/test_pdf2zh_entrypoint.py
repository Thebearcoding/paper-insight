"""pdf2zh 容器启动拓扑的静态检查。

2026-09-18 起容器里只有一个 Python 进程树：entrypoint 只起 `worker.py`，它 import
完整套依赖后 fork 出 Flask server（`server.py`），再起 Celery pool。这么做的原因是
内存：pdf2zh/babeldoc/onnxruntime/cv2 这一整套 import 在镜像里实测占 128MB 匿名内存，
两个独立进程各自 import 就是两份（Flask 进程实测私有匿名 128MB，容器空转时 cgroup
用量 381MB），
而先 import 再 fork 的话父子共享同一份物理页（fork 出来的空转子进程 PSS 只有 66MB）。

这份共享只在"fork 发生在 import 之后、celery 起之前"时成立，而且需要 worker 真的
回收 task 子进程，否则按需加载的 doclayout session（~80MB）又会常驻下来——这三条都
是运行期才能看出来的事，静态检查在这里兜住。

顺带记一下由此消失的两个 import 期竞争（当年各修过一次，现在结构上不可能发生）：
worker 和 server 同时第一次 import pdf2zh 时，`pdf2zh.cache.init_db()` 的
journal_mode 切换会在库被占用时立刻返回 SQLITE_BUSY（busy_timeout 排在字典后面，
那一刻还没生效），而 `ConfigManager.get_instance()` 一个在写 config.json、另一个在
读，读到半截就抛 json.decoder.JSONDecodeError: Extra data。2026-09-18 在 2GB 服务器
上按生产内存上限各跑 12 次冷启动，当时的修法（entrypoint 里先串行跑一遍完整初始化）
做到 12/12 干净；现在是只有一个 importer，所以那段重型预建（实测 ~200MB RSS、
3s CPU）取消了，冷启动顺序仍由这个文件看着。
"""

from __future__ import annotations

from pathlib import Path

PDF2ZH_DIR = Path(__file__).resolve().parents[1] / "docker" / "pdf2zh"
ENTRYPOINT_SCRIPT = PDF2ZH_DIR / "entrypoint.sh"
WORKER_SCRIPT = PDF2ZH_DIR / "worker.py"


def _script() -> str:
    return ENTRYPOINT_SCRIPT.read_text(encoding="utf-8")


def _worker() -> str:
    return WORKER_SCRIPT.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    script = _script()
    start = script.index(f"{name}() {{")
    end = script.index("\n}\n", start)
    return script[start : end + 3]


def _call_sites(name: str) -> list[str]:
    """行首或缩进的调用点（排除 `name() {` 这个定义行）。"""
    return [
        line
        for line in _script().splitlines()
        if name in line and "() {" not in line
    ]


def test_shared_state_is_initialized_before_the_worker_starts():
    script = _script()

    purge_call = script.index("\npurge_translation_memory\n")
    precreate_call = script.index("\ncreate_translation_memory_db\n")
    start_worker = script.index("python /opt/pdf2zh-patch/worker.py &")

    assert purge_call < precreate_call < start_worker, (
        "翻译记忆库必须在 worker 起来之前由 entrypoint 建出来：worker 及其 fork "
        "出来的进程随后都会连它，而 pdf2zh.cache.init_db() 的 journal_mode 切换"
        "在库被占用时是立刻失败的"
    )


def test_only_the_worker_is_started_and_it_forks_the_server():
    script = _script()

    started = [
        line.strip()
        for line in script.splitlines()
        if line.strip().startswith("python /opt/pdf2zh-patch/")
    ]
    assert started == ["python /opt/pdf2zh-patch/worker.py &"], (
        "entrypoint 只应起 worker.py 一个 pdf2zh 进程：Flask server 由 worker "
        "fork 出来才能共享那 128MB import 堆，独立起一遍就是又一份拷贝"
    )
    assert "python /opt/pdf2zh-patch/server.py" not in script


def test_worker_forks_the_server_before_starting_celery():
    worker = _worker()

    fork_call = worker.index("flask_pid = _fork_flask_server()")
    celery_start = worker.index("celery_app.start(argv=_worker_argv())")

    assert fork_call < celery_start, (
        "Flask 子进程必须在 celery 起来之前 fork：那之后内存里除了 import 堆还多了"
        "连接池和 pool 线程，而且 fork 时机的共享性也依赖这个顺序"
    )
    assert "runpy.run_path(SERVER_SCRIPT" in worker, (
        "server.py 仍然是 Flask 的唯一出处（含绑 0.0.0.0 的原因），子进程里跑它"
    )


def test_worker_defers_the_doclayout_session_to_the_task_child():
    worker = _worker()

    assert "ModelInstance.value = _LazyModelValue()" in worker
    assert "ModelInstance.value = LeanOnnxModel(" not in worker, (
        "import 期就建 doclayout session 会让这 ~80MB 匿名内存常驻整个容器生命周期"
    )
    assert "--max-tasks-per-child=1" in worker, (
        "按需建的 session 只能靠 task 子进程退出还给内核；不回收子进程等于没省"
    )


def test_helpers_run_in_the_foreground_and_never_block_startup():
    name = "create_translation_memory_db"
    body = _function_body(name)
    # 预建失败只应退化成改动前的抢锁行为，不能拦住容器启动
    assert "|| echo" in body, f"{name} 失败时必须只告警，不能拦住启动"

    calls = _call_sites(name)
    assert calls, f"{name} 没有被调用"
    for line in calls:
        # 后台化等于没修：抢锁窗口照样存在
        assert not line.rstrip().endswith("&"), f"{name} 必须前台同步执行：{line!r}"


def test_sqlite_precreate_matches_upstream_pragmas():
    body = _function_body("create_translation_memory_db")

    # pragmas 必须和 pdf2zh.cache.init_db() 一致，否则连接时又会去切 journal mode
    assert "journal_mode=wal" in body
    assert "busy_timeout=1000" in body
    # 表结构留给 pdf2zh 自己建，避免重复 DDL 跟上游漂移
    assert "CREATE TABLE" not in body.upper()
    # 廉价路径：-E 跳过 sitecustomize.py，否则每 30 分钟白吃 84MB
    assert body.index("python -E -") < body.index("import sqlite3")


def test_neither_script_hand_rolls_pdf2zh_shared_state():
    # 注释里可以提这些名字（说明为什么不需要这么做），代码里不许出现
    code = "\n".join(
        line for line in _script().splitlines() if not line.strip().startswith("#")
    )

    # 配置文件必须由 pdf2zh 自己的单例建出来，不要手写 JSON（上游改默认值时
    # 手写的那份会静默漂移）；库的 schema 同理，见上一个用例。
    assert "import pdf2zh" not in code
    assert "ConfigManager" not in code
    assert "json.dump" not in code
    assert "~/.config/PDFMathTranslate" not in code


def test_periodic_cleanup_preserves_live_schema_and_avoids_heavy_imports():
    script = _script()
    loop = script[script.index("while true; do") : script.index("\ndone\n")]

    assert "purge_translation_memory" not in loop
    assert "create_translation_memory_db" not in loop
    assert "python -E /opt/pdf2zh-patch/cache_maintenance.py" in loop
    assert "find /tmp" in loop
    assert "init_pdf2zh_shared_state" not in loop
    # Unlink is safe only before the worker imports/opens the database.
    assert len(_call_sites("purge_translation_memory")) == 1
    assert len(_call_sites("create_translation_memory_db")) == 1
    dockerfile = (PDF2ZH_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert "docker/pdf2zh/cache_maintenance.py" in dockerfile
