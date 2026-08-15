-- 005_users.sql
--
-- The users table: one row per authenticated person. Introduced by Stack 1a
-- (multi-tenant revamp). The data model split (companies / company_contacts /
-- outreach) is Stack 1b; nothing here references leads.
--
-- google_sub is nullable so an admin row can be seeded by email before that
-- person's first sign-in; the OAuth callback fills it in and matches on it
-- thereafter. Matching on google_sub rather than email is deliberate — Google
-- account emails can change, but the subject id cannot.
--
-- gmail_refresh_token_enc holds Fernet ciphertext, never a plaintext token.

CREATE TABLE IF NOT EXISTS users (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    google_sub              TEXT UNIQUE,
    email                   TEXT UNIQUE NOT NULL,
    name                    TEXT,
    picture_url             TEXT,
    role                    TEXT NOT NULL DEFAULT 'user',   -- user | admin
    gmail_refresh_token_enc BYTEA,
    gmail_sender_email      TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS users_google_sub_idx ON users (google_sub);
