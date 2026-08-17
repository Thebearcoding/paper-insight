CREATE TABLE IF NOT EXISTS zotero_connections (
  user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  encrypted_api_key BYTEA NOT NULL,
  zotero_user_id BIGINT NOT NULL,
  username TEXT,
  display_name TEXT,
  can_read BOOLEAN NOT NULL DEFAULT TRUE,
  can_write BOOLEAN NOT NULL DEFAULT FALSE,
  library_version BIGINT NOT NULL DEFAULT 0,
  sync_status TEXT NOT NULL DEFAULT 'idle'
    CHECK (sync_status IN ('idle', 'running', 'error')),
  last_sync_at TIMESTAMPTZ,
  last_sync_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS zotero_collections (
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  collection_key TEXT NOT NULL,
  collection_version BIGINT NOT NULL DEFAULT 0,
  name TEXT NOT NULL,
  parent_collection TEXT,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, collection_key)
);

CREATE INDEX IF NOT EXISTS idx_zotero_collections_user_parent
ON zotero_collections(user_id, parent_collection, name);

CREATE TABLE IF NOT EXISTS zotero_items (
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  item_key TEXT NOT NULL,
  item_version BIGINT NOT NULL DEFAULT 0,
  item_type TEXT NOT NULL,
  parent_item_key TEXT,
  title TEXT,
  abstract_note TEXT,
  publication_title TEXT,
  item_date TEXT,
  doi TEXT,
  url TEXT,
  creators JSONB NOT NULL DEFAULT '[]'::jsonb,
  tags JSONB NOT NULL DEFAULT '[]'::jsonb,
  collections JSONB NOT NULL DEFAULT '[]'::jsonb,
  content_type TEXT,
  link_mode TEXT,
  filename TEXT,
  note TEXT,
  annotation_text TEXT,
  annotation_comment TEXT,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb,
  llm_response TEXT,
  analyzed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, item_key)
);

CREATE INDEX IF NOT EXISTS idx_zotero_items_user_parent
ON zotero_items(user_id, parent_item_key, item_type);

CREATE INDEX IF NOT EXISTS idx_zotero_items_user_updated
ON zotero_items(user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_zotero_items_user_title
ON zotero_items(user_id, lower(title));

CREATE INDEX IF NOT EXISTS idx_zotero_items_collections
ON zotero_items USING GIN(collections);

CREATE TABLE IF NOT EXISTS zotero_chat_sessions (
  id TEXT PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  item_key TEXT NOT NULL,
  title TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  FOREIGN KEY (user_id, item_key)
    REFERENCES zotero_items(user_id, item_key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_zotero_chat_sessions_user_item
ON zotero_chat_sessions(user_id, item_key, created_at DESC);

CREATE TABLE IF NOT EXISTS zotero_chat_messages (
  id BIGSERIAL PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES zotero_chat_sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_zotero_chat_messages_session
ON zotero_chat_messages(session_id, created_at, id);
