-- Private translation task pointers, scoped to the owning Zotero item and attachment.
-- pdf2zh holds results in its temporary cache; no attachment bytes are stored here.
CREATE TABLE zotero_attachment_translations (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL,
    item_key TEXT NOT NULL,
    attachment_key TEXT NOT NULL,
    attachment_version BIGINT NOT NULL,
    lang_out TEXT NOT NULL,
    service TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress INT NOT NULL DEFAULT 0,
    remote_task_id TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (user_id, item_key) REFERENCES zotero_items(user_id, item_key) ON DELETE CASCADE,
    FOREIGN KEY (user_id, attachment_key) REFERENCES zotero_items(user_id, item_key) ON DELETE CASCADE,
    UNIQUE (user_id, item_key, attachment_key, attachment_version, lang_out, service)
);
CREATE INDEX zotero_attachment_translations_owner_idx
    ON zotero_attachment_translations (user_id, item_key, attachment_key);
