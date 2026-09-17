# Architecture

## Directory Structure

```
paper-insight/
├── backend/               # FastAPI 应用（uv 管理）
│   ├── app.py             # 主应用：90+ 路由、SSE 生成器、6 个后台调度器（~4000 行）
│   ├── database.py        # 唯一数据访问层，psycopg3 + 重试 + Typesense 同步（~5700 行）
│   ├── llm.py             # LLM 网关：多供应商热切换、流式 chunk、token 计量
│   ├── zotero.py          # Zotero Web API 客户端 + 阅读上下文构建（token 预算裁剪）
│   ├── paper_figures.py   # arXiv HTML/PDF 提取架构图与 SOTA 结果表
│   ├── paper_resources.py # 公开文档多源解析（arXiv/CVF/DOI）+ GitHub README 抓取
│   ├── pdf_translation.py # pdf2zh 翻译客户端：提交/轮询/流式转发，产物不落本机磁盘
│   ├── typesense_search.py# 混合检索（PG 为真相源，命中后回 PG 水合）
│   └── ...                # auth/chat/config/hf_daily/arxiv/openalex 等功能模块
├── frontend-react/        # React 19 SPA（无路由库）
│   └── src/
│       ├── pages/         # 13 个页面组件（admin ~1830 行、zotero-item ~685 行为体量热点）
│       ├── components/    # chat-panel(~835行)、paper-card、rich-content(Markdown+KaTeX) 等
│       ├── lib/           # api.ts(fetch+SSE 手写解析)、router.ts(自研 history 路由)、auth.ts
│       ├── hooks/         # use-reading-overview、use-zotero-paper-metadata
│       └── types/         # 单文件域类型（~700 行）
├── db/
│   ├── migrations/        # 27 个 SQL migration，启动时按文件名序自动执行
│   └── seeds/             # 顶会种子数据
├── crawler/               # OpenReview 等离线爬虫 → crawled_data/（不入库 git）
├── scripts/               # apply_migrations / import_papers / docker_compose
└── tests/                 # 后端 pytest（38 个文件，sys.path 直插 backend/）
```

## Components

| Component | Responsibility |
|-----------|---------------|
| app.py lifespan | 启动时跑 migration、引导管理员、启动 6 个后台任务（HF Daily 22:00 / 飞书推送 10:00 / 后台分析 / 在线快照 / Typesense 索引） |
| database.py | 全部 SQL；写入后同步 Typesense 索引；搜索走 RPC `search_papers_optimized` |
| llm.py ManagedLLM | 每次请求实时读 DB 激活供应商，支持按请求 select(provider, model) |
| BackgroundAnalyzer | 单循环三阶段：未分析论文 → 代码开源状态 → 关键词补全 |
| ZoteroClient | 增量同步(since version)、PDF 全文、笔记+标签回写 |
| lib/api.ts streamSse | 手写 SSE 分帧解析，供论文分析与 Chat 复用 |
| chat-panel.tsx | 论文/Zotero 通用聊天面板，会话历史分组 + 模型选择 |
| pdf2zh 容器 | 独立服务（Flask + Celery + 容器内 Redis）；翻译产物只在 Redis 结果后端（内存、LRU、24h 过期），`pdf_translations` 表仅记录任务指针与状态；无持久卷，sqlite 翻译记忆启动时与每 30 分钟清理 |

## Data Flow

论文三条入库路径：HF Daily 定时抓取（id 前缀 `hf:`）、用户导入 arXiv、访问时懒加载 OpenReview，全部 upsert 进 `papers` 中心表。分析两条路径：前台 SSE 按需生成（`GET /paper/{id}`，命中 llm_response 缓存直接返回）与后台批量补全。搜索三级降级：Typesense 混合检索 → PG FTS RPC → 内存缓存。Zotero 是独立链路（zotero_items 表，用户私有），分析产物含 figures/enrichment，可回写 Zotero 云端。

## Key Patterns
- 鉴权：cookie session（user_sessions 表）+ `Depends(require_current_user)`，无中间件
- DB 并发：psycopg 同步驱动，全部 `asyncio.to_thread` 包裹，3 次重试
- LLM 流式：SSE events 区分 content/reasoning chunk；GLM 代理 16k→8k token 两轮降级重试
- 前端状态：URL search params 为 source of truth + useState + active 标志防竞态；跨组件用 CustomEvent `paper:mark-changed`
- 匿名数据迁移：localStorage marks → 登录后 `/auth/migrate-anonymous` 上传服务端
- PDF 翻译：用户点击 → app 下载原 PDF 提交 pdf2zh → 轮询 celery 状态；下载时 app 从 pdf2zh 流式转发（`StreamingResponse`）直接给浏览器，服务器磁盘不留产物（pdf2zh 侧只有内存里的 Redis 结果 + 定时清理的 sqlite 翻译记忆）；pdf2zh 重启/Redis 淘汰/结果过期后旧结果标记为 `expired`，前端提示重新翻译
- 部署：GitHub Actions 推 master → 测试全过 → 服务器 release 目录切换，保留 .env/config.yaml/数据卷

## 核心表关系

`papers`（中心，id 如 `hf:2401.xxxxx`）← authors/keywords/arxiv_papers/hf_daily_papers/paper_marks/chat_sessions；`users` ← user_sessions/auth_identities/paper_marks/zotero_connections/zotero_items；`llm_providers` ← llm_models/llm_token_usage；API 搜索：api_keys/api_usage_daily/user_api_quotas（021）。

## 体量热点（维护关注）

app.py ~4076 行、database.py ~5683 行、admin-page.tsx ~1830 行、chat-panel.tsx ~835 行。测试缺口：app.py 的 Zotero 同步与飞书推送调度器无直接测试；database.py 搜索 RPC 主要靠 test_api_search 间接覆盖。tests/ 39 个文件。
