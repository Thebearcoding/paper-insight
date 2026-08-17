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

## Automatic deployment from GitHub Actions

The `Deploy production` CI job runs only for pushes to `master` in the
`Thebearcoding/paper-insight` repository, and only after the backend and frontend jobs
pass. It uploads the exact tested commit over SSH, builds it in an isolated
release directory, preserves `/opt/paper-insight/.env` and `config.yaml`, and
switches the Compose project after the build succeeds.

Install `deploy-release.sh` as `/usr/local/sbin/deploy-paper-insight` and add a
dedicated SSH public key with a forced command. Do not reuse a personal SSH key:

```text
restrict,command="/usr/local/sbin/deploy-paper-insight" ssh-ed25519 ... github-actions-paper-insight
```

Create a GitHub environment named `production` with these values:

- secrets: `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`
- variables: `DEPLOY_HOST`, `DEPLOY_USER`

The forced command permits only `deploy <commit-sha>` and `status`. Releases are
stored under `/opt/paper-insight/releases`, while the existing root deployment
remains available as the first rollback target.

For mainland China servers that cannot reach Docker Hub directly, the bundled
`docker-daemon.json` provides a reachable registry mirror. Install it only
when `/etc/docker/daemon.json` is not already managed by the server operator.
The personal Compose overlay can also rewrite locked Python package artifact
URLs to the configured `PYPI_FILES_MIRROR` without changing package versions or
hash verification.
