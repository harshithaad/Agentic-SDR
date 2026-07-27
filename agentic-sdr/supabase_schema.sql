-- ============================================================
-- Agentic SDR — Supabase Schema
-- Run this in the Supabase SQL editor to create all tables.
-- ============================================================

CREATE TABLE IF NOT EXISTS leads (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name              TEXT NOT NULL,
    website                   TEXT,
    status                    TEXT NOT NULL DEFAULT 'UPLOADED',

    -- Research
    company_summary           TEXT,
    industry                  TEXT,
    employee_size_estimate    TEXT,
    pain_points               JSONB DEFAULT '[]'::jsonb,
    recent_news               JSONB DEFAULT '[]'::jsonb,
    research_confidence_score FLOAT,

    -- Contact
    contact_name              TEXT,
    contact_email             TEXT,
    contact_role              TEXT,

    -- Email draft
    subject_line              TEXT,
    email_body                TEXT,
    personalisation_fact_used TEXT,
    word_count                INTEGER,
    human_approval_required   BOOLEAN DEFAULT FALSE,

    -- Send / reply tracking
    gmail_message_id          TEXT,
    gmail_thread_id           TEXT,
    sent_at                   TIMESTAMPTZ,
    follow_up_sent_at         TIMESTAMPTZ,

    -- Reply fields
    reply_text                TEXT,
    reply_received_at         TIMESTAMPTZ,
    intent                    TEXT,
    intent_confidence         FLOAT,
    intent_reasoning          TEXT,

    -- Booking
    booking_email_draft       TEXT,

    -- Meta
    error_message             TEXT,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_leads_status      ON leads (status);
CREATE INDEX IF NOT EXISTS idx_leads_created_at  ON leads (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_contact_email ON leads (contact_email);

-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id     UUID REFERENCES leads(id) ON DELETE CASCADE,
    agent_name  TEXT NOT NULL,
    action      TEXT NOT NULL,
    status      TEXT NOT NULL,   -- 'success' | 'failure' | 'info'
    details     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_logs_lead_id    ON agent_logs (lead_id);
CREATE INDEX IF NOT EXISTS idx_agent_logs_created_at ON agent_logs (created_at DESC);

-- ────────────────────────────────────────────────────────────
-- Auto-update updated_at trigger
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

DROP TRIGGER IF EXISTS update_leads_updated_at ON leads;
CREATE TRIGGER update_leads_updated_at
    BEFORE UPDATE ON leads
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
