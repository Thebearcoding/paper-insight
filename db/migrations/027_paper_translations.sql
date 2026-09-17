-- PDF 翻译任务（pdf2zh）。翻译产物不落本服务磁盘：结果保留在 pdf2zh 服务的
-- Redis 结果后端里，用户下载时由本服务流式转发，因此这里只存任务指针与状态。
CREATE TABLE IF NOT EXISTS paper_translations (
    id BIGSERIAL PRIMARY KEY,
    paper_id TEXT NOT NULL,
    pdf_url TEXT NOT NULL,
    lang_out TEXT NOT NULL DEFAULT 'zh',
    service TEXT NOT NULL DEFAULT 'google',
    status TEXT NOT NULL DEFAULT 'pending',
    progress INT NOT NULL DEFAULT 0,
    remote_task_id TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS paper_translations_cache_key
    ON paper_translations (paper_id, pdf_url, lang_out, service);

CREATE INDEX IF NOT EXISTS paper_translations_paper_id_idx
    ON paper_translations (paper_id);
