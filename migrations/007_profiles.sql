-- 007_profiles.sql
--
-- Per-user sender identity. Replaces sender_profile.PROFILE (a frozen
-- module-level dataclass) and the resume.txt / resume.pdf files that were
-- committed to the repo.
--
-- user_id is the PRIMARY KEY: one profile per user, enforced by the schema
-- rather than a unique constraint on a surrogate id.

CREATE TABLE IF NOT EXISTS profiles (
    user_id         UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    intro           TEXT NOT NULL,
    linkedin        TEXT,
    github          TEXT,
    website         TEXT,
    -- JSONB, not child tables: always read and written whole, never queried
    -- into, and the SenderProfile dataclass already models them as a list and
    -- a dict. Child tables would add joins and buy nothing.
    experience_pool JSONB NOT NULL DEFAULT '[]'::jsonb,
    company_links   JSONB NOT NULL DEFAULT '{}'::jsonb,
    resume_pdf      BYTEA,
    resume_filename TEXT,
    resume_text     TEXT,
    parsed_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Postgres pages are 8KB, so a ~400KB PDF is stored out-of-line in the TOAST
-- side table with an 18-byte pointer in the heap row — SELECT name FROM profiles
-- never touches the bytes.
--
-- EXTERNAL rather than the default EXTENDED: PDFs are already compressed, so
-- Postgres's compression attempt burns CPU on every write for no size gain.
--
-- Also re-applied on every boot via migrations/storage.sql (see
-- scripts/apply_storage.py, invoked from scripts/start.sh) because production
-- provisions its schema with Base.metadata.create_all, not by running this
-- migration file (R32) — SQLAlchemy has no way to express column storage
-- strategy, so create_all alone would leave this column at the default
-- EXTENDED. Kept here too so a migration-only provisioning path also works.
ALTER TABLE profiles ALTER COLUMN resume_pdf SET STORAGE EXTERNAL;
