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
# 通过 sitecustomize.py 给 openai SDK 注入自定义 transport 改写 UA。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BABELDOC_DISABLE_SHOW_PROGRESS=1 \
    # 让 sitecustomize.py 生效（site-packages 目录）
    PYTHONPATH=/opt/pdf2zh-patch

WORKDIR /app

# redis-server：celery 的 broker/result backend 跑在同一容器内（redis 包只是客户端）
RUN apt-get update \
    && apt-get install -y --no-install-recommends redis-server \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "pdf2zh[backend]==1.9.4" \
    "tencentcloud-sdk-python-tmt==3.1.121" \
    "numpy>=2.0.2,<3"
# 注意一：pdf2zh 1.9.4 对 tencentcloud-sdk-python-tmt 没有上界约束，而 3.1.129 起
# v20180321.models 里已经没有 TextTranslateRequest，import pdf2zh 会直接失败。
# 3.1.121 是最后一个仍包含该类的版本，必须显式钉住。
#
# 注意二：numpy 不能用 1.x。pdf2zh 依赖 babeldoc>=0.1.22，而 babeldoc 的全部 0.1.x
# 都声明 numpy>=2.0.2，钉 numpy<2 会让 pip 解析失败（ResolutionImpossible，镜像
# 根本构建不出来）。但 pdf2zh 1.9.4 的 translate_patch 和 babeldoc 的 docvision
# 仍在用被 NumPy 2 移除的二进制模式 np.fromstring（每页都会 ValueError），所以由
# sitecustomize.py 在运行时补一个 frombuffer + copy 的等价实现（见下）

# 预下载 doclayout onnx 模型（~80MB）和中文衬线字体：运行时不再依赖外网，
# 也不需要在容器可写层里缓存这些资源（容器没有挂持久卷，见 docker-compose.yml）。
RUN python -c "\
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

# UA patch：openai SDK 3.x 在 transport 层强制覆盖 User-Agent，
# 必须用自定义 transport 才能改写（已实测验证）
COPY docker/pdf2zh/sitecustomize.py \
     docker/pdf2zh/worker.py \
     docker/pdf2zh/server.py \
     /opt/pdf2zh-patch/
COPY docker/pdf2zh/entrypoint.sh /usr/local/bin/pdf2zh-entrypoint
COPY docker/pdf2zh/healthcheck.sh /usr/local/bin/pdf2zh-healthcheck
RUN chmod +x /usr/local/bin/pdf2zh-entrypoint /usr/local/bin/pdf2zh-healthcheck

EXPOSE 11008

ENTRYPOINT ["pdf2zh-entrypoint"]
