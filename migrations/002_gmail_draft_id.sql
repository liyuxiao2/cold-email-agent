-- Store the Gmail draft resource ID so the approve/send step can later
-- send or delete the exact draft this pipeline created.
ALTER TABLE drafts ADD COLUMN gmail_draft_id TEXT;
