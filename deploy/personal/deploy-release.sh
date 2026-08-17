#!/bin/sh
set -eu
set -f

deploy_root=/opt/paper-insight
release_root="$deploy_root/releases"
compose_project=paper-insight

log() {
    printf '[paper-insight-deploy] %s\n' "$*"
}

fail() {
    log "ERROR: $*" >&2
    exit 1
}

compose_for() {
    release_dir=$1
    shift
    docker compose \
        --env-file "$deploy_root/.env" \
        --project-name "$compose_project" \
        -f "$release_dir/docker-compose.yml" \
        -f "$release_dir/docker-compose.personal.yml" \
        "$@"
}

set_release_image() {
    release_dir=$1
    if [ -f "$release_dir/.paper-insight-image" ]; then
        PAPER_INSIGHT_IMAGE=$(cat "$release_dir/.paper-insight-image")
        export PAPER_INSIGHT_IMAGE
    else
        unset PAPER_INSIGHT_IMAGE || true
    fi
}

rollback_to() {
    previous_dir=$1
    if [ -z "$previous_dir" ] || [ ! -d "$previous_dir" ]; then
        log "No previous release is available for automatic rollback"
        return 0
    fi

    log "Rolling back to $previous_dir"
    set_release_image "$previous_dir"
    if [ -f "$previous_dir/.paper-insight-image" ]; then
        compose_for "$previous_dir" up -d --no-build --wait --wait-timeout 300
    else
        compose_for "$previous_dir" up -d --build --wait --wait-timeout 300
    fi
}

show_status() {
    active_dir=$(readlink -f "$deploy_root/current" 2>/dev/null || true)
    if [ -z "$active_dir" ] || [ ! -d "$active_dir" ]; then
        active_dir=$deploy_root
    fi
    set_release_image "$active_dir"
    compose_for "$active_dir" ps
    curl --fail --silent --show-error http://127.0.0.1:8000/healthz >/dev/null
    log "Production health check passed"
}

original_command=${SSH_ORIGINAL_COMMAND:-}
set -- $original_command

case "${1:-}" in
    status)
        [ "$#" -eq 1 ] || fail "status does not accept arguments"
        show_status
        ;;
    deploy)
        [ "$#" -eq 2 ] || fail "usage: deploy <40-character-commit-sha>"
        commit_sha=$2
        [ "${#commit_sha}" -eq 40 ] || fail "invalid commit SHA length"
        case "$commit_sha" in
            *[!0-9a-f]*) fail "invalid commit SHA" ;;
        esac

        [ -r "$deploy_root/.env" ] || fail "$deploy_root/.env is missing"
        [ -r "$deploy_root/config.yaml" ] || fail "$deploy_root/config.yaml is missing"

        release_dir="$release_root/$commit_sha"
        previous_dir=$(readlink -f "$deploy_root/current" 2>/dev/null || true)
        if [ -z "$previous_dir" ] || [ ! -d "$previous_dir" ]; then
            previous_dir=$deploy_root
        fi

        umask 077
        mkdir -p "$release_dir"
        tar -xzf - -C "$release_dir"

        [ -r "$release_dir/docker-compose.yml" ] || fail "release is missing docker-compose.yml"
        [ -r "$release_dir/docker-compose.personal.yml" ] || fail "release is missing the personal Compose overlay"
        [ -r "$release_dir/Dockerfile" ] || fail "release is missing Dockerfile"

        cp -p "$deploy_root/.env" "$release_dir/.env"
        cp -p "$deploy_root/config.yaml" "$release_dir/config.yaml"
        chmod 600 "$release_dir/.env" "$release_dir/config.yaml"
        chmod 755 "$deploy_root" "$release_root" "$release_dir"

        PAPER_INSIGHT_IMAGE="paper-insight:$commit_sha"
        export PAPER_INSIGHT_IMAGE
        printf '%s\n' "$PAPER_INSIGHT_IMAGE" > "$release_dir/.paper-insight-image"

        log "Validating Compose configuration"
        compose_for "$release_dir" config --quiet

        log "Building $PAPER_INSIGHT_IMAGE while the current release stays online"
        compose_for "$release_dir" build app

        log "Activating commit $commit_sha"
        if compose_for "$release_dir" up -d --no-build --wait --wait-timeout 300; then
            ln -sfn "$release_dir" "$deploy_root/current"
            docker image prune -f >/dev/null 2>&1 || true
            show_status
            log "Deployment completed for $commit_sha"
        else
            log "Activation failed"
            rollback_to "$previous_dir"
            exit 1
        fi
        ;;
    *)
        fail "only 'deploy <sha>' and 'status' are allowed"
        ;;
esac
