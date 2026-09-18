#!/bin/sh
# pdf2zh container health: Redis answers, Flask accepts connections, and the
# Celery worker process is still alive. The entrypoint exits the container as
# soon as the worker or the server dies, so a stale worker cannot masquerade as
# a healthy container while jobs sit in `pending` forever.
#
# The two Python snippets below run with `-E`, which ignores PYTHONPATH and
# therefore skips `/opt/pdf2zh-patch/sitecustomize.py`. That matters more than it
# looks: sitecustomize imports httpx + numpy + openai, so a plain `python
# -c pass` in this image costs 84 MB RSS and ~3 s of CPU, and this healthcheck
# runs every 30 s (twice, once per snippet). The snippets only use socket and
# /proc, so none of those libraries are needed — with `-E` each startup is 8 MB
# and the whole healthcheck drops from ~6 s to well under a second.
set -eu

redis-cli ping | grep -q PONG

python -E - <<'PY'
import socket

socket.create_connection(("127.0.0.1", 11008), 5).close()
PY

python -E - <<'PY'
import os
import sys

# No procps in the image, so scan /proc for the worker launcher ourselves.
for entry in os.listdir("/proc"):
    if not entry.isdigit():
        continue
    try:
        with open("/proc/" + entry + "/cmdline", "rb") as handle:
            cmdline = handle.read().decode("utf-8", "replace")
    except OSError:
        continue
    if "worker.py" in cmdline:
        sys.exit(0)
sys.exit(1)
PY
