#!/bin/sh
# pdf2zh 后端：容器内 Redis + Celery worker + Flask API(11008)
set -eu

# 翻译结果只放在 Redis 里，所以关掉 RDB/AOF 持久化（不碰磁盘），并限制内存：
# 旧结果被淘汰后 app 侧会把它标记成 expired，用户重新翻译即可。Celery 另有
# 默认 result_expires(24h)，结果最多留一天。
#
# 淘汰策略用 volatile-lru 而不是 allkeys-lru：只有带 TTL 的 key 会被淘汰，
# 也就是 Celery 存在这里的结果；broker 的队列 key 没有 TTL，用 allkeys-lru
# 时可能被一篇大论文的结果挤掉，任务会永远停在 pending。
redis-server --daemonize yes \
    --save '' \
    --appendonly no \
    --dir /tmp \
    --maxmemory "${PDF2ZH_REDIS_MAXMEMORY:-512mb}" \
    --maxmemory-policy volatile-lru

# pdf2zh/babeldoc 会把每句原文+译文写进 ~/.cache/{pdf2zh,babeldoc}/cache.v1.db
# （sqlite 翻译记忆）。那是服务器磁盘，会随翻译篇数一直涨，而用户明确要求产物
# 只留在用户本机，所以启动时清一次、之后每 30 分钟清一次：它只是省 token 的
# 缓存，删掉不影响正确性。顺带清掉 /tmp 里 6 小时以上的残留工作目录。
purge_translation_memory() {
    rm -f /root/.cache/pdf2zh/cache.v1.db* \
          /root/.cache/babeldoc/cache.v1.db* 2>/dev/null || true
}

# 翻译记忆库（~/.cache/pdf2zh/cache.v1.db）必须在启动时串行建出来，否则冷启动
# 必然抢锁：worker 和 server 都会在 import pdf2zh 时执行 pdf2zh.cache.init_db()，
# 而 init_db 的 pragmas 是 {journal_mode: wal, busy_timeout: 1000}——peewee 按
# 字典顺序生效，journal_mode 排在 busy_timeout 前面，而 SQLite 对 journal mode
# 切换在库被别的连接占用时是**立刻**返回 SQLITE_BUSY、根本不走 busy handler 的
# （所以那个 busy_timeout 那一刻还没生效，等它生效也救不了这一步）。上面刚把库
# 删掉，两个进程同时起来就会抢着建同一个文件，输的那个 server 进程带着
# "sqlite3.OperationalError: database is locked" 退出，supervisor 随即退出整个
# 容器交给 compose 重启（2026-09 上线首启就崩了一次，每次重建容器都会重演）。
#
# 所以删完之后立刻在前台把库建出来并置成 WAL：worker/server 随后连接时看到的
# 都是「文件已存在且已是 WAL」，journal_mode 变成 no-op；剩下建表那点写锁冲突由
# init_db 里已生效的 busy_timeout 兜住。表结构留给 pdf2zh 自己建，这里不重复 DDL
# （重复的 schema 会跟上游漂移）。预建失败不拦启动：那只是退化成改动前的抢锁
# 行为，多数情况下能自愈。
create_translation_memory_db() {
    python - <<'PY' || echo "警告：翻译记忆库预建失败，worker/server 将自行建库" >&2
import os
import sqlite3

path = os.path.join(os.path.expanduser("~"), ".cache", "pdf2zh", "cache.v1.db")
os.makedirs(os.path.dirname(path), exist_ok=True)
connection = sqlite3.connect(path, timeout=30)
# 与 pdf2zh.cache.init_db() 的 pragmas 保持一致
connection.execute("PRAGMA journal_mode=wal")
connection.execute("PRAGMA busy_timeout=1000")
connection.close()
PY
}

# import 期的共享状态不止 sqlite 库一个：pdf2zh.config.ConfigManager 是单例，
# __init__ 里做的是"文件不存在就写一份默认配置，存在就 json.load"，
# 路径是 ~/.config/PDFMathTranslate/config.json。worker/server 同时第一次 import
# 时，一个在写、另一个在读，读的那个会拿到写了一半的文件并抛
# json.decoder.JSONDecodeError: Extra data → 进程退出 → 容器退出重启
# （实测 8 次冷启动里中过 1 次，和上面那个 sqlite 竞争是同一种病）。
#
# 所以启动时先在前台把 pdf2zh 的初始化整套跑一遍，两个进程随后的 import 看到的
# 都是"已存在"，竞争窗口就没有了。这里不自己拼配置内容，直接调用上游代码，
# 避免跟上游的默认值漂移。
#
# 只放在启动路径：定期清理只删 sqlite 库、不删配置文件，那条路径用上面那个廉价
# 的预建就够了——没必要每 30 分钟在 2GB 机器上再起一个重型进程。
init_pdf2zh_shared_state() {
    python - <<'PY' || echo "警告：pdf2zh 共享状态预建失败，worker/server 将自行竞争" >&2
import pdf2zh.cache  # noqa: F401  —— 模块 import 阶段就会 init_db()
import pdf2zh.backend  # noqa: F401  —— server.py 走的那条 import 链
from pdf2zh.config import ConfigManager

# 串行创建配置文件：路径和默认内容都由上游单例决定，这里不重复
ConfigManager.get_instance()
PY
}

purge_translation_memory
create_translation_memory_db
init_pdf2zh_shared_state
(
    while true; do
        sleep 1800
        purge_translation_memory
        # 定期清理同样会把库删掉，而 worker/server 之后是懒连接（celery 每个任务、
        # Flask 每个请求都可能新建连接），两个进程同时新建连接就会重演同一个竞争。
        create_translation_memory_db
        find /tmp -mindepth 1 -maxdepth 1 -mmin +360 -exec rm -rf {} + 2>/dev/null || true
    done
) &

# celery 并发固定 1：单篇翻译内部已按 thread 并行调 LLM，worker 多开会重复占用
# 内存里那份 doclayout 模型（小服务器容易 OOM）。
python /opt/pdf2zh-patch/worker.py &
worker_pid=$!
python /opt/pdf2zh-patch/server.py &
server_pid=$!

# 任一进程退出（OOM/崩溃）就让容器一起退出，交给 compose 的 restart 策略拉起，
# 否则 worker 死了容器还活着，任务会永远停在 pending。
trap 'kill "$worker_pid" "$server_pid" 2>/dev/null || true; exit 0' TERM INT
while kill -0 "$worker_pid" 2>/dev/null && kill -0 "$server_pid" 2>/dev/null; do
    sleep 5
done

echo "pdf2zh worker/server 退出，容器随之退出以便重启" >&2
exit 1
