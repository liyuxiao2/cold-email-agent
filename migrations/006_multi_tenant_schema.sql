-- 006_multi_tenant_schema.sql
--
-- Splits `leads` into global company facts and per-user outreach state, and
-- replaces the single founder_email with a pool of company_contacts.
--
-- THE KEY TRICK: companies.id reuses leads.id verbatim. Because the UUIDs carry
-- over, research.lead_id -> company_id is a pure column rename, dead_letter
-- research rows map directly, and no ID translation table is needed anywhere.
--
-- `leads` is RENAMED to leads_legacy, never dropped, so a bad deploy is
-- recoverable without restoring a backup. A follow-up PR drops it.
--
-- This database has two provisioning histories: these migration files, and
-- Base.metadata.create_all on every boot (scripts/start.sh). The preflight
-- guards below assert the pre-migration shape rather than assuming either, and
-- the additive DDL is idempotent, so a table create_all already made is
-- adopted instead of colliding. Every guard raises before any DDL runs: a
-- migration that refuses to start beats one that dies halfway through.

BEGIN;

-- ================================================================= PREFLIGHT

-- Abort if Stack 1a's admin seed has not run: there would be nobody to own the
-- backfilled outreach rows, and silently dropping that history is worse than
-- failing the migration.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM users WHERE role = 'admin') THEN
        RAISE EXCEPTION 'No admin user exists. Run scripts/seed_admin.py first.';
    END IF;
END $$;

-- The tables this migration rewrites must be present and still in their
-- pre-migration shape. Checked up front because the alternative is discovering
-- it from a raw Postgres error partway through a production run.
DO $$
BEGIN
    IF to_regclass('leads') IS NULL THEN
        RAISE EXCEPTION
            'Table "leads" is missing. This migration rewrites it; there is nothing to migrate.';
    END IF;

    -- dead_letter came from 004, but create_all builds it from the ORM instead,
    -- so a database provisioned either way can be missing it entirely.
    IF to_regclass('dead_letter') IS NULL THEN
        RAISE EXCEPTION
            'Table "dead_letter" is missing. Apply migrations/004_dead_letter.sql first, then re-run this migration.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'research' AND column_name = 'lead_id'
    ) THEN
        RAISE EXCEPTION
            'Column research.lead_id is missing, so this schema is already migrated (or was built from the post-1b models). Refusing to run 006 again.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'drafts' AND column_name = 'lead_id'
    ) THEN
        RAISE EXCEPTION
            'Column drafts.lead_id is missing, so this schema is already migrated (or was built from the post-1b models). Refusing to run 006 again.';
    END IF;
END $$;

-- Drop the old views up front, before the columns they read disappear.
-- Postgres refuses `ALTER TABLE drafts DROP COLUMN lead_id` while pending_sends
-- still selects it, so these cannot wait until the new views are defined below.
DROP VIEW IF EXISTS pending_drafts;
DROP VIEW IF EXISTS pending_sends;

-- ---------------------------------------------------------------- companies
CREATE TABLE IF NOT EXISTS companies (
    id              UUID PRIMARY KEY,
    company_name    TEXT NOT NULL,
    company_url     TEXT,
    linkedin_url    TEXT,
    founder_name    TEXT,
    funding_stage   TEXT,
    headcount       INT,
    industry        TEXT,
    research_status TEXT NOT NULL DEFAULT 'found',   -- found | researched | failed
    error_msg       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS companies_name_idx   ON companies (company_name);
CREATE INDEX IF NOT EXISTS companies_status_idx ON companies (research_status);

-- ---------------------------------------------------- company_contacts
-- One row per Hunter domain-search result. Ineligible contacts are stored too,
-- so loosening DECISION_MAKER_PATTERNS later can re-classify stored rows
-- instead of re-spending Hunter credits.
CREATE TABLE IF NOT EXISTS company_contacts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    first_name  TEXT,
    last_name   TEXT,
    position    TEXT,
    seniority   TEXT,
    department  TEXT,
    confidence  INT  NOT NULL DEFAULT 0,             -- Hunter 0-100
    is_founder  BOOLEAN NOT NULL DEFAULT false,
    eligible    BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_contact_company_email UNIQUE (company_id, email)
);
-- Partial: selection and pool queries only ever read eligible contacts, so
-- indexing the ineligible ones wastes space and write throughput.
CREATE INDEX IF NOT EXISTS company_contacts_eligible_idx
    ON company_contacts (company_id) WHERE eligible;

-- ----------------------------------------------------------------- outreach
CREATE TABLE IF NOT EXISTS outreach (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id)            ON DELETE CASCADE,
    company_id        UUID NOT NULL REFERENCES companies(id)        ON DELETE CASCADE,
    -- SET NULL, not CASCADE: if a contact is purged (bounce, GDPR), the record
    -- that an email was sent must survive, or the same user could re-email them.
    contact_id        UUID REFERENCES company_contacts(id)          ON DELETE SET NULL,
    status            TEXT NOT NULL DEFAULT 'queued',
    scheduled_send_at TIMESTAMPTZ,                   -- NULL = send immediately
    error_msg         TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_outreach_user_company UNIQUE (user_id, company_id)
);
CREATE INDEX IF NOT EXISTS outreach_user_status_idx ON outreach (user_id, status);
-- For Stack 3's per-contact cap query: COUNT(*) WHERE contact_id = ?
CREATE INDEX IF NOT EXISTS outreach_contact_idx     ON outreach (contact_id);

-- =================================================================== BACKFILL

-- 1. companies <- leads, id carried verbatim.
--    research_status collapses the old lead-level status to the global facts:
--    anything past research proves research succeeded; 'failed' with no email
--    means research failed; everything else is still 'found'.
INSERT INTO companies (
    id, company_name, company_url, linkedin_url, founder_name,
    funding_stage, headcount, research_status, error_msg, created_at, updated_at
)
SELECT
    id, company_name, company_url, linkedin_url, founder_name,
    funding_stage, headcount,
    CASE
        WHEN status IN ('researched', 'drafted', 'approved', 'sent', 'rejected')
            THEN 'researched'
        WHEN status = 'failed' AND founder_email IS NULL THEN 'failed'
        WHEN status = 'failed' AND founder_email IS NOT NULL THEN 'researched'
        ELSE 'found'
    END,
    error_msg, created_at, updated_at
FROM leads;

-- 2. company_contacts <- the single founder_email per lead.
--    confidence = 25 (MIN_EMAIL_SCORE): the real Hunter score was never
--    persisted, and 25 is the floor these addresses demonstrably cleared.
--    The surname is only taken when the stored name actually has a second
--    word: never fabricate a surname that isn't there.
INSERT INTO company_contacts (
    company_id, email, first_name, last_name, is_founder, eligible, confidence
)
SELECT
    id,
    founder_email,
    NULLIF(split_part(COALESCE(founder_name, ''), ' ', 1), ''),
    CASE
        WHEN position(' ' in COALESCE(founder_name, '')) > 0
            THEN NULLIF(
                substring(
                    COALESCE(founder_name, '')
                    from position(' ' in COALESCE(founder_name, '')) + 1
                ),
                ''
            )
    END,
    true,
    true,
    25
FROM leads
WHERE founder_email IS NOT NULL;

-- 3. research: pure rename, IDs already match.
--    First drop the old FK to `leads`. A rename does not follow it, so it would
--    survive as a constraint against leads_legacy — and every company
--    discovered after this migration is absent from that frozen table, so all
--    future research inserts would fail. Looked up by catalog rather than by
--    name because the name depends on whether the table came from 001_initial
--    or from the ORM's create_all.
DO $$
DECLARE
    fk_name TEXT;
BEGIN
    FOR fk_name IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'research'::regclass
          AND confrelid = 'leads'::regclass
          AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE research DROP CONSTRAINT %I', fk_name);
    END LOOP;
END $$;

ALTER TABLE research RENAME COLUMN lead_id TO company_id;
DO $$
BEGIN
    -- Named as Postgres/SQLAlchemy would name it implicitly, so a create_all
    -- boot and this migration produce the same constraint.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'research_company_id_fkey') THEN
        ALTER TABLE research
            ADD CONSTRAINT research_company_id_fkey
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE;
    END IF;
END $$;

-- 4. outreach <- leads that reached drafting or beyond, owned by the admin.
--    'failed' WITH an email is a drafting/send failure (per-user);
--    'failed' WITHOUT one is a research failure (global) and gets no row.
INSERT INTO outreach (user_id, company_id, contact_id, status, error_msg, created_at, updated_at)
SELECT
    (SELECT id FROM users WHERE role = 'admin' ORDER BY created_at LIMIT 1),
    l.id,
    ct.id,
    l.status,
    l.error_msg,
    l.created_at,
    l.updated_at
FROM leads l
LEFT JOIN company_contacts ct ON ct.company_id = l.id
WHERE l.status IN ('drafted', 'approved', 'sent', 'rejected')
   OR (l.status = 'failed' AND l.founder_email IS NOT NULL);

-- 5. drafts: lead_id -> outreach_id via the shared company id.
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS outreach_id UUID REFERENCES outreach(id) ON DELETE CASCADE;
UPDATE drafts d
SET outreach_id = o.id
FROM outreach o
WHERE o.company_id = d.lead_id;

-- Deleting an unmatched draft would destroy a generated email body forever;
-- leads_legacy preserves the lead but not the draft text. Expected count is
-- zero (a draft only exists for a lead that reached 'drafted', and every such
-- lead gets an outreach row above), so this costs nothing normally and stops
-- the deploy for a human to look at when the data disagrees.
DO $$
DECLARE
    orphaned INT;
BEGIN
    SELECT COUNT(*) INTO orphaned FROM drafts WHERE outreach_id IS NULL;
    IF orphaned > 0 THEN
        RAISE EXCEPTION
            'Aborting: % draft row(s) have no matching outreach row. Investigate before re-running; refusing to delete draft bodies.',
            orphaned;
    END IF;
END $$;

ALTER TABLE drafts DROP COLUMN lead_id;
ALTER TABLE drafts ALTER COLUMN outreach_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS drafts_outreach_idx ON drafts (outreach_id);

-- 6. dead_letter: two nullable FKs. Research failures are company-level
--    ("nobody can email them"); drafting/send failures are outreach-level
--    ("this user's draft broke"). One FK would lose that distinction.
ALTER TABLE dead_letter ADD COLUMN IF NOT EXISTS company_id  UUID REFERENCES companies(id) ON DELETE CASCADE;
ALTER TABLE dead_letter ADD COLUMN IF NOT EXISTS outreach_id UUID REFERENCES outreach(id) ON DELETE CASCADE;

UPDATE dead_letter SET company_id = lead_id WHERE stage = 'research';

UPDATE dead_letter dl
SET outreach_id = o.id
FROM outreach o
WHERE o.company_id = dl.lead_id AND dl.stage IN ('drafting', 'logistics');

-- Any non-research row without an outreach match is a pre-existing data
-- inconsistency. Anchor it to the company rather than deleting the record.
UPDATE dead_letter SET company_id = lead_id
WHERE outreach_id IS NULL AND company_id IS NULL;

ALTER TABLE dead_letter DROP COLUMN lead_id;
-- 004 created this; a create_all-provisioned database never had it.
CREATE INDEX IF NOT EXISTS dead_letter_stage_idx ON dead_letter (stage);
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'dead_letter_one_level') THEN
        ALTER TABLE dead_letter
            ADD CONSTRAINT dead_letter_one_level
            CHECK (company_id IS NOT NULL OR outreach_id IS NOT NULL);
    END IF;
END $$;

-- ==================================================================== VIEWS
-- These three views are ALSO defined in migrations/views.sql, applied on
-- every container boot after Base.metadata.create_all (see scripts/start.sh),
-- because create_all does not provision views at all — see R23 in
-- views.sql's header. That is intentional duplication, not drift to clean up:
-- this migration only ever runs once against a fresh pre-006 database, so a
-- migration-only provisioning path still needs its own copy. CREATE OR
-- REPLACE VIEW in views.sql makes re-declaring these harmless, so do NOT
-- delete either copy — that breaks whichever provisioning path relied on it.
CREATE VIEW pending_drafts AS
SELECT DISTINCT ON (o.id)
    o.id            AS outreach_id,
    o.user_id,
    o.company_id,
    o.contact_id,
    c.company_name,
    c.company_url,
    c.founder_name,
    ct.email        AS contact_email,
    ct.first_name   AS contact_first_name,
    ct.position     AS contact_position,
    r.raw_content,
    r.tech_stack,
    r.recent_news,
    r.hook
FROM outreach o
JOIN companies c         ON c.id  = o.company_id
JOIN company_contacts ct ON ct.id = o.contact_id
JOIN research r          ON r.company_id = o.company_id
WHERE o.status = 'queued'
ORDER BY o.id, r.created_at DESC;

-- The scheduled_send_at clause is written now even though nothing sets that
-- column until Stack 4. NULL therefore means "send immediately" from day one,
-- and Stack 4 needs no view migration.
CREATE VIEW pending_sends AS
SELECT DISTINCT ON (o.id)
    o.id          AS outreach_id,
    o.user_id,
    ct.email      AS contact_email,
    d.gmail_draft_id,
    d.subject_line,
    d.body
FROM outreach o
JOIN company_contacts ct ON ct.id = o.contact_id
JOIN drafts d            ON d.outreach_id = o.id
WHERE o.status = 'approved'
  AND (o.scheduled_send_at IS NULL OR o.scheduled_send_at <= now())
ORDER BY o.id, d.created_at DESC;

-- Exposes use_count rather than filtering on the cap: baking K into a view
-- would make changing a business rule require a migration.
CREATE VIEW available_contacts AS
SELECT
    ct.id         AS contact_id,
    ct.company_id,
    ct.confidence,
    ct.is_founder,
    COUNT(o.id)   AS use_count
FROM company_contacts ct
LEFT JOIN outreach o ON o.contact_id = ct.id
WHERE ct.eligible
GROUP BY ct.id;

-- ============================================================ FINALIZE
ALTER TABLE companies ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE leads RENAME TO leads_legacy;

COMMIT;
