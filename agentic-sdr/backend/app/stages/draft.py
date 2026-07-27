"""Boundary stage: draft. Consumes the event-carried payload (seed + research +
contact) and is the point where the lead LEAVES the stream: its entire
accumulated state materializes into Postgres in one transaction — either as
DRAFT_READY (+ a send command via the outbox) or HUMAN_REVIEW. From here on the
lead lives in zone 2 and the row is the truth.

Validation feedback is fed back into the retry — the model is told exactly what
was rejected instead of being blindly re-rolled."""
from typing import Dict, Optional, Tuple

from app import events, llm, prompts, repository
from app.logging_setup import get_logger
from app.stages.common import tx
from app.transitions import TransitionConflict

log = get_logger(stage="draft")
GROUP = "draft-workers"

OPT_OUT_LINE = "To opt out, reply STOP."

CARRIED_FIELDS = events.RESEARCH_FIELDS + events.CONTACT_FIELDS


def _validate(draft: Dict) -> Tuple[bool, str]:
    body = draft.get("email_body") or ""
    if not (draft.get("subject_line") or "").strip():
        return False, "missing subject line"
    words = len(body.split())
    if words == 0:
        return False, "empty body"
    if words > 200:
        return False, f"body is {words} words (max 200)"
    if OPT_OUT_LINE not in body:
        return False, f"missing required opt-out line: '{OPT_OUT_LINE}'"
    if not (draft.get("personalisation_fact_used") or "").strip():
        return False, "no personalisation fact provided"
    if "pricing" in body.lower() or "$" in body:
        return False, "mentions pricing"
    return True, "ok"


def handle(message: Dict) -> None:
    lead_id = message["lead_id"]
    trace_id = message.get("trace_id")
    carried = message.get("data") or {}

    seller = carried.get("seller") or {}
    research_context = (
        f"Prospect Company: {carried.get('company_name')}\n"
        f"Industry: {carried.get('industry')}\n"
        f"Company Summary: {carried.get('company_summary')}\n"
        f"Pain Points: {', '.join(carried.get('pain_points') or [])}\n"
        f"Recent News: {', '.join(carried.get('recent_news') or [])}\n"
        f"Contact Name: {carried.get('contact_name')}\n"
        f"Contact Role: {carried.get('contact_role')}\n"
    )
    sender_line = seller.get("sender_name") or "the sales representative"
    if seller.get("sender_title"):
        sender_line += f", {seller['sender_title']}"
    cta_hint = (
        f"The call to action should propose a short call; include this booking link: "
        f"{seller['meeting_link']}\n" if seller.get("meeting_link") else
        "The call to action should propose a short call this week.\n"
    )
    base_msg = (
        f"{prompts.seller_block(seller)}\n"
        f"Sign the email as: {sender_line}\n{cta_hint}\n"
        f"Write a personalized cold email to the prospect using this research data:\n\n"
        f"{research_context}\n"
        f"Return a JSON object matching this schema exactly:\n{prompts.EMAIL_WRITER_SCHEMA}"
    )

    draft: Optional[Dict] = None
    reason = "no attempt"
    usage = []
    for attempt in range(2):
        user_msg = base_msg if attempt == 0 else (
            f"{base_msg}\n\nYour previous draft was rejected: {reason}. "
            f"Fix exactly that problem and return corrected JSON."
        )
        try:
            result = llm.complete_json("email_writer", prompts.EMAIL_WRITER_SYSTEM, user_msg)
        except Exception as e:
            reason = f"llm error: {e}"
            break
        usage.append(result)
        ok, reason = _validate(result.parsed)
        if ok:
            draft = result.parsed
            break

    src = draft or (usage[-1].parsed if usage else {})
    body = src.get("email_body") or ""

    # everything known about the lead lands in the row in one write
    fields = {k: carried.get(k) for k in CARRIED_FIELDS if k in carried}
    fields.update({
        "subject_line": src.get("subject_line"),
        "email_body": body or None,
        "personalisation_fact_used": src.get("personalisation_fact_used"),
        "word_count": len(body.split()) if body else None,
    })

    with tx() as conn:
        if not repository.try_mark_processed(conn, message["event_id"], GROUP):
            return
        try:
            if draft:
                repository.materialize_from_hot_path(
                    conn, lead_id, "DRAFT_READY", by="draft", trace_id=trace_id,
                    human_approval_required=False, **fields,
                )
                cmd = events.make_message(
                    events.CMD_SEND_EMAIL, lead_id, {"kind": "initial"}, trace_id=trace_id
                )
                repository.outbox_add(conn, events.CMD_TOPICS["send"], lead_id, cmd)
            else:
                repository.materialize_from_hot_path(
                    conn, lead_id, "HUMAN_REVIEW", by="draft", trace_id=trace_id,
                    human_approval_required=True,
                    review_reason=f"draft failed validation twice: {reason}", **fields,
                )
        except TransitionConflict as e:
            log.warning("draft_superseded", lead_id=lead_id, detail=str(e))
            return
        for i, u in enumerate(usage):
            repository.log_agent_action(
                conn, lead_id, "draft", "write_email",
                "success" if (draft and i == len(usage) - 1) else "rejected",
                status_after="DRAFT_READY" if draft else "HUMAN_REVIEW",
                prompt_version=prompts.EMAIL_WRITER_PROMPT_VERSION, model=u.model,
                input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                latency_ms=u.latency_ms,
                details={"attempt": i + 1,
                         "rejection": None if draft and i == len(usage) - 1 else reason},
            )
        if not usage:
            repository.log_agent_action(
                conn, lead_id, "draft", "write_email", "failure",
                status_after="HUMAN_REVIEW", details={"error": reason},
            )
