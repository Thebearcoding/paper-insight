ALTER TABLE zotero_items
ADD COLUMN IF NOT EXISTS analysis_figures JSONB NOT NULL DEFAULT '[]'::jsonb;
