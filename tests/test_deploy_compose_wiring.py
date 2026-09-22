"""生产部署的 Compose / 部署脚本静态一致性检查。

服务器上实际执行的是手动安装的 /usr/local/sbin/deploy-paper-insight（薄入口点），
它把 CI 上传的归档解包后再交给 release 里的 deploy/personal/deploy-release.sh；
部署逻辑属于 release 内容，所以它由 release 脚本 `docker compose build`、再用
`up -d --no-build` 激活。任何"只有 build: 没有可拉取 image:"的服务都必须被构建到，
否则激活时才会报 `No such image` 并回滚（pdf2zh 首次上线就是这样失败的）。CI 里
构建镜像很贵，这里用静态检查兜住这类接线错误。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_BASE = REPO_ROOT / "docker-compose.yml"
COMPOSE_PERSONAL = REPO_ROOT / "docker-compose.personal.yml"
ENTRYPOINT_SCRIPT = REPO_ROOT / "deploy" / "personal" / "deploy-entrypoint.sh"
RELEASE_SCRIPT = REPO_ROOT / "deploy" / "personal" / "deploy-release.sh"

# 生产（个人 profile）实际使用的 Compose 组合：base + personal overlay。
# 与 deploy/personal/deploy-release.sh 里的 -f 参数保持一致。
BUILD_COMMAND_PATTERN = re.compile(
    r'compose_for\s+"\$release_dir"\s+(?:--parallel\s+1\s+)?build(?P<services>[^\n]*)'
)


def _posix_shell() -> str:
    sh = shutil.which("sh")
    if sh is None and os.name == "nt":
        git = shutil.which("git")
        if git:
            candidate = Path(git).resolve().parents[1] / "bin" / "sh.exe"
            if candidate.is_file():
                sh = str(candidate)
    if sh is None:
        pytest.skip("需要 POSIX sh 来实跑 shell 函数")
    return sh


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
    assert BUILD_COMMAND_PATTERN.search(release_script)
    # 解包上传归档是入口点的职责（release 脚本只处理已落盘的 release）
    assert "tar -xzf - -C" in entrypoint
    assert "tar -xzf - -C" not in release_script


def test_installed_entrypoint_accepts_the_verb_from_arguments_too():
    """手工执行 /usr/local/sbin/deploy-paper-insight status 也必须能用。

    CI 走 SSH 强制命令，动词在 SSH_ORIGINAL_COMMAND 里；但运维手工检查时动词就是
    普通参数。入口点如果只读环境变量，手工执行会让 $1 为空，掉进最后的分支报
    "only 'deploy <sha>' and 'status' are allowed"，看起来像装错了版本（线上实际
    遇到过，诊断成本很高）。所以环境变量只应覆写位置参数，不能取代它。
    """
    entrypoint = ENTRYPOINT_SCRIPT.read_text(encoding="utf-8")

    assert 'if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then' in entrypoint
    assert re.search(r"set -- \$SSH_ORIGINAL_COMMAND\nfi", entrypoint), (
        "SSH_ORIGINAL_COMMAND 应该只在非空时覆写位置参数，否则调用者传的 \"$@\" 会丢失"
    )
    assert "set -- $original_command" not in entrypoint

    # deploy 从 stdin 读归档，在终端里跑会一直等输入；必须快速失败而不是挂住。
    assert "[ -t 0 ]" in entrypoint

    # 手工安装的副本变旧时行为和新副本几乎一样，所以每次调用都要打印自身指纹，
    # 让日志能直接指认跑的是哪一份文件。
    assert 'log "entrypoint sha256=$(this_fingerprint) verb=${1:-<none>}"' in entrypoint
    assert 'sha256sum "$0"' in entrypoint


def test_pdf2zh_build_mirror_args_stay_wired_and_default_off():
    """国内服务器构建 pdf2zh 镜像必须能走镜像源，否则冷缓存构建会拖挂部署。

    apt(deb.debian.org) + pip(pypi.org) 在国内实测几十 KB/s，整层要一两个小时，
    而 CI 部署 job 只有 60 分钟超时。所以 compose 必须把三个可选参数透传进去，
    而且默认值必须是空——GitHub Actions 的 docker-images 校验不带参数构建，默认
    留空才能保证那条路径与改动前完全一致。
    """
    overlay = _load_services(COMPOSE_PERSONAL)
    build_args = ((overlay.get("pdf2zh") or {}).get("build") or {}).get("args") or {}
    assert set(build_args) == {"DEBIAN_MIRROR", "PYPI_INDEX_URL", "HF_ENDPOINT"}
    for name, value in build_args.items():
        assert value == "${" + name + ":-}", f"{name} 应该从 .env 取值且允许留空"

    dockerfile = (REPO_ROOT / "docker" / "pdf2zh" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    for name in build_args:
        assert re.search(rf"^ARG {name}=$", dockerfile, re.MULTILINE), (
            f"Dockerfile 没有声明 ARG {name}=（compose 传了但构建时会被忽略）"
        )

    # 默认必须是国际源：改动前后行为一致
    assert "deb.debian.org" in dockerfile
    assert "https://huggingface.co" in dockerfile


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


def test_every_personal_service_has_a_memory_limit():
    """每个服务都有上限，但上限之和超出物理内存仍可能触发 global OOM。

    此检查只防止单个容器无限增长，不能替代包含宿主机与构建进程的总预算。
    """
    services = _personal_deployment_services()
    missing = sorted(name for name, service in services.items() if "mem_limit" not in service)
    assert not missing, f"{missing} 没有 mem_limit，失控时会把整机拖进 global OOM"


def test_personal_postgres_avoids_extra_parallel_workers():
    command = _personal_deployment_services()["postgres"]["command"]
    assert command[0] == "postgres"
    assert command[1::2] == ["-c"] * len(command[2::2])
    settings = dict(value.split("=", 1) for value in command[2::2])
    assert settings["max_parallel_workers_per_gather"] == "0"
    assert settings["max_parallel_maintenance_workers"] == "0"
    assert settings["jit"] == "off"
    assert settings.get("autovacuum", "on") == "on"
    assert settings["shared_buffers"] == "64MB"
    assert settings["work_mem"] == "2MB"
    assert settings["maintenance_work_mem"] == "32MB"
    assert settings["max_connections"] == "30"


def test_personal_optimizations_preserve_limits_and_features():
    services = _personal_deployment_services()
    limits = {name: service["mem_limit"] for name, service in services.items()}
    assert limits == {
        "postgres": "384m", "typesense": "512m", "app": "320m",
        "pdf2zh": "768m", "caddy": "80m",
    }
    assert sum(int(value[:-1]) for value in limits.values()) == 2064
    # 本辅助函数的浅合并足够检查限制；环境变量按 Compose 的 mapping 规则单独合并。
    base = _load_services(COMPOSE_BASE)
    overlay = _load_services(COMPOSE_PERSONAL)
    for name in ("typesense", "pdf2zh"):
        assert not services[name].get("profiles"), f"{name} 不应变为默认停用的服务"
    app_env = {**base["app"]["environment"], **overlay["app"].get("environment", {})}
    assert app_env["TYPESENSE_ENABLED"] == "true"
    assert app_env["TYPESENSE_SEMANTIC_SEARCH_ENABLED"] == (
        "${TYPESENSE_SEMANTIC_SEARCH_ENABLED:-true}"
    )
    pdf_env = {**base["pdf2zh"]["environment"], **overlay["pdf2zh"]["environment"]}
    assert pdf_env["PDF2ZH_CELERY_CONCURRENCY"] == "${PDF2ZH_CELERY_CONCURRENCY:-1}"
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        assert pdf_env[name] == "1"


def test_personal_env_sources_supply_all_required_compose_variables():
    compose = COMPOSE_BASE.read_text(encoding="utf-8") + COMPOSE_PERSONAL.read_text(encoding="utf-8")
    required = set(re.findall(r"\$\{([A-Z_]+):\?", compose))
    bootstrap = (REPO_ROOT / "deploy/personal/bootstrap-server.sh").read_text(encoding="utf-8")
    template = re.search(r'cat >"\$env_tmp" <<EOF\n(.*?)\nEOF', bootstrap, re.DOTALL)
    assert template
    example = (REPO_ROOT / ".env.personal.example").read_text(encoding="utf-8")
    for source in (template.group(1), example):
        values = dict(re.findall(r"^([A-Z_]+)=(.*)$", source, re.MULTILINE))
        assert required <= values.keys()
        assert all(values[key] for key in required)
        assert "pdf2zh" in values["OUTBOUND_NO_PROXY"].split(",")
        assert values["TYPESENSE_SEMANTIC_SEARCH_ENABLED"] == "true"
    assert 'typesense_key="$(openssl rand -hex 32)"' in bootstrap
    assert "TYPESENSE_API_KEY=$typesense_key" in template.group(1)


def test_release_builds_all_services_serially(tmp_path):
    """运行真实 Compose 包装函数与构建命令，验证选项实际传给 Docker。"""
    script = RELEASE_SCRIPT.read_text(encoding="utf-8")
    wrapper = re.search(r"^compose_for\(\) \{\n.*?\n\}\n", script, re.MULTILINE | re.DOTALL)
    assert wrapper
    build_line = re.search(
        r'^\s*(COMPOSE_BAKE=false compose_for "\$release_dir" --parallel 1 build)\s*$', script, re.MULTILINE,
    )
    assert build_line, "2GB 主机应串行构建所有服务，且不能禁用 Docker 层缓存"
    runner = tmp_path / "build.sh"
    runner.write_text(
        'set -eu\ndeploy_root="/test deployment"\ncompose_project=paper-insight\n'
        'release_dir="/test deployment/releases/new"\n'
        'docker() { printf "%s\\n" "$COMPOSE_BAKE" "$@"; }\n'
        + wrapper.group(0) + "\n" + build_line.group(1) + "\n",
        encoding="utf-8",
    )
    result = subprocess.run([_posix_shell(), str(runner)], check=True, capture_output=True, text=True)
    assert result.stdout.splitlines() == [
        "false", "compose", "--env-file", "/test deployment/.env",
        "--project-name", "paper-insight",
        "-f", "/test deployment/releases/new/docker-compose.yml",
        "-f", "/test deployment/releases/new/docker-compose.personal.yml",
        "--parallel", "1", "build",
    ]


def test_personal_shell_scripts_have_valid_syntax():
    sh = _posix_shell()
    for name in ("deploy-release.sh", "deploy-entrypoint.sh", "bootstrap-server.sh"):
        subprocess.run([sh, "-n", str(REPO_ROOT / "deploy/personal" / name)], check=True)


def test_release_script_prunes_stale_release_images_but_keeps_rollback_target(tmp_path):
    """带标签的历史 release 镜像不会自己被清掉，得靠部署脚本。

    每次部署都会 build 出一个 paper-insight:<sha>；`docker image prune -f` 只删
    无标签镜像，所以它们一直累积——实测 66 个之后 dockerd 的匿名内存涨到约 500MB
    （机器只有 1.8GB）。回滚只退一级（rollback_to 走 --no-build 需要上一版镜像
    还在本地），所以当前版和上一版必须留下。keep 列表写错很难从静态字符串看出来，
    这里用一个假的 docker 把函数真正跑一遍。
    """
    sh = _posix_shell()

    script = RELEASE_SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        r"^prune_stale_release_images\(\) \{\n(?P<body>.*?)\n\}\n",
        script,
        re.MULTILINE | re.DOTALL,
    )
    assert match, "deploy-release.sh 里找不到 prune_stale_release_images()"

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_docker = bin_dir / "docker"
    fake_docker.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "images" ]; then\n'
        "    printf '%s\\n' paper-insight:new paper-insight:prev paper-insight:old-1 paper-insight:old-2\n"
        "else\n"
        '    printf \'%s\\n\' "$2" >> "$DOCKER_RMI_LOG"\n'
        "fi\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    rmi_log = tmp_path / "rmi.log"
    runner = tmp_path / "run.sh"
    runner.write_text(
        "set -eu\n"
        + match.group(0)
        + '\nprune_stale_release_images "paper-insight:new" "paper-insight:prev"\n',
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["DOCKER_RMI_LOG"] = rmi_log.as_posix()
    subprocess.run([sh, str(runner)], check=True, env=env)

    removed = sorted(rmi_log.read_text(encoding="utf-8").split())
    assert removed == ["paper-insight:old-1", "paper-insight:old-2"]

    # 没有上一版时（首次部署）不能把 current 也删掉
    rmi_log.unlink()
    subprocess.run(
        [sh, "-c", match.group(0) + '\nprune_stale_release_images "paper-insight:new" ""\n'],
        check=True,
        env=env,
    )
    assert sorted(rmi_log.read_text(encoding="utf-8").split()) == [
        "paper-insight:old-1",
        "paper-insight:old-2",
        "paper-insight:prev",
    ]
