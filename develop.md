# develop.md

这个文档记录 Paper Insight 的本地开发、数据准备和部署流程。README 只保留项目必要信息，开发者相关细节统一放在这里。

## 启动指令(开发者)

```bash
brew services start postgresql@16

cd backend
uv run uvicorn app:app --reload --host 127.0.0.1 --port 8000
```


## 当前项目状态

Paper Insight 当前使用：

- 后端：FastAPI
- 前端：React 19 + TypeScript + Vite
- 数据库：PostgreSQL 16
- 数据访问：`psycopg`
- 配置入口：`config.yaml`
- 搜索：Typesense 关键词/向量混合检索，PostgreSQL Full Text Search 作为回退
- 论文正文：缓存到 `data/paper_cache/`，不写入主业务表
- 账号：GitHub OAuth 注册，旧邮箱密码账号可继续登录

运行时真相以代码为准；如果 README、历史脚本和代码有冲突，优先看 `backend/app.py`、`backend/config.py`、`backend/database.py` 和 `db/migrations/`。

## 依赖安装

后端：

```bash
uv sync
```

前端：

```bash
cd frontend-react
npm install
```

## 本地 PostgreSQL 16

推荐使用 Homebrew：

```bash
brew install postgresql@16
brew services start postgresql@16
createdb paper_online
```

常用命令：

```bash
brew services start postgresql@16
brew services stop postgresql@16
brew services restart postgresql@16
brew services list | grep postgresql@16
```

如果要清空重来：

```bash
dropdb --if-exists paper_online
createdb paper_online
```

## 配置 `config.yaml`

复制示例配置：

```bash
cp config.yaml.example config.yaml
```

`config.yaml` 已加入 `.gitignore`，不要提交。

本地开发至少确认：

```yaml
database:
  url: postgresql:///paper_online

llm:
  step_api_key: your_api_key_here

admin:
  email: admin@example.com
  initial_password: change-this-admin-password

auth:
  github_client_id: your_github_oauth_client_id
  github_client_secret: your_github_oauth_client_secret
  github_callback_url: http://127.0.0.1:8000/auth/github/callback
  frontend_base_url: http://127.0.0.1:5173

hf_daily:
  enabled: true
  api_url: https://huggingface.co/api/daily_papers
  # 国内服务器可填写仅供 HF Daily 使用的 HTTP 代理。
  proxy_url:
  fetch_time: "22:00"
  timezone: Asia/Shanghai
  top_n: 5
```

本地 GitHub OAuth App 推荐填写：

- Homepage URL：`http://127.0.0.1:8000`
- Authorization callback URL：`http://127.0.0.1:8000/auth/github/callback`

生产 GitHub OAuth App 推荐单独创建：

- Homepage URL：`https://paper.athebear.me`
- Authorization callback URL：`https://paper.athebear.me/auth/github/callback`

## 初始化数据库

执行 migration：

```bash
uv run python scripts/apply_migrations.py
```

后端启动时也会自动执行 SQL migration；手动运行脚本主要用于提前准备全新的本地数据库。

如需最小开发数据：

```bash
uv run python scripts/apply_migrations.py --seed dev
```

## 开发模式启动

启动后端：

```bash
cd backend
uv run uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

另开一个终端启动前端：

```bash
cd frontend-react
npm run dev
```

访问：

- 前端：`http://127.0.0.1:5173`
- 后端 API：`http://127.0.0.1:8000`

常用页面：

- 首页：`http://127.0.0.1:5173/`
- 全局搜索：`http://127.0.0.1:5173/search?q=agent`
- 会议页：`http://127.0.0.1:5173/conference/iclr_2026`
- Hugging Face Daily Papers：`http://127.0.0.1:5173/hf-daily`
- 登录 / 注册：`http://127.0.0.1:5173/login`、`http://127.0.0.1:5173/register`
- 我的论文：`http://127.0.0.1:5173/me`
- 管理员后台：`http://127.0.0.1:5173/admin`

停止服务时，在两个终端分别按 `Ctrl + C`。

## 本地开发数据

### 方式 A：最小 seed

适合快速启动页面、联调接口，不需要完整线上数据。

```bash
uv run python scripts/apply_migrations.py --seed dev
```

### 方式 B：从 `crawled_data/` 导入

适合重建或补充某个会议的数据。

先确保已经初始化数据库：

```bash
uv run python scripts/apply_migrations.py
```

然后按会议导入：

```bash
uv run python scripts/import_papers.py --conference neurips_2025
uv run python scripts/import_papers.py --conference iclr_2026
uv run python scripts/import_papers.py --conference icml_2025
uv run python scripts/build_chi_2026_jsonl.py
uv run python scripts/import_papers.py --conference chi_2026
uv run python scripts/build_cvpr_2026_jsonl.py
uv run python scripts/import_papers.py --conference cvpr_2026
```

说明：

- 数据源目录固定为 `crawled_data/{conference}/`
- CHI 2026 的元数据源是 DBLP + OpenAlex，先用 `scripts/build_chi_2026_jsonl.py` 生成 `crawled_data/chi_2026/main_papers.jsonl`
- CHI 2026 默认只保留 OpenAlex 提供的非 ACM PDF 论文；ACM DL PDF 在服务器侧常被访问验证拦截，只有维护者明确需要全量元数据时才使用 `--include-acm-only`
- CVPR 2026 的元数据源是 CVF Open Access，先用 `scripts/build_cvpr_2026_jsonl.py` 生成 `crawled_data/cvpr_2026/main_papers.jsonl`；默认排序写入 `sort_order`，会议页按 CVF 官方 `day=all` 顺序展示
- 导入是按论文覆盖式刷新
- `papers` 会 upsert
- 对应论文的 `authors` / `keywords` 会先删后插
- `llm_response` 不会在导入阶段生成，后续由用户访问或后台分析补全

## 论文正文磁盘缓存

Jina Reader 解析出来的论文正文不会写入 PostgreSQL，而是缓存到：

```text
data/paper_cache/
```

当前行为：

- 第一次分析论文时，如果缓存不存在，会调用 Jina Reader 并写入缓存
- 第一次初始化 chat 上下文时，如果缓存不存在，也会调用 Jina Reader
- 命中缓存后，analysis / chat / 后台分析都会直接复用本地文本

如果想强制重新抓取正文：

```bash
rm -rf data/paper_cache
```

## Hugging Face Daily Papers

默认配置会启用每日同步：

```yaml
hf_daily:
  enabled: true
  api_url: https://huggingface.co/api/daily_papers
  fetch_time: "22:00"
  timezone: Asia/Shanghai
  top_n: 5
```

当前行为：

- 定时任务运行在 FastAPI 进程内
- 每个配置日期抓取一次 Hugging Face Daily Papers API
- `proxy_url` 仅作用于 HF Daily API；需要让整个应用的外部 HTTP(S) 请求统一走代理时，在 `.env` 配置 `OUTBOUND_PROXY_URL`
- `OUTBOUND_NO_PROXY` 默认保留 PostgreSQL、Typesense 和其他容器内部服务直连；不要删除这些内部主机名
- 点赞数最高的论文会写入 `papers`，来源元数据写入 `hf_daily_papers`
- 新论文在 AI 分析完成前保持 `llm_response IS NULL`
- 管理员后台提供手动同步按钮

## 推荐本地开发顺序

```bash
brew services start postgresql@16
createdb paper_online
cp config.yaml.example config.yaml
# 编辑 config.yaml，填入 database.url、LLM 凭据加密密钥、GitHub OAuth 和初始管理员
uv run python scripts/apply_migrations.py --seed dev
(cd backend && uv run uvicorn app:app --reload --host 127.0.0.1 --port 8000)
(cd frontend-react && npm run dev)
```

## 本地模拟生产运行

先构建前端：

```bash
cd frontend-react
npm run build
```

再启动 FastAPI：

```bash
cd backend
uv run uvicorn app:app --host 0.0.0.0 --port 8000
```

访问：

```text
http://127.0.0.1:8000
```

如果 `frontend-react/dist` 不存在，FastAPI 会返回明确错误，提示先构建前端。

## Docker / VPS 部署

仓库包含可直接使用的 [Dockerfile](./Dockerfile)。它会：

- 构建 `frontend-react`
- 将 `frontend-react/dist` 复制进最终镜像
- 在启动前自动执行 PostgreSQL migration
- 启动 FastAPI

推荐在 VPS 上通过 `config.yaml` 启动 Docker Compose：

```bash
cp config.yaml.example config.yaml
# 编辑 config.yaml：
# - server.host 改为 0.0.0.0
# - database.url 改为 postgresql://paper:<password>@postgres:5432/paper_online
# - 填入 llm.credential_encryption_key、GitHub OAuth 和初始管理员
uv run python scripts/docker_compose.py up --build -d
```

`scripts/docker_compose.py` 会在首次运行时生成随机 Typesense API Key，保存到权限为
`0600` 的 `.docker/compose.env`，后续更新会继续复用同一个 key。

Compose 会同时启动 PostgreSQL、Typesense 和应用。应用首次启动时会在后台建立
Typesense 索引；在索引完成前或 Typesense 暂时不可用时，搜索自动回退到 PostgreSQL。
默认使用 `ts/multilingual-e5-small`，首次建索引会下载模型文件。

需要手动完整重建搜索索引时执行：

```bash
docker compose exec app python /app/scripts/reindex_typesense.py
```

重建过程先写入新的物理 collection，完成后原子切换 `papers` alias，搜索不需要停机。

如果只想构建单个应用镜像：

```bash
docker build -t paper-insight .
```

容器运行时读取挂载进去的 `/app/config.yaml`。当前 Compose 会把 `/app/data` 挂到命名 volume，用来持久化 `data/paper_cache/`。

## 生产更新

当前生产环境使用：

- 域名：`paper.athebear.me`
- 代码仓库：`Thebearcoding/paper-insight`
- 部署分支：`master`
- 部署目录：`/opt/paper-insight`
- 受限部署入口：`/usr/local/sbin/deploy-paper-insight`（由 `deploy/personal/deploy-entrypoint.sh` 安装）

这个入口点是手动安装的薄层，只做参数校验、把上传的归档解包到
`/opt/paper-insight/releases/<sha>`，然后把控制权交给该 release 自带的
`deploy/personal/deploy-release.sh`。也就是说**部署逻辑属于 release 内容**，改构建/
激活/回滚流程正常合入 master 即可生效，不需要再登服务器同步；只有入口点自身的契约
变化（新增 verb、改上传协议）才需要重新安装那份文件。pdf2zh 首次上线失败就是因为
`/usr/local/sbin` 里还是只构建 `app` 的旧副本——`tests/test_deploy_compose_wiring.py`
会守住这个分工（入口点里不允许出现 `docker compose`）。

入口点的 verb 既可以从 `SSH_ORIGINAL_COMMAND`（CI 的强制命令）取，也可以直接从参数
取，所以手工检查用 `/usr/local/sbin/deploy-paper-insight status` 就行；`deploy` 从 stdin
读归档，在终端里会直接报错退出，只应由 CI 驱动。

**权威路径只有一个**：部署密钥 `/root/.ssh/authorized_keys` 里 `command="..."` 写的那个
文件。不要假设它就是文档里的 `/usr/local/sbin/deploy-paper-insight`——副本放在别的路径
上时，从行为上完全看不出差别（新旧脚本日志几乎一样）。每次调用入口点都会在第一行打印
自身 sha256，部署日志因此能直接指认跑的是哪一份：

```text
[paper-insight-deploy] entrypoint sha256=87b33f08bf19… verb=deploy
```

这个摘要和仓库里的对不上，就说明强制命令指向的文件是旧的。装完/换完入口点要核对：

```bash
sha256sum "$(grep -o 'command="[^"]*"' /root/.ssh/authorized_keys | sed 's/command="//;s/"$//')"
curl -fsSL https://raw.githubusercontent.com/Thebearcoding/paper-insight/master/deploy/personal/deploy-entrypoint.sh | sha256sum
```

两个摘要必须一致。从 Windows 工作区拷过去的话先 `sed -i 's/\r$//'` 去掉 CRLF，否则
shebang 解析不了。
- Caddy 反代：`127.0.0.1:8000`

日常更新通过 GitHub Actions 完成。推送到个人仓库的 `master` 后，后端测试、前端测试、Lint 和生产构建全部通过才会连接服务器：

```bash
git push origin master
```

部署任务会把已测试的提交上传到独立 release 目录，保留服务器上的 `.env`、`config.yaml` 和 Docker 数据卷，健康检查通过后再切换当前版本。

部署后验证：

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://paper.athebear.me/
curl -sS "https://paper.athebear.me/conference/iclr_2026/papers?limit=1"
```

端口策略必须保持：

- Caddy 对外监听 `80/443`
- Docker app 只绑定 `127.0.0.1:8000->8000`
- Docker Postgres 只绑定到 `127.0.0.1` 上配置的数据库端口
- Docker Typesense 只绑定 `127.0.0.1:8108->8108`

不要把应用端口、数据库端口或 Typesense 的 `8108` 端口暴露到公网。

## PDF 翻译（pdf2zh）

`docker-compose.yml` 里的 `pdf2zh` 服务（`docker/pdf2zh/`）提供 PDF 中文翻译，容器内自带 Redis、Celery worker 和 Flask API（`11008`），不对外映射端口。

### 产物只落在用户本机

翻译结果（mono 纯中文 / dual 中英对照）保存在 **容器内 Redis** 里，用户点击下载时由 app 侧 `StreamingResponse` 直接转发到浏览器（`Content-Disposition: attachment`），文件存在用户自己的机器上。服务器磁盘上不留翻译产物：

- `paper_translations` 表只存任务指针（`remote_task_id`）与状态，不存正文
- Redis 关闭 RDB/AOF 持久化（`--save '' --appendonly no --dir /tmp`），只占内存；`--maxmemory-policy volatile-lru` 控制上限（只淘汰带 TTL 的结果 key，broker 队列 key 不会被挤掉），Celery 默认 `result_expires` 让结果最多留 24 小时
- pdf2zh/babeldoc 会把每句原文+译文写进 `~/.cache/{pdf2zh,babeldoc}/cache.v1.db`（sqlite 翻译记忆，会随翻译篇数无限增长），`entrypoint.sh` 启动时清理一次、之后每 30 分钟清理一次
- 容器**不挂持久卷**：doclayout onnx 模型和中文衬线字体在镜像构建时烤进镜像层，避免运行时反复下载或积累文件

pdf2zh 容器重启、Redis 淘汰或结果过期后，历史翻译会被标记成 `expired`，前端提示重新翻译。

### 配置

`config.yaml`（非密部分）：

```yaml
pdf_translation:
  enabled: true
  service: openai:deepseek-v4-flash   # 不调 LLM 的话可用 bing（会限流）；google 基本被限流
  lang_in: en
  lang_out: zh
  openai_base_url: https://agentrouter.org/v1
  openai_model: deepseek-v4-flash
```

`.env`（只有 API Key 放这里）：

```bash
PDF_TRANSLATION_OPENAI_API_KEY=sk-...
```

- `service: openai:<模型名>` 走 OpenAI 兼容网关，`docker-compose.yml` 会把 `PDF_TRANSLATION_OPENAI_*` 注入 app，再由 app 随每次请求转给 pdf2zh
- 依赖钉版本：`pdf2zh[backend]==1.9.4` + `tencentcloud-sdk-python-tmt==3.1.121`（3.1.129 起 `import pdf2zh` 直接失败）+ `numpy>=2.0.2,<3`。numpy 不能用 1.x——pdf2zh 依赖的 babeldoc 0.1.x 全部要求 `numpy>=2.0.2`，钉 `numpy<2` 会让 pip 解析失败、镜像根本构建不出来（pdf2zh 首次上线的部署失败根因之一）
- NumPy 2 移除了 `np.fromstring` 的二进制模式，而 pdf2zh 的 `translate_patch` 和 babeldoc 的 `docvision` 仍在用（每页都会 ValueError），所以 `docker/pdf2zh/sitecustomize.py` 用 `np.frombuffer(...).copy()` 补了一个等价实现，`docker-images.yml` 的冒烟测试会在容器里实跑一次 `np.fromstring` 和 `import pdf2zh`
- agentrouter 会按客户端 UA 拒绝请求（不带伪装 UA 直接 `401 unauthorized client detected`），同一个 `sitecustomize.py` 已通过自定义 httpx transport 改写 `User-Agent`，不要再给 pdf2zh 传 `OPENAI_BASE_URL` 之类的环境变量绕过它
- pdf2zh 容器需要能访问该网关；`.env` 里的 `OUTBOUND_PROXY_URL` 会同时注入 app 和 pdf2zh
- 内存：`PDF2ZH_REDIS_MAXMEMORY`（个人 overlay 默认 `128mb`，base 默认 `512mb`）、`PDF2ZH_CELERY_CONCURRENCY`（默认 `1`）；容器上限见 `docker-compose.personal.yml` 的 `mem_limit: 768m`
- 2GB 机器的内存是超配的：postgres 384m + typesense 512m + app 320m + pdf2zh 768m + caddy 80m ≈ 2.06GB（对比 1.87GB 物理内存 + 4GB swap），`mem_limit` 只是上限不是预留，靠各容器不会同时吃满才跑得动。深度分析和翻译并发时如果容器被 OOM 杀掉，先降 `PDF2ZH_REDIS_MAXMEMORY` 或 `PDF2ZH_CELERY_CONCURRENCY`，再考虑调低 app 的 `mem_limit`
- Redis 用 `maxmemory-policy volatile-lru`：只淘汰带 TTL 的结果 key，broker 的队列 key 没有 TTL 因此不会被挤掉。译文（尤其 dual）不小——一篇 20MB 的论文 dual 产物约 40MB，所以 256MB 缓存只留得住最近一两篇，更早的会被标成 `expired` 让用户重翻
- 该 Key 已用 `POST /v1/chat/completions` 实测：带伪装 UA 能正常返回补全，不带则 `401 unauthorized client detected`，说明网关与 UA patch 都按预期工作
- 本机没有 Docker/Postgres，pdf2zh 的 Flask 契约（`/v1/translate` 收 form 字段 `data`，JSON 里的 `envs` 由 `translate_stream` 透传给 `OpenAITranslator`；产物由 `send_file` 从 Celery 的 Redis 结果后端读出）是通过读 1.9.4 的 `backend.py` / `translator.py` 核对的，不是跑出来的

### 容器内的启动脚本

上游 `pdf2zh` 1.9.4 的 CLI 直接用不了，`docker/pdf2zh/` 里用两个小脚本代替：

- `worker.py`：容器里唯一的 Python 入口。pdf2zh 的 argparse 会拒绝 Celery 自己的 `--loglevel` / `--concurrency`（`unrecognized arguments`），worker 根本起不来；而直接跑 `celery -A pdf2zh.backend:celery_app worker` 又会让 `ModelInstance.value` 为空，`translate_stream` 每页都会 `NoneType.predict` 崩。所以这里以 `argv=["worker", ...]` 交给 Celery，并在 `pdf2zh.doclayout.ModelInstance.value` 上装一个惰性描述符：第一次读它才建 doclayout session（~80MB 匿名内存）。唯一读它的是上游 `pdf2zh/backend.py` 里 `translate_task` 的 `model=ModelInstance.value`，也就是 celery 的 task 子进程，配合 `--max-tasks-per-child=1` 让这份内存在子进程退出时还给内核
- `server.py`：`pdf2zh --flask` 调用的是 `flask_app.run(port=11008)`，Flask 默认只绑 `127.0.0.1`，别的容器连不上，所以显式绑 `0.0.0.0`。它不再是独立进程：`worker.py` import 完整套依赖后用 `os.fork()` 把它 fork 出来，父子共享那份 ~128MB 的 import 堆（独立起一遍就是第二份拷贝，实测 Flask 进程私有匿名 128MB、容器空转时 cgroup 用量 381MB）

`entrypoint.sh` 只启动 `worker.py` 一个进程，由 `worker.py` 自己盯着 fork 出来的 Flask 子进程：任一角色退出就让容器退出（交给 `restart: unless-stopped` 重启），避免 worker 死了但容器还健康、任务永远停在 `pending`。`healthcheck.sh` 检查 Redis 应答、Flask 端口可连、pdf2zh 进程存活；CI 部署用 `docker compose up --wait`，所以 pdf2zh 不健康会导致部署回滚。

### 镜像构建

部署脚本激活前会执行不带服务名的 `docker compose build`，也就是说**所有声明了
`build:` 的服务（`app` 和 `pdf2zh`）都由部署流程自己构建**，不需要在服务器上手动
准备镜像。首次构建 pdf2zh 要装 `pdf2zh[backend]` 依赖并预下载 doclayout 模型和中文
字体（镜像约 2GB），所以 CI 的 deploy job 超时放宽到 60 分钟；依赖层和模型层之后都在
Docker 构建缓存里，改 `docker/pdf2zh/` 下的脚本只需要几十秒重建。

**但缓存一旦失效（改 Dockerfile、清构建缓存、换机器），构建速度就取决于源站的可达
性**。国内服务器实测 apt(deb.debian.org) + pip(pypi.org) 只有几十 KB/s，冷构建要一两
个小时，直接超过 deploy job 的 60 分钟超时——2026-09 手工构建时 apt 一层就跑了十几
分钟。所以 `docker/pdf2zh/Dockerfile` 认三个可选构建参数，默认留空（= 与改动前完全
一致，`docker-images.yml` 的构建校验就是这么跑的），`.env` 里打开即可：

```bash
DEBIAN_MIRROR=https://mirrors.aliyun.com          # 重写 deb822 sources 的 apt 源
PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple   # pip -i
HF_ENDPOINT=https://hf-mirror.com                 # babeldoc 取 doclayout 模型的 hf_hub_download
```

注意 `PYPI_INDEX_URL` 和 app 用的 `PYPI_FILES_MIRROR` 语义不同：后者是 uv.lock 里
`files.pythonhosted.org` 的文件下载基址（`.../pypi/packages`），前者是 pip 要的
simple 索引（`.../pypi/simple`），不要互相顶替。中文衬线字体是从 GitHub 下载的，
不受 `HF_ENDPOINT` 影响，国内只是慢。

`tests/test_deploy_compose_wiring.py` 会检查部署脚本是否覆盖了所有 build-only
服务：新增这类服务时如果脚本又写死了服务名，pytest 会直接失败，避免再出现
`No such image: paper-insight-pdf2zh:latest` 这种只在生产激活时暴露的问题。

`.github/workflows/docker-images.yml` 在 `docker/**` 或 compose 文件变化时才跑：
校验两套 Compose 组合的 `config`，构建 pdf2zh 镜像并用镜像自带的 healthcheck 做
冒烟测试。CI 里不再需要单独构建 `app`（前端 job 已经覆盖构建产物）。

万一需要手动重建（例如构建缓存被清理后想提前预热），在服务器上执行：

```bash
cd /opt/paper-insight/current
docker compose --env-file /opt/paper-insight/.env --project-name paper-insight \
  -f docker-compose.yml -f docker-compose.personal.yml build pdf2zh
```

## 项目结构

```text
paper-insight/
├── backend/
│   ├── app.py              # FastAPI 主应用
│   ├── auth.py             # 密码哈希与 session token 工具
│   ├── chat.py             # 聊天会话管理
│   ├── config.py           # config.yaml 读取逻辑
│   ├── database.py         # PostgreSQL 数据库操作
│   ├── github_oauth.py     # GitHub OAuth 逻辑
│   ├── hf_daily.py         # Hugging Face Daily Papers 同步逻辑
│   ├── llm.py              # LLM 调用封装
│   ├── migrations.py       # SQL migration 执行器
│   ├── pdf_translation.py  # pdf2zh 翻译客户端（提交/轮询/流式转发）
│   ├── prompt.py           # 系统提示词
│   └── utils.py            # 工具函数
├── db/
│   ├── migrations/         # PostgreSQL schema、索引和搜索函数
│   └── seeds/              # 本地开发小样本数据
├── docker/
│   └── pdf2zh/             # 翻译服务镜像：Dockerfile / entrypoint / worker / server / healthcheck / UA patch
├── frontend-react/
│   ├── src/                # React 前端源码
│   ├── dist/               # 前端构建产物
│   └── vite.config.ts      # Vite 配置
├── scripts/
│   ├── apply_migrations.py # 执行 migration / seed
│   ├── docker_compose.py   # 根据 config.yaml 启动 Docker Compose
│   └── import_papers.py    # 批量导入论文
├── config.yaml.example     # 运行时配置模板
└── crawled_data/           # 爬虫数据存储，本仓库不提交
```

## 常用检查

```bash
uv run pytest
cd frontend-react && npm run build
git diff --check
```
