CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE llm_providers
  ADD COLUMN IF NOT EXISTS encrypted_api_key BYTEA;

COMMENT ON COLUMN llm_providers.api_key IS
  'Legacy plaintext field. Application startup migrates values to encrypted_api_key and clears this column.';

COMMENT ON COLUMN llm_providers.encrypted_api_key IS
  'API key encrypted with pgp_sym_encrypt and the server-side llm credential encryption key.';
