-- The salesperson's context: who is selling, what, and to whom. Single-tenant —
-- one row, enforced. Uploads are refused until this is configured, because a
-- personalized email written by someone who doesn't know what they sell is
-- fiction, not sales.
CREATE TABLE IF NOT EXISTS seller_profile (
    id                    integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    company_name          text NOT NULL,
    product_description   text NOT NULL,
    value_proposition     text NOT NULL,
    target_customer       text,
    sender_name           text NOT NULL,
    sender_title          text,
    meeting_link          text,
    tone                  text,
    updated_at            timestamptz NOT NULL DEFAULT now()
);
