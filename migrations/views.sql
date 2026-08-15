-- views.sql
--
-- R23: production has no database VIEWS from a plain schema provision.
-- scripts/start.sh provisions the schema with Base.metadata.create_all, and
-- SQLAlchemy's metadata does not model views — so on a create_all-only
-- database, pending_drafts / pending_sends / available_contacts simply don't
-- exist, and the drafting and logistics workers (which read them) would fail
-- every tick with "relation does not exist".
--
-- This file is applied on EVERY boot (see scripts/start.sh, right after the
-- create_all step). CREATE OR REPLACE VIEW makes that idempotent — re-running
-- it is a no-op if the definition hasn't changed — and it means the database
-- SELF-HEALS whenever a view's definition changes here: the next boot picks
-- it up with no separate migration step required.
--
-- These same three view definitions also live in
-- migrations/006_multi_tenant_schema.sql (as plain CREATE VIEW, since that
-- migration only ever runs once against a fresh pre-006 database), so that a
-- migration-only provisioning path still works without this file. That is
-- intentional duplication: CREATE OR REPLACE VIEW makes re-declaring the same
-- view harmless, so do NOT "de-duplicate" this file by deleting one copy —
-- doing so breaks whichever of the two provisioning paths (create_all+this
-- file, or the migration files) no longer defines the view.

CREATE OR REPLACE VIEW pending_drafts AS
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
CREATE OR REPLACE VIEW pending_sends AS
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
CREATE OR REPLACE VIEW available_contacts AS
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
