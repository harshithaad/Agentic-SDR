-- Booking emails are drafted autonomously but sent by a human (spec §18.2).
-- Track the send so the dashboard can distinguish "drafted" from "delivered".
ALTER TABLE leads ADD COLUMN IF NOT EXISTS booking_sent_at timestamptz;
