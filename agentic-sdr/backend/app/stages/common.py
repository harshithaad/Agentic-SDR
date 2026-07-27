"""Shared stage plumbing.

The handler contract: do external work on a plain connectionless code path, then
finalize everything — idempotency mark, guarded transition, agent log, outbox
commands — in ONE database transaction. If the process dies mid-handler, nothing
was marked processed, the offset was never committed, and redelivery redoes the
work safely.
"""
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app import repository
from app.db import get_pool
from app.logging_setup import get_logger
from app.transitions import TransitionConflict

log = get_logger(component="stages")

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Deterministic compliance checks — these must never depend on an LLM.
OPT_OUT_REGEX = re.compile(r"\b(stop|unsubscribe|opt[\s\-]?out|remove me|take me off)\b", re.IGNORECASE)
AI_QUESTION_REGEX = re.compile(r"\bare you (an? )?(ai|bot|robot|automated|machine)\b", re.IGNORECASE)
SENSITIVE_KEYWORDS = (
    "pricing", "contract", "security", "legal", "gdpr", "compliance", "enterprise",
    "agreement", "terms", "nda", "privacy",
)


class SkipMessage(Exception):
    """Message should be acked without effects (duplicate, stale, or superseded)."""


class InfraError(Exception):
    """Infrastructure is unhealthy (DB/broker); the runner seeks back and retries."""


@dataclass
class Produce:
    """Zone-1 happy path: stay in the stream. The runner produces these messages
    inside the Kafka transaction that also commits the consumer offset."""
    messages: List[Tuple[str, str, Dict]]  # (topic, key, payload dict)


@dataclass
class Park:
    """Zone-1 exit: leave the stream and materialize into Postgres. The runner
    aborts nothing (no txn open), runs the boundary transaction, then commits
    the offset plainly."""
    to_status: str
    fields: Dict
    log_action: str
    log_status: str
    log_details: Optional[Dict] = None
    llm_usage: Optional[List] = None       # LLMResult list to record


@contextmanager
def tx():
    with get_pool().connection() as conn:
        with conn.transaction():
            yield conn


def load_lead_for_stage(lead_id: str, expected_status: str) -> Dict:
    """Fetch the lead and verify it is still in the status this stage owns."""
    with get_pool().connection() as conn:
        lead = repository.get_lead(conn, lead_id)
    if lead is None:
        raise SkipMessage(f"lead {lead_id} does not exist")
    if lead["status"] != expected_status:
        raise SkipMessage(
            f"lead {lead_id} is {lead['status']}, stage expects {expected_status} — superseded"
        )
    return lead


def sensitive_keywords_in(text: str):
    lower = text.lower()
    return [kw for kw in SENSITIVE_KEYWORDS if kw in lower]


def park_for_review(conn, lead: Dict, by: str, reason: str,
                    trace_id: Optional[str] = None, **fields) -> None:
    """Best-effort move to HUMAN_REVIEW; concurrent supersession is not an error."""
    try:
        repository.cas_transition(
            conn, lead["id"], lead["status"], "HUMAN_REVIEW", by=by, trace_id=trace_id,
            review_reason=reason, human_approval_required=True, **fields,
        )
    except TransitionConflict:
        log.warning("park_conflict", lead_id=str(lead["id"]), reason=reason)
