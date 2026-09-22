#!/bin/sh
# pdf2zh 后端：容器内 Redis + 一个进程树（worker.py import 完成后 fork 出 Flask API
# 11008，再起 Celery pool）。
#
# 为什么是"一个进程树"而不是两个独立进程：worker/server 都要 import pdf2zh 那一整套
# 依赖，而这套 import 在镜像里实测占 128MB 匿名内存。各自 import 就是两份（实测
# Flask 进程私有匿名 128MB，容器空转时 cgroup 用量 381MB）；先 import 再 fork 的话
# 父子共享同一份物理页（实测 fork 出来的空转子进程 PSS 只有 66MB）。2026-09-18 之前
# 这里是两个独立进程，那条 128MB 的重复拷贝就是这台 2GB 机器上最大的一块可回收内存。
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
# （sqlite 翻译记忆）。启动前可以删库；运行中每 30 分钟只 DELETE 缓存行，
# 保留表结构、文件和 WAL。上游只在 import 时建表，删掉活跃库后仅预建空库会
# 导致后续任务不断报 no such table: _translationcache，永远无法生成 PDF。
# 清空后的页由 SQLite 复用。顺带清掉 /tmp 里 6 小时以上的残留工作目录。
purge_translation_memory() {
    rm -f /root/.cache/pdf2zh/cache.v1.db* \
          /root/.cache/babeldoc/cache.v1.db* 2>/dev/null || true
}

# 翻译记忆库（~/.cache/pdf2zh/cache.v1.db）必须在启动时建出来，否则第一次用它的
# 进程会去建：pdf2zh.cache.init_db() 的 pragmas 是 {journal_mode: wal,
# busy_timeout: 1000}——peewee 按字典顺序生效，journal_mode 排在 busy_timeout
# 前面，而 SQLite 对 journal mode 切换在库被别的连接占用时是**立刻**返回
# SQLITE_BUSY、根本不走 busy handler 的（所以那个 busy_timeout 那一刻还没生效，
# 等它生效也救不了这一步）。上面刚把库删掉，下面这些进程随后都会连它，谁先连谁建。
#
# 这里先在前台把库建出来并置成 WAL：worker 和它 fork 出来的 Flask 子进程、以及
# 之后每个 task 子进程连接时看到的都是「文件已存在且已是 WAL」，journal_mode 变成
# no-op；剩下建表那点写锁冲突由 init_db 里已生效的 busy_timeout 兜住。表结构留给
# pdf2zh 自己建，这里不重复 DDL（重复的 schema 会跟上游漂移）。预建失败不拦启动：
# 那只是退化成改动前的抢锁行为，多数情况下能自愈。
#
# `-E` 只用到 sqlite3，却正好跳过 PYTHONPATH 上的 sitecustomize.py——它会先
# import httpx + numpy + openai，让这个轻量初始化进程白吃 84MB 和
# 约 3 秒 CPU（见 healthcheck.sh 里同样的处理）。
create_translation_memory_db() {
    python -E - <<'PY' || echo "警告：翻译记忆库预建失败，worker 将自行建库" >&2
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

# 2026-09-18 起这里少了一个函数：以前还要在前台把 pdf2zh 的 import 期共享状态
# 整套跑一遍（import pdf2zh.cache 建库 + ConfigManager.get_instance() 建配置文件），
# 因为 worker 和 server 两个进程会同时第一次 import，一个在写、另一个在读：
# 读的会拿到写了一半的 config.json 并抛 json.decoder.JSONDecodeError: Extra data，
# 或者抢 sqlite 库的 journal mode 拿到 database is locked，带着错误退出。
# 现在 server 不是独立进程了——worker.py import 完之后 fork 出它，全容器只有一个
# 进程会跑 import 期那段初始化，竞争结构上就不可能发生，所以那个"先串行跑一遍
# 完整初始化"的重型进程（实测 ~200MB RSS、3s CPU）在启动路径上取消了。库仍然要
# 预建：启动时的 purge 把它删掉了，而 worker 及其 fork 出来的进程之后都是懒连接。
purge_translation_memory
create_translation_memory_db
(
    while true; do
        sleep 1800
        # 只清行，不删活跃库：已 import 的 worker 不会重新执行上游建表逻辑。
        # 碰到写锁则跳过本轮，避免清理任务阻塞翻译。
        python -E /opt/pdf2zh-patch/cache_maintenance.py || true
        find /tmp -mindepth 1 -maxdepth 1 -mmin +360 -exec rm -rf {} + 2>/dev/null || true
    done
) &

# 只起这一个进程：它 import 完整套依赖后 fork 出 Flask server，再起 Celery pool，
# 三个角色共享同一份已 import 的堆（见文件头的实测数字）。worker 自己盯着 Flask
# 子进程，所以这里只管 worker 一个 pid。
python /opt/pdf2zh-patch/worker.py &
worker_pid=$!

# 任一角色退出（OOM/崩溃）就让容器一起退出，交给 compose 的 restart 策略拉起，
# 否则 API 挂了而 worker 还活着（或者反过来），任务会永远停在 pending。
trap 'kill "$worker_pid" 2>/dev/null || true; exit 0' TERM INT
while kill -0 "$worker_pid" 2>/dev/null; do
    sleep 5
done

echo "pdf2zh worker 退出，容器随之退出以便重启" >&2
exit 1
