# Paper Insight + Sub2API server deployment

This deployment keeps Paper Insight and Sub2API in separate Compose projects, while attaching the Paper Insight application container to Sub2API's existing private Docker network. PostgreSQL and application ports are bound to loopback and Caddy is the only public HTTP entry point.

## Capacity prerequisite

Do not start this stack on the current `Standard_B2ats_v2` VM (2 vCPU / 1 GiB). The most direct resize options in the same Basv2 family are:

| SKU | vCPU | RAM | Japan West Linux pay-as-you-go compute estimate* | Recommendation |
| --- | ---: | ---: | ---: | --- |
| `Standard_B2als_v2` | 2 | 4 GiB | about USD 35.77/month | Minimum for light personal use |
| `Standard_B2as_v2` | 2 | 8 GiB | about USD 71.54/month | Recommended for smooth PDF reading and concurrent Sub2API traffic |

\* Checked against the Azure Retail Prices API on August 10, 2026 using 730 hours/month. The estimate excludes managed disks, bandwidth, tax, discounts, reservations, and savings plans. The current 1 GiB SKU is about USD 8.98/month for compute, so the approximate compute increase is USD 26.79/month for 4 GiB or USD 62.56/month for 8 GiB.

Basv2 is burstable. If this server later has sustained CPU-heavy PDF/OCR processing or many simultaneous users, move to a non-burstable general-purpose SKU instead of relying on CPU credits.

The target VM reports these Azure identifiers:

```text
Resource group: AUZRE_GROUP
VM name: Auzre
Region: japanwest
Current size: Standard_B2ats_v2
```

Resizing changes billing and restarts the VM, so perform it from Azure Portal or an authenticated Azure Cloud Shell after choosing the target size. In Cloud Shell, first confirm the size is offered to this VM:

```bash
az vm list-vm-resize-options \
  --resource-group AUZRE_GROUP \
  --name Auzre \
  --query "[?name=='Standard_B2als_v2' || name=='Standard_B2as_v2'].name" \
  --output table
```

Then resize to the selected SKU, for example the recommended 8 GiB option:

```bash
az vm resize \
  --resource-group AUZRE_GROUP \
  --name Auzre \
  --size Standard_B2as_v2
```

After the VM returns, verify `free -h`, `docker ps`, and the Sub2API health state before starting Paper Insight.

## 1. Prepare configuration

```bash
cp .env.server.example .env
cp config.server.yaml.example config.yaml
openssl rand -hex 32
openssl rand -base64 36
```

Put the hexadecimal value in `zotero.credential_encryption_key`. Use a separate random value for `POSTGRES_PASSWORD`, and put the same PostgreSQL password in `config.yaml`'s database URL. Replace the admin email, admin password, public domain, callback URL, and CORS origin.

The existing Sub2API network on the target server is:

```text
sub2api-deploy_sub2api-network
```

Keep that value in `.env` unless the Sub2API Compose project is renamed.

## 2. Start Paper Insight

```bash
docker compose -f docker-compose.yml -f docker-compose.server.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.server.yml ps
curl --fail http://127.0.0.1:8000/healthz
```

Database migrations run automatically when the application container starts.

To validate all migrations against a running PostgreSQL container without touching its application database, copy the repository to the server and run:

```bash
POSTGRES_CONTAINER=sub2api-postgres sh scripts/validate_postgres_migrations.sh db/migrations
```

The script creates a uniquely named temporary database, applies every migration, reports the resulting table counts, and drops the temporary database on exit.

## 3. Add the Caddy site

Copy `deploy/server/Caddyfile.example` into the server Caddyfile, replace `paper.example.com`, create the corresponding DNS record, then validate and reload Caddy.

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Do not expose ports 8000 or 5433 in the cloud firewall. They bind to `127.0.0.1` and are intended only for Caddy and local maintenance.

## 4. Connect Paper Insight to Sub2API

Sign in as the Paper Insight administrator and add an OpenAI-compatible LLM provider with:

```text
Name: Sub2API
Base URL: http://sub2api:8080/v1
API Key: a valid Sub2API user token
Model: a model available through that Sub2API account
```

The hostname `sub2api` resolves through the shared private Docker network. This avoids routing internal LLM traffic through the public domain and Caddy.

Sub2API's published port 8080 should also be bound to `127.0.0.1` instead of `0.0.0.0`, so external clients cannot bypass HTTPS and Caddy.

## 5. Verify Zotero

Open `/zotero`, create a Zotero key with personal-library read access only, connect it, and start a sync. Metadata is synchronized incrementally. PDF text is downloaded only when a paper is opened for deep reading and is cached in the `app_data` volume.

Useful checks:

```bash
docker compose -f docker-compose.yml -f docker-compose.server.yml logs --tail=200 app
docker stats --no-stream
curl --fail https://paper.example.com/healthz
```
