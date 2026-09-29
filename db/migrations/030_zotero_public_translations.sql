-- Public-PDF translation pointers are separate from private Zotero attachment tasks.
CREATE TABLE IF NOT EXISTS zotero_public_translations (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL,
    item_key TEXT NOT NULL,
    item_version BIGINT NOT NULL,
    lang_out TEXT NOT NULL,
    service TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress INT NOT NULL DEFAULT 0,
    remote_task_id TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (user_id, item_key) REFERENCES zotero_items(user_id, item_key) ON DELETE CASCADE,
    UNIQUE (user_id, item_key, item_version, lang_out, service)
);
