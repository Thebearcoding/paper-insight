#!/usr/bin/env bash
set -euo pipefail

domain="${1:?usage: configure-sub2api.sh DOMAIN [SUB2API_BASE_URL]}"
sub2api_base_url="${2:-https://sub2api.athebear.me/v1}"
credentials_file="${CREDENTIALS_FILE:-/root/paper-insight-credentials.txt}"
admin_email="$(sed -n 's/^Admin email: //p' "$credentials_file")"
admin_password="$(sed -n 's/^Admin password: //p' "$credentials_file")"

cookie_jar="$(mktemp)"
response_body="$(mktemp)"
trap 'rm -f "$cookie_jar" "$response_body"' EXIT

base_url="https://$domain"
resolve_target="$domain:443:127.0.0.1"
login_payload="$(printf '{"email":"%s","password":"%s"}' "$admin_email" "$admin_password")"

login_status="$(curl --silent --show-error --resolve "$resolve_target" \
    --cookie-jar "$cookie_jar" --output "$response_body" --write-out '%{http_code}' \
    --header 'Content-Type: application/json' --data "$login_payload" \
    "$base_url/auth/login")"
[[ "$login_status" == 200 ]]

providers_status="$(curl --silent --show-error --resolve "$resolve_target" \
    --cookie "$cookie_jar" --output "$response_body" --write-out '%{http_code}' \
    "$base_url/admin/llm/providers")"
[[ "$providers_status" == 200 ]]

if grep -q '"name":"Sub2API"' "$response_body"; then
    echo "sub2api_provider=existing"
    exit 0
fi

create_payload="$(printf '{"name":"Sub2API","base_url":"%s","api_key":null,"models":[],"active_model":null}' "$sub2api_base_url")"
create_status="$(curl --silent --show-error --resolve "$resolve_target" \
    --cookie "$cookie_jar" --output "$response_body" --write-out '%{http_code}' \
    --header 'Content-Type: application/json' --data "$create_payload" \
    "$base_url/admin/llm/providers")"

printf 'sub2api_provider=create_http_%s\n' "$create_status"
[[ "$create_status" == 200 ]]
