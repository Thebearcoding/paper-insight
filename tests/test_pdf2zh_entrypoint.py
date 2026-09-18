"""pdf2zh 容器 entrypoint 的启动顺序静态检查。

worker 和 server 会在同一瞬间 import pdf2zh，而 pdf2zh 的 import 阶段会初始化两处
共享状态，两边都是"不存在就建"，于是两个进程抢着建同一份东西：

- pdf2zh.cache 在 import 阶段调用 init_db()，pragmas 是
  {journal_mode: wal, busy_timeout: 1000}。peewee 按字典顺序生效，而 SQLite 对
  journal mode 切换在库被别的连接占用时是立刻返回 SQLITE_BUSY、不走 busy handler
  的（那个 busy_timeout 排在后面，那一刻还没生效）。entrypoint 又刚好在启动时把
  这个库删掉，输的进程带着 "sqlite3.OperationalError: database is locked" 退出。
- pdf2zh.config.ConfigManager 是单例，__init__ 里"文件不存在就写默认配置，存在就
  json.load"。一个进程在写、另一个在读时，读的会拿到半截文件并抛
  json.decoder.JSONDecodeError: Extra data。

任一进程退出，supervisor 就退出整个容器交给 compose 重启。2026-09 上线首启崩的
就是第一种。2026-09-18 在 2GB 服务器上按生产内存上限（900m）各跑 12 次冷启动：
改动前 5/12 干净（6 次 database is locked、1 次配置竞争），改动后 12/12 干净。
这类错误只有真的重建容器才跑得出来，CI 里没有容器，所以用静态检查兜住顺序。
"""

from __future__ import annotations

from pathlib import Path

ENTRYPOINT_SCRIPT = (
    Path(__file__).resolve().parents[1] / "docker" / "pdf2zh" / "entrypoint.sh"
)


def _script() -> str:
    return ENTRYPOINT_SCRIPT.read_text(encoding="utf-8")


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


def test_shared_state_is_initialized_before_worker_and_server():
    script = _script()

    purge_call = script.index("\npurge_translation_memory\n")
    precreate_call = script.index("\ncreate_translation_memory_db\n")
    init_call = script.index("\ninit_pdf2zh_shared_state\n")
    start_worker = script.index("python /opt/pdf2zh-patch/worker.py &")
    start_server = script.index("python /opt/pdf2zh-patch/server.py &")

    assert purge_call < precreate_call < init_call < start_worker < start_server, (
        "pdf2zh 的 import 期共享状态必须在 worker/server 起来之前由 entrypoint "
        "串行建出来，否则两个进程会抢着建同一份文件并崩掉一个"
    )


def test_helpers_run_in_the_foreground_and_never_block_startup():
    for name in ("create_translation_memory_db", "init_pdf2zh_shared_state"):
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


def test_pdf2zh_config_is_created_by_upstream_code_not_hand_rolled():
    body = _function_body("init_pdf2zh_shared_state")
    # 注释里可以提这个路径，但不许在代码里手写它
    code = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("#")
    )

    assert "ConfigManager.get_instance()" in code, (
        "配置文件必须由 pdf2zh 自己的单例建出来，不要手写 JSON"
    )
    assert "json.dump" not in code, (
        "不要自己拼配置内容：上游改默认值时手写的那份会静默漂移"
    )
    assert "~/.config/PDFMathTranslate" not in code


def test_periodic_purge_only_uses_the_cheap_precreate():
    script = _script()
    loop = script[script.index("while true; do") : script.index("\ndone\n")]

    purge = loop.index("purge_translation_memory")
    precreate = loop.index("create_translation_memory_db")
    tmp_cleanup = loop.index("find /tmp")

    assert purge < precreate < tmp_cleanup, (
        "定期清理同样会删掉库，而 worker/server 之后是懒连接"
        "（celery 每个任务、Flask 每个请求都可能新建连接），"
        "所以每轮清理后都要重新预建，否则会重演同一个竞争"
    )
    # 定期路径只删 sqlite 库、不删配置文件，用廉价的预建就够；
    # 每 30 分钟再起一个重型 import 进程在 2GB 机器上要省着点。
    assert "init_pdf2zh_shared_state" not in loop
