-- Agentic SDR — production schema
-- Postgres is the source of truth. Kafka transports commands/events via the outbox.

CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS leads (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id                  uuid,
    company_name              text NOT NULL,
    website                   text,
    status                    text NOT NULL DEFAULT 'UPLOADED' CHECK (status IN (
                                  'UPLOADED','RESEARCH_PENDING','RESEARCH_COMPLETE','RESEARCH_FAILED',
                                  'CONTACT_FOUND','NO_CONTACT_FOUND','DRAFT_READY','SENT',
                                  'FOLLOW_UP_SENT','REPLY_RECEIVED','INTERESTED','BOOKING_DRAFTED',
                                  'CLOSED_LOST','HUMAN_REVIEW','INVALID_EMAIL')),
    -- optimistic concurrency: every write through the transition guard bumps this
    version                   integer NOT NULL DEFAULT 0,
    retry_count               integer NOT NULL DEFAULT 0,
    -- single timer column drives ALL scheduled work (follow-ups, expiry)
    next_action_at            timestamptz,
    -- send claim lease: prevents double-send across concurrent consumers
    claimed_at                timestamptz,

    company_summary           text,
    industry                  text,
    employee_size_estimate    text,
    pain_points               jsonb,
    recent_news               jsonb,
    research_confidence       numeric(3,2),

    contact_name              text,
    contact_email             text,
    contact_role              text,
    contact_source            text,

    subject_line              text,
    email_body                text,
    personalisation_fact_used text,
    word_count                integer,
    human_approval_required   boolean NOT NULL DEFAULT false,
    review_reason             text,

    gmail_message_id          text,
    gmail_thread_id           text,
    rfc_message_id            text,   -- RFC 2822 Message-ID header; replies match on this
    sent_at                   timestamptz,
    follow_up_sent_at         timestamptz,

    reply_text                text,
    reply_received_at         timestamptz,
    intent                    text,
    intent_confidence         numeric(3,2),
    intent_reasoning          text,
    booking_email_draft       text,

    error_message             text,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now()
);

-- duplicate company in the same upload batch is rejected at ingest
CREATE UNIQUE INDEX IF NOT EXISTS uq_leads_batch_company
    ON leads (batch_id, lower(company_name)) WHERE batch_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_leads_status         ON leads (status);
CREATE INDEX IF NOT EXISTS idx_leads_next_action_at ON leads (next_action_at) WHERE next_action_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_leads_updated_at     ON leads (updated_at);
CREATE INDEX IF NOT EXISTS idx_leads_contact_email  ON leads (lower(contact_email)) WHERE contact_email IS NOT NULL;

-- full observability per agent action (spec §19.1)
CREATE TABLE IF NOT EXISTS agent_logs (
    id              bigserial PRIMARY KEY,
    lead_id         uuid REFERENCES leads(id) ON DELETE CASCADE,
    agent           text NOT NULL,
    action          text NOT NULL,
    status          text NOT NULL,
    status_before   text,
    status_after    text,
    prompt_version  text,
    model           text,
    input_tokens    integer,
    output_tokens   integer,
    latency_ms      integer,
    confidence      numeric(3,2),
    details         jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_logs_lead    ON agent_logs (lead_id);
CREATE INDEX IF NOT EXISTS idx_agent_logs_created ON agent_logs (created_at);

-- transactional outbox: DB write + intent-to-publish commit atomically,
-- the relay publishes to Kafka and marks published. Solves dual-write drift.
CREATE TABLE IF NOT EXISTS outbox (
    id            bigserial PRIMARY KEY,
    topic         text NOT NULL,
    key           text NOT NULL,
    payload       jsonb NOT NULL,
    headers       jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    published_at  timestamptz
);
CREATE INDEX IF NOT EXISTS idx_outbox_unpublished ON outbox (id) WHERE published_at IS NULL;

-- idempotent consumer: effects are recorded with the event id in one transaction;
-- redelivered events are detected here and skipped.
CREATE TABLE IF NOT EXISTS processed_events (
    event_id        uuid NOT NULL,
    consumer_group  text NOT NULL,
    processed_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, consumer_group)
);

-- permanent opt-out list (CAN-SPAM). Deterministic, never LLM-gated.
CREATE TABLE IF NOT EXISTS suppression_list (
    email       text PRIMARY KEY,          -- stored lowercased
    reason      text NOT NULL,
    lead_id     uuid,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_leads_updated_at ON leads;
CREATE TRIGGER trg_leads_updated_at BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
