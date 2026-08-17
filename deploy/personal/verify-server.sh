#!/usr/bin/env bash
set -euo pipefail

domain="${1:?usage: verify-server.sh DOMAIN}"
credentials_file="${CREDENTIALS_FILE:-/root/paper-insight-credentials.txt}"
admin_email="$(sed -n 's/^Admin email: //p' "$credentials_file")"
admin_password="$(sed -n 's/^Admin password: //p' "$credentials_file")"

if [[ -z "$admin_email" || -z "$admin_password" ]]; then
    echo "Could not read admin credentials" >&2
    exit 1
fi

cookie_jar="$(mktemp)"
response_body="$(mktemp)"
trap 'rm -f "$cookie_jar" "$response_body"' EXIT

base_url="https://$domain"
resolve_target="$domain:443:127.0.0.1"
payload="$(printf '{"email":"%s","password":"%s"}' "$admin_email" "$admin_password")"

login_status="$(curl --silent --show-error --resolve "$resolve_target" \
    --cookie-jar "$cookie_jar" --output "$response_body" --write-out '%{http_code}' \
    --header 'Content-Type: application/json' --data "$payload" \
    "$base_url/auth/login")"

me_status="$(curl --silent --show-error --resolve "$resolve_target" \
    --cookie "$cookie_jar" --output "$response_body" --write-out '%{http_code}' \
    "$base_url/auth/me")"

zotero_status="$(curl --silent --show-error --resolve "$resolve_target" \
    --cookie "$cookie_jar" --output "$response_body" --write-out '%{http_code}' \
    "$base_url/me/zotero/connection")"

zotero_page_status="$(curl --silent --show-error --resolve "$resolve_target" \
    --output "$response_body" --write-out '%{http_code}' \
    "$base_url/zotero")"

printf 'login=%s me=%s zotero_api=%s zotero_page=%s\n' \
    "$login_status" "$me_status" "$zotero_status" "$zotero_page_status"

[[ "$login_status" == 200 && "$me_status" == 200 && "$zotero_status" == 200 && "$zotero_page_status" == 200 ]]
