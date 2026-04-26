CREATE TABLE IF NOT EXISTS leads (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name  TEXT NOT NULL,
    founder_name  TEXT,
    founder_email TEXT,
    linkedin_url  TEXT,
    company_url   TEXT,
    funding_stage TEXT,
    headcount     INT,
    status        TEXT NOT NULL DEFAULT 'found',
    error_msg     TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_leads_company_name ON leads (company_name);

CREATE TABLE IF NOT EXISTS research (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id       UUID REFERENCES leads(id) ON DELETE CASCADE,
    tech_stack    JSONB,
    recent_news   TEXT,
    hook          TEXT,
    raw_content   TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS drafts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id         UUID REFERENCES leads(id) ON DELETE CASCADE,
    subject_line    TEXT NOT NULL,
    body            TEXT NOT NULL,
    version         INT DEFAULT 1,
    reviewer_notes  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
