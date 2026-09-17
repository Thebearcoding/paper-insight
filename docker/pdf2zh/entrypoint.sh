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

purge_translation_memory
(
    while true; do
        sleep 1800
        purge_translation_memory
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
