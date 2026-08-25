ALTER TABLE zotero_items
ADD COLUMN IF NOT EXISTS analysis_enrichment JSONB NOT NULL DEFAULT '{}'::jsonb;
