"""Serve the pdf2zh HTTP API on every interface.

Upstream `pdf2zh --flask` calls `flask_app.run(port=11008)` without a host, so
Flask binds 127.0.0.1 and the app container (a different container, reaching
this one over the compose network) gets connection refused. Binding 0.0.0.0 is
the only change: Flask's built-in server is threaded by default, so a download
can stream while the frontend polls task progress.
"""

from pdf2zh.backend import flask_app

PORT = 11008

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=PORT)
