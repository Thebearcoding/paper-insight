#!/usr/bin/env bash
set -euo pipefail

install_dir="${INSTALL_DIR:-/opt/paper-insight}"
domain="${1:?usage: bootstrap-server.sh DOMAIN [ADMIN_EMAIL]}"
admin_email="${2:-admin@athebear.me}"
public_https_port="${PUBLIC_HTTPS_PORT:-443}"
credentials_file="/root/paper-insight-credentials.txt"

if [[ "$public_https_port" == "443" ]]; then
    public_url="https://$domain"
else
    public_url="https://$domain:$public_https_port"
fi

cd "$install_dir"

for target in .env config.yaml "$credentials_file"; do
    if [[ -e "$target" ]]; then
        echo "Refusing to overwrite existing $target" >&2
        exit 1
    fi
done

umask 077
db_password="$(openssl rand -hex 24)"
admin_password="$(openssl rand -hex 16)"
llm_key="$(openssl rand -hex 32)"
zotero_key="$(openssl rand -hex 32)"

env_tmp="$(mktemp)"
config_tmp="$(mktemp)"
credentials_tmp="$(mktemp)"
trap 'rm -f "$env_tmp" "$config_tmp" "$credentials_tmp"' EXIT

cat >"$env_tmp" <<EOF
POSTGRES_DB=paper_online
POSTGRES_USER=paper
POSTGRES_PASSWORD=$db_password
POSTGRES_PORT=5433
PORT=8000
PAPER_DOMAIN=$domain
DOCKERHUB_PREFIX=docker.1ms.run/library/
POSTGRES_IMAGE=docker.1ms.run/library/postgres:16-alpine
CADDY_IMAGE=docker.1ms.run/library/caddy:2-alpine
PYPI_FILES_MIRROR=https://mirrors.aliyun.com/pypi/packages
EOF

cat >"$config_tmp" <<EOF
server:
  host: 0.0.0.0
  port: 8000

database:
  url: postgresql://paper:$db_password@postgres:5432/paper_online

admin:
  email: $admin_email
  initial_password: $admin_password

llm:
  credential_encryption_key: $llm_key
  openai_api_key:
  siliconflow_api_key:
  open_router_api_key:
  step_api_key:
  step_base_url: https://api.stepfun.com/v1
  arkplan_api_key:
  deepseek_api_key:

paths:
  paper_content_cache_dir: data/paper_cache
  zotero_content_cache_dir: data/zotero_cache

zotero:
  credential_encryption_key: $zotero_key
  api_base_url: https://api.zotero.org
  request_timeout_seconds: 30
  max_attachment_mb: 30

auth:
  require_email_verification: false
  session_cookie_name: paper_session
  session_ttl_days: 30
  cookie_secure: true
  cookie_samesite: lax
  password_min_length: 12
  github_client_id:
  github_client_secret:
  github_callback_url: $public_url/auth/github/callback
  frontend_base_url: $public_url

presence:
  online_timeout_seconds: 30
  snapshot_interval_seconds: 60
  retention_days: 90

background_analysis:
  enabled: false
  check_interval_seconds: 86400

hf_daily:
  enabled: false
  api_url: https://huggingface.co/api/daily_papers
  fetch_time: "22:00"
  timezone: Asia/Hong_Kong
  top_n: 5

feishu_notifications:
  enabled: false
  push_time: "10:00"
  max_daily_push_count: 5

cors:
  allowed_origins:
    - $public_url
EOF

cat >"$credentials_tmp" <<EOF
Paper Insight URL: $public_url
Admin email: $admin_email
Admin password: $admin_password
PostgreSQL user: paper
PostgreSQL password: $db_password
LLM encryption key: $llm_key
Zotero encryption key: $zotero_key
Sub2API base URL: https://sub2api.athebear.me/v1
Sub2API API token/model: configure in the Paper Insight admin UI
EOF

install -m 600 "$env_tmp" .env
install -m 600 "$config_tmp" config.yaml
install -m 600 "$credentials_tmp" "$credentials_file"

docker compose -f docker-compose.yml -f docker-compose.personal.yml config --quiet
echo "Created .env, config.yaml, and $credentials_file"
