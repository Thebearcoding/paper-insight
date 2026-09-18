# Personal server deployment

This overlay is tuned for a small 2-vCPU, 2-GB server. It adds conservative
PostgreSQL settings, service memory limits, and Caddy-managed HTTPS.

```bash
docker compose -f docker-compose.yml -f docker-compose.personal.yml up -d --build
```

Set `PAPER_DOMAIN` and the database variables in `.env`, and create
`config.yaml` from `config.server.yaml.example`. The application stays bound to
localhost while Caddy publishes ports 80 and 443.

To route every outbound HTTP(S) request from the application through one HTTP
proxy, set `OUTBOUND_PROXY_URL` in `.env`. Keep `OUTBOUND_NO_PROXY` populated
with the bundled internal service names so PostgreSQL, Typesense, and other
Docker-network traffic remain direct. Both uppercase and lowercase proxy
variables are injected for compatibility with the application's HTTP clients.

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

Install `deploy-entrypoint.sh` as `/usr/local/sbin/deploy-paper-insight` and add a
dedicated SSH public key with a forced command. Do not reuse a personal SSH key:

```text
restrict,command="/usr/local/sbin/deploy-paper-insight" ssh-ed25519 ... github-actions-paper-insight
```

The installed entry point is deliberately thin: it validates the command, extracts
the uploaded archive into `/opt/paper-insight/releases/<sha>`, and then hands over
to that release's own `deploy/personal/deploy-release.sh`. The deployment logic
therefore ships with every release, so changes to it (build steps, activation,
rollback) take effect on the next deploy without touching the server. Only a change
to the entry point's own contract — new verbs, a different upload protocol — requires
reinstalling it; when that happens, remember the installed copy is stale until you
do, which is exactly how the first pdf2zh rollout failed.

The entry point takes the verb either from `SSH_ORIGINAL_COMMAND` (the SSH forced
command CI uses) or from its own arguments, so it is also usable by hand:

```bash
/usr/local/sbin/deploy-paper-insight status
```

`deploy` reads the release archive from stdin and refuses to run in a terminal; it
is meant to be driven as `git archive --format=tar.gz HEAD | ssh ... deploy <sha>`.

Exactly one file is authoritative: whichever path the deploy key's forced command
names. Read it from the server instead of assuming the documented default, because a
stale copy at some other path looks identical from the outside:

```bash
grep -o 'command="[^"]*"' /root/.ssh/authorized_keys
```

Every invocation logs its own digest on the first line, so the deploy log names the
copy that actually ran:

```text
[paper-insight-deploy] entrypoint sha256=87b33f08bf19… verb=deploy
```

A `sha256` that does not match the repository means the file at the forced-command
path is stale. After installing or replacing it, confirm it is the copy you intended:

```bash
sha256sum "$(grep -o 'command="[^"]*"' /root/.ssh/authorized_keys | sed 's/command="//;s/"$//')"
curl -fsSL https://raw.githubusercontent.com/Thebearcoding/paper-insight/master/deploy/personal/deploy-entrypoint.sh | sha256sum
```

Both digests must match. If you copied the file from a Windows checkout, strip the
CRLF endings first (`sed -i 's/\r$//'`) or the shebang will not resolve.

Do not move deployment logic into the installed entry point: `tests/test_deploy_compose_wiring.py`
asserts that it stays a dispatcher (no `docker compose` calls, and the release script
keeps the tarball extraction out of its own hands).

Create a GitHub environment named `production` with these values:

- secrets: `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`
- variables: `DEPLOY_HOST`, `DEPLOY_USER`

The forced command permits only `deploy <commit-sha>` and `status`. Releases are
stored under `/opt/paper-insight/releases`, while the existing root deployment
remains available as the first rollback target.

For mainland China servers that cannot reach Docker Hub directly, the bundled
`docker-daemon.json` provides a reachable registry mirror. Install it only
when `/etc/docker/daemon.json` is not already managed by the server operator.

Build-time downloads are the other China-specific hazard. The personal Compose
overlay rewrites locked Python package artifact URLs to the configured
`PYPI_FILES_MIRROR` without changing package versions or hash verification, and
passes three optional args to the `pdf2zh` image build:

- `DEBIAN_MIRROR` — base for the apt sources (`https://mirrors.aliyun.com`)
- `PYPI_INDEX_URL` — `pip -i` index (`https://mirrors.aliyun.com/pypi/simple`);
  note this is not the same thing as `PYPI_FILES_MIRROR`, which is a file-download base
- `HF_ENDPOINT` — for the doclayout model fetch (`https://hf-mirror.com`)

All three default to empty, which reproduces the international behaviour exactly.
Without them an apt + pip build of the pdf2zh image runs at tens of KB/s from a
mainland server and overruns the deploy job's 60-minute timeout.
