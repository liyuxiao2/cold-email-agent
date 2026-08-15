-- 010_send_cadence.sql
--
-- Per-user send cadence, plus the indexes the due-send scanner and stuck-send
-- reaper read.
--
-- outreach.scheduled_send_at already exists (migration 006) and pending_sends
-- already filters on it (`scheduled_send_at IS NULL OR <= now()`), so nothing
-- about the outreach table's COLUMNS changes here — only new indexes and a
-- new column on `users`.
--
-- Numbered 010, not 009: 009 was already taken by
-- 009_outreach_reclaim_count.sql by the time this was written.
--
-- Applied on every boot via scripts/apply_storage.py (ADD COLUMN IF NOT
-- EXISTS / CREATE INDEX IF NOT EXISTS, same idiom as 008/009): create_all
-- only ever issues CREATE TABLE, never ALTER TABLE, so on any database where
-- `users` and `outreach` already exist (i.e. every production deploy after
-- the first), a plain create_all boot would never apply this migration on
-- its own.

-- JSONB, not five columns: the object is always read and written whole, never
-- queried into, and it will grow (min_gap_minutes, per-day overrides) without
-- a migration each time. NULL means "send immediately on approve".
ALTER TABLE users ADD COLUMN IF NOT EXISTS send_cadence JSONB;

-- PARTIAL index. The scanner runs every 5 minutes forever, and 'sent' rows
-- will eventually dominate the table. Indexing all statuses would grow an
-- index without bound while only a sliver is ever read.
CREATE INDEX IF NOT EXISTS outreach_due_idx ON outreach (scheduled_send_at)
    WHERE status = 'approved';

-- Rows the scanner claimed but whose outcome is unknown (worker crashed
-- mid-flight). Reaped into the DLQ after 30 minutes, never auto-retried.
CREATE INDEX IF NOT EXISTS outreach_sending_idx ON outreach (updated_at)
    WHERE status = 'sending';
