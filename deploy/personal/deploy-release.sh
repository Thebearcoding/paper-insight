#!/bin/sh
# 部署逻辑（属于 release 内容，随每次发布一起上传）。
#
# 由 /usr/local/sbin/deploy-paper-insight 调用：入口点已经校验过参数、把归档解包
# 到 $release_root/<sha>，这里只负责把那个 release 切上线（构建镜像、迁移、健康
# 检查、失败回滚）。
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

release_image_of() {
    if [ -f "$1/.paper-insight-image" ]; then
        cat "$1/.paper-insight-image"
    fi
}

prune_stale_release_images() {
    # 每次部署都会 build 出一个带标签的 paper-insight:<sha>，而 `docker image
    # prune -f` 只删无标签（dangling）镜像，带标签的历史版本会一直累积。实测攒到
    # 66 个、外加 289 条 build cache 之后，dockerd 自己的匿名内存涨到约 500MB：
    # 这台机器只有 1.8GB 物理内存，一个守护进程占了四分之一还多，比任何应用容器
    # 都大，而 / 盘也一直被镜像层占着。
    #
    # 回滚只会退一级（rollback_to 走 `up -d --no-build` 时需要上一版的镜像还在
    # 本地），所以保留当前版和上一版就够；其余删掉，让 dockerd 的元数据不再随
    # 部署次数增长。
    keep_current=$1
    keep_previous=$2
    for image in $(docker images --format '{{.Repository}}:{{.Tag}}' --filter 'reference=paper-insight:*'); do
        if [ "$image" = "$keep_current" ]; then
            continue
        fi
        if [ -n "$keep_previous" ] && [ "$image" = "$keep_previous" ]; then
            continue
        fi
        docker rmi "$image" >/dev/null 2>&1 || true
    done
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

case "${1:-}" in
    status)
        [ "$#" -eq 1 ] || fail "status does not accept arguments"
        show_status
        ;;
    activate)
        [ "$#" -eq 2 ] || fail "usage: activate <40-character-commit-sha>"
        commit_sha=$2
        [ "${#commit_sha}" -eq 40 ] || fail "invalid commit SHA length"
        case "$commit_sha" in
            *[!0-9a-f]*) fail "invalid commit SHA" ;;
        esac

        release_dir="$release_root/$commit_sha"
        [ -r "$release_dir/docker-compose.yml" ] || fail "release $commit_sha has not been staged"

        previous_dir=$(readlink -f "$deploy_root/current" 2>/dev/null || true)
        if [ -z "$previous_dir" ] || [ ! -d "$previous_dir" ]; then
            previous_dir=$deploy_root
        fi

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

        log "Building release images while the current release stays online"
        # 不带服务名：构建所有声明了 build: 的服务（app 和 pdf2zh）。写死服务名
        # 会让新增的 build-only 服务在激活时才暴露缺镜像（pdf2zh 就是这样挂的）。
        compose_for "$release_dir" build

        log "Activating commit $commit_sha"
        if compose_for "$release_dir" up -d --no-build --wait --wait-timeout 300; then
            ln -sfn "$release_dir" "$deploy_root/current"
            docker image prune -f >/dev/null 2>&1 || true
            prune_stale_release_images "$PAPER_INSIGHT_IMAGE" "$(release_image_of "$previous_dir")"
            # build cache 同理，每次 build 留一层（实测 289 条 / 700MB）。只清 14
            # 天以前的：近期的缓存让重复构建继续走 cache，而 pdf2zh 那层冷缓存要
            # 一个多小时，热缓存只要两分钟。
            docker builder prune -f --filter until=336h >/dev/null 2>&1 || true
            show_status
            log "Deployment completed for $commit_sha"
        else
            log "Activation failed"
            rollback_to "$previous_dir"
            exit 1
        fi
        ;;
    *)
        fail "only 'status' and 'activate <sha>' are allowed"
        ;;
esac
