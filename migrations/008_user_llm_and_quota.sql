-- 008_user_llm_and_quota.sql
--
-- Optional bring-your-own-key, plus a per-user monthly draft quota.
--
-- Quota is per-user rather than a single global constant so it can be raised for
-- an individual without a deploy — and it is the seam Stripe plans attach to.

ALTER TABLE users ADD COLUMN IF NOT EXISTS llm_api_key_enc     BYTEA;  -- Fernet
ALTER TABLE users ADD COLUMN IF NOT EXISTS llm_provider        TEXT;   -- groq | gemini
ALTER TABLE users ADD COLUMN IF NOT EXISTS monthly_draft_quota INT NOT NULL DEFAULT 100;
