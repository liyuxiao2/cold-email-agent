-- 009_outreach_reclaim_count.sql
--
-- Tracks how many times drafting_recovery_task has reclaimed an outreach row
-- from a stale 'drafting' claim back to 'queued' (a hard Celery process
-- crash — SIGKILL, OOM, container eviction — between claim_pending_drafts and
-- the row finishing, which the per-row `except Exception` in drafting_task
-- never sees since it isn't a Python exception). Capped by
-- MAX_DRAFTING_RECLAIMS so a row that keeps crashing the worker eventually
-- gets dead-lettered instead of silently re-queued forever.
--
-- Distinct from dead_letter.retry_count, which counts human-initiated retries
-- of an already-dead-lettered row (POST /api/dlq/retry) — this counts
-- crash-recovery attempts that happen BEFORE the row is ever dead-lettered,
-- so it has to live on the row itself.
--
-- ADD COLUMN IF NOT EXISTS, same as 008_user_llm_and_quota.sql: create_all
-- only CREATEs tables, it never ALTERs one that already exists (R43), so this
-- is applied on every boot via scripts/apply_storage.py, not just once.

ALTER TABLE outreach ADD COLUMN IF NOT EXISTS reclaim_count INTEGER NOT NULL DEFAULT 0;
