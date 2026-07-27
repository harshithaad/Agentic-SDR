"""The state machine, enforced — not documented.

Every status write in the system goes through cas_transition(), which compiles to:

    UPDATE leads SET status = <to>, version = version + 1, ...
    WHERE id = <id> AND status = <from>

The WHERE clause makes enforcement atomic at the database: a stale worker, a
duplicate delivery, or a concurrent human action simply matches zero rows and
raises TransitionConflict instead of corrupting state. This module is
dependency-free so the matrix is unit-testable without infrastructure.
"""
from typing import Dict, Iterable, Set

VALID_TRANSITIONS: Dict[str, Set[str]] = {
    "UPLOADED":          {"RESEARCH_PENDING"},
    "RESEARCH_PENDING":  {"RESEARCH_COMPLETE", "RESEARCH_FAILED", "HUMAN_REVIEW"},
    "RESEARCH_COMPLETE": {"CONTACT_FOUND", "NO_CONTACT_FOUND", "RESEARCH_PENDING"},
    "CONTACT_FOUND":     {"DRAFT_READY", "HUMAN_REVIEW", "RESEARCH_PENDING"},
    "DRAFT_READY":       {"SENT", "INVALID_EMAIL", "HUMAN_REVIEW", "CLOSED_LOST"},
    "SENT":              {"FOLLOW_UP_SENT", "REPLY_RECEIVED", "INVALID_EMAIL"},
    "FOLLOW_UP_SENT":    {"REPLY_RECEIVED", "CLOSED_LOST"},
    "REPLY_RECEIVED":    {"INTERESTED", "BOOKING_DRAFTED", "CLOSED_LOST", "HUMAN_REVIEW"},
    "INTERESTED":        {"BOOKING_DRAFTED"},
    # a human may re-drive a parked lead to any operationally sensible state
    "HUMAN_REVIEW":      {"DRAFT_READY", "SENT", "CLOSED_LOST", "RESEARCH_PENDING", "BOOKING_DRAFTED"},
    # note: *_COMPLETE/CONTACT_FOUND → RESEARCH_PENDING are the reaper's re-drive
    # edges — a lead wedged mid-hot-path restarts the stream from its seed.
}

# ── Zone boundary (hot path → Postgres) ───────────────────────────────────────
# Zone 1 (research → contact → draft) is event-carried: state rides in Kafka
# messages under EOS transactions, and the DB row stays at a hot-path status
# (possibly shadow-advanced by the projector for the dashboard). When a lead
# LEAVES the stream — parks, fails, or completes a draft — it materializes in
# one write. That write is a composite of legal chain transitions, so its CAS
# guard accepts any hot-path status rather than one exact from-status.
HOT_PATH_STATUSES: Set[str] = {"RESEARCH_PENDING", "RESEARCH_COMPLETE", "CONTACT_FOUND"}
MATERIALIZE_TARGETS: Set[str] = {
    "RESEARCH_FAILED", "NO_CONTACT_FOUND", "HUMAN_REVIEW", "DRAFT_READY",
}

TERMINAL_STATUSES: Set[str] = {
    "RESEARCH_FAILED", "NO_CONTACT_FOUND", "CLOSED_LOST", "INVALID_EMAIL", "BOOKING_DRAFTED",
}

ALL_STATUSES: Set[str] = set(VALID_TRANSITIONS) | TERMINAL_STATUSES | {
    s for targets in VALID_TRANSITIONS.values() for s in targets
}

# columns a transition is allowed to set alongside status — anything else is a bug
UPDATABLE_COLUMNS: Set[str] = {
    "company_summary", "industry", "employee_size_estimate", "pain_points", "recent_news",
    "research_confidence", "contact_name", "contact_email", "contact_role", "contact_source",
    "subject_line", "email_body", "personalisation_fact_used", "word_count",
    "human_approval_required", "review_reason", "gmail_message_id", "gmail_thread_id",
    "rfc_message_id", "sent_at", "follow_up_sent_at", "reply_text", "reply_received_at",
    "intent", "intent_confidence", "intent_reasoning", "booking_email_draft",
    "error_message", "next_action_at", "claimed_at", "retry_count",
}


class TransitionError(Exception):
    """The requested from→to pair is not in the matrix (a programming error)."""


class TransitionConflict(Exception):
    """The row was not in the expected from-status (a concurrency event, not a bug)."""


def assert_valid(from_status: str, to_status: str) -> None:
    allowed = VALID_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise TransitionError(f"illegal transition {from_status} -> {to_status}")


def is_valid(from_status: str, to_status: str) -> bool:
    return to_status in VALID_TRANSITIONS.get(from_status, set())


def build_transition_sql(from_status: str, to_status: str, fields: Iterable[str]):
    """Return (sql, ordered_field_names). Field values are always bound params."""
    assert_valid(from_status, to_status)
    names = []
    set_clauses = ["status = %(to_status)s", "version = version + 1"]
    for f in fields:
        if f not in UPDATABLE_COLUMNS:
            raise TransitionError(f"column not updatable via transition: {f}")
        names.append(f)
        set_clauses.append(f"{f} = %({f})s")
    sql = (
        f"UPDATE leads SET {', '.join(set_clauses)} "
        f"WHERE id = %(lead_id)s AND status = %(from_status)s "
        f"RETURNING id, status, version"
    )
    return sql, names
