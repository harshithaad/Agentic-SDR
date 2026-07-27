"""Stage: send (DRAFT_READY → SENT | INVALID_EMAIL | CLOSED_LOST) and follow-ups
(SENT → FOLLOW_UP_SENT).

Sending email is a side effect on an external system with no idempotency keys,
so exactly-once is impossible in principle. The design is at-least-once with a
claim lease: claim (bounded by SEND_CLAIM_LEASE_MINUTES) → send → finalize.
Compliance gates run BEFORE the claim: suppression list, 7-day resend window,
and syntactic address validation."""
from datetime import datetime, timedelta, timezone
from typing import Dict

from app import events, repository
from app.config import settings
from app.integrations import gmail
from app.logging_setup import get_logger
from app.stages.common import EMAIL_REGEX, SkipMessage, load_lead_for_stage, tx
from app.transitions import TransitionConflict

log = get_logger(stage="send")
GROUP = "send-workers"


def handle(message: Dict) -> None:
    kind = (message.get("data") or {}).get("kind", "initial")
    if kind == "follow_up":
        _handle_follow_up(message)
    else:
        _handle_initial(message)


def _next_action(hours: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def _handle_initial(message: Dict) -> None:
    lead_id = message["lead_id"]
    trace_id = message.get("trace_id")
    lead = load_lead_for_stage(lead_id, "DRAFT_READY")

    if lead.get("human_approval_required"):
        raise SkipMessage(f"lead {lead_id} requires human approval; not auto-sending")

    email = (lead.get("contact_email") or "").strip()

    # compliance gates — deterministic, pre-claim
    gate_target, gate_reason = None, None
    if not EMAIL_REGEX.match(email):
        gate_target, gate_reason = "INVALID_EMAIL", f"malformed address: {email!r}"
    else:
        with tx() as conn:
            if repository.is_suppressed(conn, email):
                gate_target, gate_reason = "CLOSED_LOST", "address is on the suppression list"
            elif repository.recently_contacted(conn, email, settings.RESEND_WINDOW_DAYS):
                gate_target, gate_reason = (
                    "CLOSED_LOST",
                    f"address contacted within the last {settings.RESEND_WINDOW_DAYS} days",
                )
    if gate_target:
        with tx() as conn:
            if not repository.try_mark_processed(conn, message["event_id"], GROUP):
                return
            try:
                repository.cas_transition(
                    conn, lead_id, "DRAFT_READY", gate_target,
                    by="send", trace_id=trace_id, error_message=gate_reason,
                )
            except TransitionConflict:
                return
            repository.log_agent_action(
                conn, lead_id, "send", "compliance_gate", "blocked",
                status_before="DRAFT_READY", status_after=gate_target,
                details={"reason": gate_reason},
            )
        return

    # claim lease — the only writer of this lead's send for the next N minutes
    with tx() as conn:
        claimed = repository.claim_for_send(conn, lead_id, settings.SEND_CLAIM_LEASE_MINUTES)
    if not claimed:
        raise SkipMessage(f"lead {lead_id}: send already done or claimed by a peer")

    sent = gmail.send_email(
        to=email, subject=lead.get("subject_line") or "", body=lead.get("email_body") or ""
    )

    with tx() as conn:
        repository.try_mark_processed(conn, message["event_id"], GROUP)
        try:
            repository.cas_transition(
                conn, lead_id, "DRAFT_READY", "SENT", by="send", trace_id=trace_id,
                gmail_message_id=sent["message_id"], gmail_thread_id=sent["thread_id"],
                rfc_message_id=sent["rfc_message_id"],
                sent_at=datetime.now(timezone.utc),
                next_action_at=_next_action(settings.FOLLOW_UP_HOURS),
                claimed_at=None,
            )
        except TransitionConflict as e:
            # The email left the building but the lead moved concurrently — this
            # must be visible, not swallowed.
            log.error("sent_but_superseded", lead_id=lead_id, detail=str(e),
                      gmail_message_id=sent["message_id"])
        repository.log_agent_action(
            conn, lead_id, "send", "send_email", "success",
            status_before="DRAFT_READY", status_after="SENT",
            details={"to": email, "gmail_message_id": sent["message_id"],
                     "rfc_message_id": sent["rfc_message_id"]},
        )


def _handle_follow_up(message: Dict) -> None:
    lead_id = message["lead_id"]
    trace_id = message.get("trace_id")
    lead = load_lead_for_stage(lead_id, "SENT")
    email = (lead.get("contact_email") or "").strip()

    with tx() as conn:
        if repository.is_suppressed(conn, email):
            raise SkipMessage(f"lead {lead_id}: suppressed since initial send")
        claimed = repository.claim_for_follow_up(conn, lead_id, settings.SEND_CLAIM_LEASE_MINUTES)
    if not claimed:
        raise SkipMessage(f"lead {lead_id}: follow-up already sent or claimed")

    sent = gmail.send_email(
        to=email,
        subject=f"Re: {lead.get('subject_line') or ''}",
        body=(
            f"Hi {lead.get('contact_name') or 'there'},\n\n"
            f"I wanted to follow up on my previous note about "
            f"{lead.get('company_name')}. Would a quick 15-minute call this week "
            f"be worthwhile?\n\nBest regards\n\nTo opt out, reply STOP."
        ),
        in_reply_to_rfc_id=lead.get("rfc_message_id") or None,
        thread_id=lead.get("gmail_thread_id") or None,
    )

    with tx() as conn:
        repository.try_mark_processed(conn, message["event_id"], GROUP)
        try:
            repository.cas_transition(
                conn, lead_id, "SENT", "FOLLOW_UP_SENT", by="send", trace_id=trace_id,
                follow_up_sent_at=datetime.now(timezone.utc),
                next_action_at=_next_action(settings.FOLLOW_UP_HOURS),
                claimed_at=None,
            )
        except TransitionConflict as e:
            log.error("follow_up_sent_but_superseded", lead_id=lead_id, detail=str(e))
        repository.log_agent_action(
            conn, lead_id, "send", "send_follow_up", "success",
            status_before="SENT", status_after="FOLLOW_UP_SENT",
            details={"gmail_message_id": sent["message_id"]},
        )
