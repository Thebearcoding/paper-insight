# pdf2zh HTTP 后端（Flask + Celery + 容器内 Redis）
#
# 结果只存在于 Redis 结果后端里，由 app 侧按需转发给浏览器，因此本容器不落
# 翻译产物；entrypoint 里关掉了 Redis 持久化、用 LRU 限制内存，并定期清掉
# pdf2zh/babeldoc 的 sqlite 翻译记忆。
#
# 上层的 pip 依赖与模型/字体预下载都放在 COPY 之前：改自己的脚本时不会让
# ~100MB 的下载层缓存失效。
#
# agentrouter 等网关会做客户端 UA 检测（默认 openai SDK / httpx UA 直接 401），
# 通过 sitecustomize.py 给 openai SDK 的 httpx 客户端挂一个 request 事件钩子改写 UA。
# 不能用自定义 transport= 实现：httpx 只在 transport is None 时才认 HTTP(S)_PROXY，
# 而本容器只能经 OUTBOUND_PROXY_URL 出网，那样写会让每次翻译都 Network is unreachable。
FROM python:3.12-slim

# 三个可选镜像参数，默认留空 = 走国际源。默认值必须保持"什么都不做"，因为
# .github/workflows/docker-images.yml 就是不带参数构建这个 Dockerfile 的。
# 国内服务器在 .env 里打开（见 .env.personal.example）：
#   DEBIAN_MIRROR / PYPI_INDEX_URL / HF_ENDPOINT
# 不开的话，apt(deb.debian.org) + pip(pypi.org) 在国内实测只有几十 KB/s，整层要
# 一两个小时，而 CI 部署 job 的超时是 60 分钟——冷缓存构建会直接把部署拖挂。
ARG DEBIAN_MIRROR=
ARG PYPI_INDEX_URL=
ARG HF_ENDPOINT=

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BABELDOC_DISABLE_SHOW_PROGRESS=1 \
    # 让 sitecustomize.py 生效（site-packages 目录）
    PYTHONPATH=/opt/pdf2zh-patch

WORKDIR /app

# redis-server：celery 的 broker/result backend 跑在同一容器内（redis 包只是客户端）
#
# libgl1 / libglib2.0-0 / libxext6 / libsm6 / libxrender1：opencv 的动态链接依赖
# （libxext6 会带出 libxcb1）。pdf2zh 的 doclayout 和 babeldoc 的 docvision 都要
# `import cv2`，缺 libxcb.so.1 时 build 阶段的模型预下载就直接 ImportError；
# 这份清单与上游 PDFMathTranslate 的 Dockerfile 一致（不需要 LibreOffice，我们的
# 服务只收 PDF，translate_stream 也不做 PDF/A 转换）。
#
# DEBIAN_MIRROR 只重写 deb822 的 sources 文件。两条 sed 的先后顺序有讲究：
# debian-security 必须先替换，否则会被 debian 那条匹配掉，拼出
# "<mirror>-security" 这种不存在的路径。
RUN if [ -n "$DEBIAN_MIRROR" ]; then \
        sed -i \
            -e "s#http://deb.debian.org/debian-security#${DEBIAN_MIRROR%/}/debian-security#g" \
            -e "s#http://deb.debian.org/debian#${DEBIAN_MIRROR%/}/debian#g" \
            /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        redis-server \
        libgl1 \
        libglib2.0-0 \
        libxext6 \
        libsm6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# PYPI_INDEX_URL 用位置参数拼进去，避免为了一个可选参数把包清单写两遍（副本之间
# 会漂移）。空值时 set -- 清空参数列表，pip 走默认源，与改动前完全一致。
RUN set -eu; \
    if [ -n "$PYPI_INDEX_URL" ]; then set -- -i "$PYPI_INDEX_URL"; else set --; fi; \
    pip install --no-cache-dir "$@" \
        "pdf2zh[backend]==1.9.4" \
        "tencentcloud-sdk-python-tmt==3.1.121" \
        "numpy>=2.0.2,<3" \
        "opencv-python==4.14.0.94" \
        "opencv-python-headless==4.14.0.94"
# 注意一：pdf2zh 1.9.4 对 tencentcloud-sdk-python-tmt 没有上界约束，而 3.1.129 起
# v20180321.models 里已经没有 TextTranslateRequest，import pdf2zh 会直接失败。
# 3.1.121 是最后一个仍包含该类的版本，必须显式钉住。
#
# 注意二：numpy 不能用 1.x。pdf2zh 依赖 babeldoc>=0.1.22，而 babeldoc 的全部 0.1.x
# 都声明 numpy>=2.0.2，钉 numpy<2 会让 pip 解析失败（ResolutionImpossible，镜像
# 根本构建不出来）。但 pdf2zh 1.9.4 的 translate_patch 和 babeldoc 的 docvision
# 仍在用被 NumPy 2 移除的二进制模式 np.fromstring（每页都会 ValueError），所以由
# sitecustomize.py 在运行时补一个 frombuffer + copy 的等价实现（见下）
#
# 注意三：pdf2zh 要 opencv-python-headless，babeldoc 要 opencv-python（带 GUI），
# 两个发行版装进的是同一个 cv2/ 目录，谁后装谁覆盖对方——所以钉成同一个版本，
# 避免出现两套版本文件混在一起；不钉的话 headless 会跟着上 OpenCV 5（alpha）。

# 预下载 doclayout onnx 模型（~80MB）和中文衬线字体：运行时不再依赖外网，
# 也不需要在容器可写层里缓存这些资源（容器没有挂持久卷，见 docker-compose.yml）。
#
# HF_ENDPOINT 影响 babeldoc 取 doclayout 模型的那次 hf_hub_download；国内默认的
# huggingface.co 取不到时用 hf-mirror.com。中文衬线字体是从 GitHub 下载的，不受
# 这个变量影响，国内只是慢。
RUN export HF_ENDPOINT="${HF_ENDPOINT:-https://huggingface.co}" \
    && python -c "\
from pathlib import Path; \
import shutil; \
from pdf2zh.high_level import download_remote_fonts; \
src = Path(download_remote_fonts('zh')); \
dst = Path('/app') / src.name; \
src == dst or shutil.copy2(src, dst); \
print('baked font:', dst)" \
    && python -c "\
from babeldoc.assets.assets import get_doclayout_onnx_model_path as path; \
print('baked model:', path())"

# UA patch：openai SDK 3.x 在组装请求时自己写 User-Agent，default_headers 覆盖不掉，
# 所以改用 httpx 的 request 事件钩子在发送前改写（已实测验证；钩子同时保留了
# httpx 对 HTTP(S)_PROXY 的支持，见 sitecustomize.py 的说明）
COPY docker/pdf2zh/sitecustomize.py \
     docker/pdf2zh/cache_maintenance.py \
     docker/pdf2zh/worker.py \
     docker/pdf2zh/server.py \
     /opt/pdf2zh-patch/
COPY docker/pdf2zh/entrypoint.sh /usr/local/bin/pdf2zh-entrypoint
COPY docker/pdf2zh/healthcheck.sh /usr/local/bin/pdf2zh-healthcheck
RUN chmod +x /usr/local/bin/pdf2zh-entrypoint /usr/local/bin/pdf2zh-healthcheck

EXPOSE 11008

ENTRYPOINT ["pdf2zh-entrypoint"]
