ALTER TABLE zotero_items
  ADD COLUMN IF NOT EXISTS analysis_source TEXT,
  ADD COLUMN IF NOT EXISTS analysis_warning TEXT,
  ADD COLUMN IF NOT EXISTS analysis_provider_id TEXT,
  ADD COLUMN IF NOT EXISTS analysis_provider_name TEXT,
  ADD COLUMN IF NOT EXISTS analysis_model_name TEXT;
