"""Zone-1 stage: research. Event-carried — the command brings the seed
(company_name, website); the result rides FORWARD inside the contact command.
No Postgres access on the happy path. Exits (low confidence, hard failure)
return Park and materialize at the boundary."""
from typing import Dict, Union

import httpx

from app import events, llm, prompts
from app.config import settings
from app.integrations import firecrawl, serper
from app.logging_setup import get_logger
from app.stages.common import Park, Produce

log = get_logger(stage="research")
GROUP = "research-workers"


def handle(message: Dict) -> Union[Produce, Park]:
    lead_id = message["lead_id"]
    trace_id = message.get("trace_id")
    seed = message.get("data") or {}
    company_name = seed.get("company_name") or ""
    website = seed.get("website")

    parts = []
    if website:
        try:
            scraped = firecrawl.scrape(website)
            if scraped:
                parts.append(f"=== WEBSITE CONTENT ({website}) ===\n{scraped[:5000]}")
        except (httpx.HTTPError, firecrawl.FirecrawlNotConfigured) as e:
            parts.append(f"=== WEBSITE SCRAPE UNAVAILABLE: {e} ===")
    for label, query in (("RECENT NEWS", f"{company_name} news"),
                         ("COMPANY SEARCH", f"{company_name} company")):
        try:
            found = serper.search(query)
            if found:
                parts.append(f"=== {label} ===\n{found}")
        except (httpx.HTTPError, serper.SerperNotConfigured) as e:
            parts.append(f"=== {label} UNAVAILABLE: {e} ===")

    content = f"Company: {company_name}\n\n" + "\n\n".join(parts)
    seller_ctx = prompts.seller_block(seed.get("seller") or {})
    user_msg = (
        (f"{seller_ctx}\n\n" if seller_ctx else "")
        + f"Here is the research content collected about a prospect company:\n\n{content}\n\n"
        f"Return a JSON object matching this schema exactly:\n{prompts.RESEARCH_SCHEMA}"
    )

    try:
        result = llm.complete_json("research", prompts.RESEARCH_SYSTEM, user_msg, max_tokens=1024)
    except Exception as e:
        return Park(
            to_status="RESEARCH_FAILED",
            fields={"error_message": str(e)[:500]},
            log_action="structure_research", log_status="failure",
            log_details={"error": str(e)[:500]},
        )

    data = result.parsed
    pain_points = data.get("pain_points") if isinstance(data.get("pain_points"), list) else []
    recent_news = data.get("recent_news") if isinstance(data.get("recent_news"), list) else []
    try:
        confidence = float(data.get("confidence_score") or 0.5)
    except (TypeError, ValueError):
        confidence = 0.5
    if not pain_points:
        confidence = min(confidence, 0.60)  # spec hallucination guard
    confidence = round(max(0.0, min(confidence, 1.0)), 2)

    research_fields = {
        "company_summary": data.get("company_summary"),
        "industry": data.get("industry"),
        "employee_size_estimate": data.get("employee_size_estimate"),
        "pain_points": pain_points,
        "recent_news": recent_news,
        "research_confidence": confidence,
    }

    if confidence < settings.RESEARCH_MIN_CONFIDENCE:
        return Park(
            to_status="HUMAN_REVIEW",
            fields={**research_fields, "human_approval_required": True,
                    "review_reason": f"research confidence {confidence:.2f} below "
                                     f"{settings.RESEARCH_MIN_CONFIDENCE}"},
            log_action="structure_research", log_status="low_confidence",
            llm_usage=[result],
        )

    # happy path: result rides forward inside the next command + a fact event.
    payload = {**seed, **research_fields}
    cmd = events.make_message(events.CMD_FIND_CONTACT, lead_id, payload, trace_id)
    evt = events.transition_event(lead_id, "RESEARCH_PENDING", "RESEARCH_COMPLETE",
                                  by="research", trace_id=trace_id,
                                  extra={"confidence": confidence})
    log.info("research_complete", lead_id=lead_id, confidence=confidence,
             tokens=result.input_tokens + result.output_tokens)
    return Produce(messages=[
        (events.CMD_TOPICS["contact"], lead_id, cmd),
        (events.EVT_LEADS, lead_id, evt),
    ])
