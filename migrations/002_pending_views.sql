-- 002_pending_views.sql
--
-- 1. Sync the drafts table with the ORM: the Draft model gained a
--    gmail_draft_id column (Gmail's draft resource ID) that 001 predates.
-- 2. Add the pending_drafts view: the set of researched-but-un-drafted leads,
--    pre-joined to their latest research. Defining pending-ness once in the DB
--    means every consumer (drafting worker, ad-hoc psql) queries the same
--    source of truth instead of re-deriving the WHERE clause.

ALTER TABLE drafts ADD COLUMN IF NOT EXISTS gmail_draft_id TEXT;

-- Leads that have been researched but not yet drafted, pre-joined to their
-- most recent research row. DISTINCT ON (l.id) keeps exactly one row per lead;
-- ORDER BY l.id, r.created_at DESC makes that the *latest* research.
CREATE OR REPLACE VIEW pending_drafts AS
SELECT DISTINCT ON (l.id)
    l.id            AS lead_id,
    l.company_name,
    l.founder_name,
    l.founder_email,
    l.company_url,
    r.raw_content,
    r.tech_stack,
    r.recent_news,
    r.hook
FROM leads l
JOIN research r ON r.lead_id = l.id
WHERE l.status = 'researched'
ORDER BY l.id, r.created_at DESC;
