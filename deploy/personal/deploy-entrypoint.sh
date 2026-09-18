#!/bin/sh
# 安装为 /usr/local/sbin/deploy-paper-insight（GitHub Actions 的 SSH 强制命令）。
#
# 这个文件是唯一的"服务器侧"脚本，职责只有三件事：校验命令、把 CI 上传的归档解包
# 到 release 目录、然后把控制权交给 release 自带的
# deploy/personal/deploy-release.sh。部署逻辑本身属于 release 内容，所以以后改
# 部署流程只需要正常合入 master，不会再出现"仓库里改了、服务器上跑的还是旧副本"
# ——pdf2zh 首次上线就是因为 /usr/local/sbin 里的旧副本只构建 app 镜像而回滚的。
#
# 只有在入口点本身的契约变化时（例如新增 verb、改上传协议）才需要重新安装。
set -eu
set -f

deploy_root=/opt/paper-insight
release_root="$deploy_root/releases"

log() {
    printf '[paper-insight-deploy] %s\n' "$*"
}

fail() {
    log "ERROR: $*" >&2
    exit 1
}

active_release_script() {
    active_dir=$(readlink -f "$deploy_root/current" 2>/dev/null || true)
    [ -n "$active_dir" ] || return 1
    [ -r "$active_dir/deploy/personal/deploy-release.sh" ] || return 1
    printf '%s\n' "$active_dir/deploy/personal/deploy-release.sh"
}

# 通过 SSH 强制命令调用时（CI 走的路径）动词在 SSH_ORIGINAL_COMMAND 里；直接在
# 服务器上手工执行时动词就是普通参数（'deploy-paper-insight status'）。这里必须
# 两种都认：只读环境变量会让手工执行时 $1 为空，直接掉进最后一个分支报
# "only 'deploy <sha>' and 'status' are allowed"，看起来像装错版本。
if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
    set -- $SSH_ORIGINAL_COMMAND
fi

case "${1:-}" in
    status)
        [ "$#" -eq 1 ] || fail "status does not accept arguments"
        script=$(active_release_script) || fail "no active release provides deploy/personal/deploy-release.sh"
        exec /bin/sh "$script" status
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
        umask 077
        mkdir -p "$release_dir"
        if [ -t 0 ]; then
            fail "deploy reads the release archive from stdin (git archive | ssh ...); it cannot be run from a terminal"
        fi
        tar -xzf - -C "$release_dir"

        script="$release_dir/deploy/personal/deploy-release.sh"
        [ -r "$script" ] || fail "release is missing deploy/personal/deploy-release.sh"
        exec /bin/sh "$script" activate "$commit_sha"
        ;;
    *)
        fail "only 'deploy <sha>' and 'status' are allowed"
        ;;
esac
