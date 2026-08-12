-- 004_dead_letter.sql
--
-- Dead-letter queue for terminally-failed tasks. handle_terminal_failure marks
-- the lead 'failed' AND inserts a row here, so failures are both visible on the
-- lead and independently retryable via POST /api/dlq/retry. `stage` tells the
-- retry which worker to re-dispatch to.

CREATE TABLE IF NOT EXISTS dead_letter (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id         UUID REFERENCES leads(id) ON DELETE CASCADE,
    task_name       TEXT NOT NULL,
    stage           TEXT NOT NULL,          -- research | drafting | logistics
    error_msg       TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_retried_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS dead_letter_lead_id_idx ON dead_letter (lead_id);
CREATE INDEX IF NOT EXISTS dead_letter_stage_idx ON dead_letter (stage);
