"""Stage: reply classification (REPLY_RECEIVED → BOOKING_DRAFTED | CLOSED_LOST | HUMAN_REVIEW).

Order of checks is a compliance requirement, not a style choice:
1. Deterministic opt-out regex → suppression list + CLOSED_LOST. Never LLM-gated.
2. "Are you an AI?" question → HUMAN_REVIEW (spec §18.2).
3. Sensitive keywords → HUMAN_REVIEW.
4. Only then the LLM, with confidence thresholds from the spec.
"""
from typing import Dict

from app import events, llm, prompts, repository
from app.config import settings
from app.logging_setup import get_logger
from app.stages.common import (AI_QUESTION_REGEX, OPT_OUT_REGEX,
                               load_lead_for_stage, sensitive_keywords_in, tx)
from app.transitions import TransitionConflict

log = get_logger(stage="classify")
GROUP = "classify-workers"

VALID_INTENTS = {"INTERESTED", "NOT_INTERESTED", "NEEDS_FOLLOW_UP"}


def handle(message: Dict) -> None:
    lead_id = message["lead_id"]
    trace_id = message.get("trace_id")
    lead = load_lead_for_stage(lead_id, "REPLY_RECEIVED")
    reply = (lead.get("reply_text") or "").strip()

    # 1. deterministic opt-out
    if OPT_OUT_REGEX.search(reply):
        with tx() as conn:
            if not repository.try_mark_processed(conn, message["event_id"], GROUP):
                return
            repository.suppress_email(conn, lead.get("contact_email") or "",
                                      reason="opt-out reply", lead_id=lead_id)
            try:
                repository.cas_transition(
                    conn, lead_id, "REPLY_RECEIVED", "CLOSED_LOST",
                    by="classify", trace_id=trace_id,
                    intent="NOT_INTERESTED", intent_confidence=1.0,
                    intent_reasoning="Deterministic opt-out detected; address suppressed permanently.",
                )
            except TransitionConflict:
                return
            repository.log_agent_action(
                conn, lead_id, "classify", "opt_out", "success",
                status_before="REPLY_RECEIVED", status_after="CLOSED_LOST",
                details={"rule": "opt_out_regex"},
            )
        return

    # 2 & 3. human-only territory
    review_reason = None
    if AI_QUESTION_REGEX.search(reply):
        review_reason = "prospect asked whether the outreach is AI-generated"
    else:
        keywords = sensitive_keywords_in(reply)
        if keywords:
            review_reason = f"reply contains sensitive keywords: {', '.join(keywords)}"
    if review_reason:
        with tx() as conn:
            if not repository.try_mark_processed(conn, message["event_id"], GROUP):
                return
            try:
                repository.cas_transition(
                    conn, lead_id, "REPLY_RECEIVED", "HUMAN_REVIEW",
                    by="classify", trace_id=trace_id,
                    review_reason=review_reason, human_approval_required=True,
                    intent_reasoning=review_reason,
                )
            except TransitionConflict:
                return
            repository.log_agent_action(
                conn, lead_id, "classify", "keyword_escalation", "escalated",
                status_before="REPLY_RECEIVED", status_after="HUMAN_REVIEW",
                details={"reason": review_reason},
            )
        return

    # 4. LLM classification
    user_msg = (
        f"Classify this email reply:\n\n---\n{reply}\n---\n\n"
        f"Return a JSON object matching this schema:\n{prompts.CLASSIFIER_SCHEMA}"
    )
    result = llm.complete_json("classifier", prompts.CLASSIFIER_SYSTEM, user_msg, max_tokens=512)
    data = result.parsed
    intent = data.get("intent") if data.get("intent") in VALID_INTENTS else "NEEDS_FOLLOW_UP"
    try:
        confidence = round(max(0.0, min(float(data.get("confidence_score") or 0.5), 1.0)), 2)
    except (TypeError, ValueError):
        confidence = 0.5
    reasoning = (data.get("reasoning") or "")[:500]

    booking_result = None
    if intent == "INTERESTED" and confidence >= settings.REPLY_AUTO_THRESHOLD:
        target = "BOOKING_DRAFTED"
        from app.db import get_pool
        with get_pool().connection() as pconn:
            profile = repository.get_seller_profile(pconn)
        seller = repository.seller_context_block(profile) if profile else {}
        seller_note = prompts.seller_block(seller)
        link_note = (f"Include this booking link: {seller['meeting_link']}. "
                     if seller.get("meeting_link") else "")
        sign_note = f"Sign as {seller.get('sender_name') or 'the sales representative'}."
        booking_result = llm.complete_text(
            "booking",
            prompts.BOOKING_SYSTEM,
            f"{seller_note}\n{link_note}{sign_note}\n\n"
            f"The prospect {lead.get('contact_name') or 'there'} at {lead.get('company_name')} "
            f"replied with interest: \"{reply[:500]}\". Company context: "
            f"{lead.get('company_summary') or ''}",
        )
    elif intent == "NOT_INTERESTED" and confidence >= settings.CLOSED_LOST_MIN_CONFIDENCE:
        target = "CLOSED_LOST"
    else:
        target = "HUMAN_REVIEW"

    with tx() as conn:
        if not repository.try_mark_processed(conn, message["event_id"], GROUP):
            return
        fields = {
            "intent": intent, "intent_confidence": confidence, "intent_reasoning": reasoning,
        }
        if target == "BOOKING_DRAFTED":
            fields["booking_email_draft"] = booking_result.parsed
        if target == "HUMAN_REVIEW":
            fields["review_reason"] = (
                f"classified {intent} at {confidence:.2f} — below the auto-action threshold"
            )
            fields["human_approval_required"] = True
        try:
            repository.cas_transition(
                conn, lead_id, "REPLY_RECEIVED", target, by="classify",
                trace_id=trace_id, **fields,
            )
        except TransitionConflict as e:
            log.warning("classify_superseded", lead_id=lead_id, detail=str(e))
            return
        repository.log_agent_action(
            conn, lead_id, "classify", "classify_intent", "success",
            status_before="REPLY_RECEIVED", status_after=target,
            prompt_version=prompts.CLASSIFIER_PROMPT_VERSION, model=result.model,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            latency_ms=result.latency_ms, confidence=confidence,
            details={"intent": intent},
        )
        if booking_result:
            repository.log_agent_action(
                conn, lead_id, "classify", "draft_booking_email", "success",
                prompt_version=prompts.BOOKING_PROMPT_VERSION, model=booking_result.model,
                input_tokens=booking_result.input_tokens,
                output_tokens=booking_result.output_tokens,
                latency_ms=booking_result.latency_ms,
            )
