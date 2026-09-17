#!/bin/sh
# pdf2zh container health: Redis answers, Flask accepts connections, and the
# Celery worker process is still alive. The entrypoint exits the container as
# soon as the worker or the server dies, so a stale worker cannot masquerade as
# a healthy container while jobs sit in `pending` forever.
set -eu

redis-cli ping | grep -q PONG

python - <<'PY'
import socket

socket.create_connection(("127.0.0.1", 11008), 5).close()
PY

python - <<'PY'
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
