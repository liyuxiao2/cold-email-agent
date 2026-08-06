-- 003_pending_sends.sql
--
-- Add the pending_sends view: leads a human has approved but that have not yet
-- been sent, pre-joined to their most recent draft (which carries the
-- gmail_draft_id the logistics worker sends). Same idea as pending_drafts in
-- 002 — pending-ness defined once in the DB, queried by the logistics worker.

CREATE OR REPLACE VIEW pending_sends AS
SELECT DISTINCT ON (l.id)
    l.id            AS lead_id,
    l.founder_email,
    d.gmail_draft_id,
    d.subject_line,
    d.body
FROM leads l
JOIN drafts d ON d.lead_id = l.id
WHERE l.status = 'approved'
ORDER BY l.id, d.created_at DESC;
