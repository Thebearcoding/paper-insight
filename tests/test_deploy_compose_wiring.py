"""生产部署的 Compose / 部署脚本静态一致性检查。

服务器上实际执行的是手动安装的 /usr/local/sbin/deploy-paper-insight（薄入口点），
它把 CI 上传的归档解包后再交给 release 里的 deploy/personal/deploy-release.sh；
部署逻辑属于 release 内容，所以它由 release 脚本 `docker compose build`、再用
`up -d --no-build` 激活。任何"只有 build: 没有可拉取 image:"的服务都必须被构建到，
否则激活时才会报 `No such image` 并回滚（pdf2zh 首次上线就是这样失败的）。CI 里
构建镜像很贵，这里用静态检查兜住这类接线错误。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_BASE = REPO_ROOT / "docker-compose.yml"
COMPOSE_PERSONAL = REPO_ROOT / "docker-compose.personal.yml"
ENTRYPOINT_SCRIPT = REPO_ROOT / "deploy" / "personal" / "deploy-entrypoint.sh"
RELEASE_SCRIPT = REPO_ROOT / "deploy" / "personal" / "deploy-release.sh"

# 生产（个人 profile）实际使用的 Compose 组合：base + personal overlay。
# 与 deploy/personal/deploy-release.sh 里的 -f 参数保持一致。
BUILD_COMMAND_PATTERN = re.compile(
    r'compose_for\s+"\$release_dir"\s+build(?P<services>[^\n]*)'
)


def _load_services(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    services = document.get("services") or {}
    assert isinstance(services, dict)
    return services


def _personal_deployment_services() -> dict:
    """按 Compose 语义把 base 与 personal overlay 逐服务合并。"""
    base = _load_services(COMPOSE_BASE)
    overlay = _load_services(COMPOSE_PERSONAL)
    merged: dict[str, dict] = {}
    for name in sorted(set(base) | set(overlay)):
        merged[name] = {**(base.get(name) or {}), **(overlay.get(name) or {})}
    return merged


def test_pdf2zh_service_is_defined_once_and_is_build_only():
    services = _personal_deployment_services()
    pdf2zh = services["pdf2zh"]
    assert "build" in pdf2zh
    # 没有写死 image：镜像名由 Compose 按项目名生成（paper-insight-pdf2zh），
    # 因此必须由部署脚本在本机构建，不能指望从 registry 拉取。
    assert "image" not in pdf2zh


def test_deploy_script_builds_every_build_only_service():
    services = _personal_deployment_services()
    build_services = {name for name, service in services.items() if "build" in service}
    assert {"app", "pdf2zh"} <= build_services

    script = RELEASE_SCRIPT.read_text(encoding="utf-8")
    commands = BUILD_COMMAND_PATTERN.findall(script)
    assert commands, "部署脚本没有构建任何镜像"

    for command in commands:
        declared = command.split()
        if not declared:
            # 不带服务名 = 构建所有声明了 build 的服务，天然覆盖新增服务。
            continue
        missing = sorted(build_services - set(declared))
        assert not missing, (
            "部署脚本显式列举了构建目标，但漏了 "
            f"{missing}；要么补上，要么去掉服务名让它构建全部"
        )


def test_installed_entrypoint_delegates_to_the_release_script():
    """/usr/local/sbin/deploy-paper-insight 是手动安装的，仓库里改了不会自动生效。

    所以入口点必须保持是薄的一层：解包 + 交给 release 自带的 deploy-release.sh。
    如果部署逻辑又长回入口点里，就会重现"仓库里改了、服务器上跑旧副本"的问题
    （pdf2zh 首次上线的激活失败就是这么来的）。
    """
    entrypoint = ENTRYPOINT_SCRIPT.read_text(encoding="utf-8")

    # 入口点必须把控制权交给 release 自带的脚本，并且只通过 activate / status 两个
    # verb 调用它（release 脚本里对应的 case 分支也要存在）。
    assert 'script="$release_dir/deploy/personal/deploy-release.sh"' in entrypoint
    assert 'exec /bin/sh "$script" activate "$commit_sha"' in entrypoint
    assert 'exec /bin/sh "$script" status' in entrypoint
    release_script = RELEASE_SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"^\s*activate\)", release_script, re.MULTILINE)
    assert re.search(r"^\s*status\)", release_script, re.MULTILINE)

    assert "docker compose" not in entrypoint
    assert 'compose_for "$release_dir" build' in release_script
    # 解包上传归档是入口点的职责（release 脚本只处理已落盘的 release）
    assert "tar -xzf - -C" in entrypoint
    assert "tar -xzf - -C" not in release_script


def test_personal_deployment_services_rotate_logs():
    services = _personal_deployment_services()
    for name, service in services.items():
        logging_config = service.get("logging") or {}
        assert logging_config.get("driver") == "json-file", f"{name} 没有限制日志驱动"
        options = logging_config.get("options") or {}
        assert options.get("max-size"), f"{name} 没有限制日志文件大小"
        assert options.get("max-file"), f"{name} 没有限制日志文件个数"


def test_only_caddy_publishes_public_ports():
    services = _personal_deployment_services()
    published: dict[str, list[str]] = {}
    for name, service in services.items():
        for entry in service.get("ports") or []:
            assert isinstance(entry, str), f"{name} 使用了未支持的 ports 长语法"
            published.setdefault(name, []).append(entry)

    assert published.get("caddy") == ["80:80", "443:443", "443:443/udp"]

    for name, entries in published.items():
        if name == "caddy":
            continue
        for entry in entries:
            assert entry.startswith("127.0.0.1:"), f"{name} 把 {entry} 暴露到了公网"
