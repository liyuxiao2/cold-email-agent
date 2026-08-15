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
-- create_all step). It is NOT fully self-healing via CREATE OR REPLACE VIEW
-- alone, despite that being the original intent: Postgres refuses to let
-- CREATE OR REPLACE VIEW rename, drop, or reorder an existing view's output
-- columns (only appending trailing columns is allowed). This stack's own
-- transition proves it — against a production database that still has the
-- pre-migration-006 pending_drafts (whose first column is `lead_id`), running
-- this file's `... AS outreach_id` definition fails with "cannot change name
-- of view column \"lead_id\" to \"outreach_id\"". And scripts/apply_views.py
-- runs this whole file as ONE multi-statement string via exec_driver_sql, so
-- that single failure aborts before any of the three CREATE OR REPLACE
-- statements below take effect — not just the one whose columns changed.
-- Hence the explicit DROP VIEW IF EXISTS ahead of every CREATE: dropping and
-- recreating is what actually makes a column-shape change self-healing.
--
-- These same three view definitions also live in
-- migrations/006_multi_tenant_schema.sql (with a DROP VIEW IF EXISTS list of
-- its own, since that migration only ever runs once against a fresh pre-006
-- database), so that a migration-only provisioning path still works without
-- this file. That is intentional duplication: do NOT "de-duplicate" this file
-- by deleting one copy — doing so breaks whichever of the two provisioning
-- paths (create_all+this file, or the migration files) no longer defines the
-- view.

DROP VIEW IF EXISTS pending_drafts;
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
    ct.last_name    AS contact_last_name,
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
DROP VIEW IF EXISTS pending_sends;
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
DROP VIEW IF EXISTS available_contacts;
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
