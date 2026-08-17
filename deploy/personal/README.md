# Personal server deployment

This overlay is tuned for a small 2-vCPU, 2-GB server. It adds conservative
PostgreSQL settings, service memory limits, and Caddy-managed HTTPS.

```bash
docker compose -f docker-compose.yml -f docker-compose.personal.yml up -d --build
```

Set `PAPER_DOMAIN` and the database variables in `.env`, and create
`config.yaml` from `config.server.yaml.example`. The application stays bound to
localhost while Caddy publishes ports 80 and 443.

On a fresh server checkout, the bootstrap helper can generate the secret files
without printing their values:

```bash
bash deploy/personal/bootstrap-server.sh paper.example.com admin@example.com
```

The generated credentials are stored in `/root/paper-insight-credentials.txt`
with mode `0600`.

For mainland China servers that cannot reach Docker Hub directly, the bundled
`docker-daemon.json` provides a reachable registry mirror. Install it only
when `/etc/docker/daemon.json` is not already managed by the server operator.
The personal Compose overlay can also rewrite locked Python package artifact
URLs to the configured `PYPI_FILES_MIRROR` without changing package versions or
hash verification.
