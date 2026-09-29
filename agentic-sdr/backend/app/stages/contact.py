"""Zone-1 stage: contact discovery. Event-carried — research data arrives inside
the command and rides forward with the contact result. Hunter verified-only,
independent Apollo fallback. No contact found = a boundary exit."""
from typing import Dict, Optional, Union

import httpx

from app import events
from app.integrations import contacts
from app.logging_setup import get_logger
from app.stages.common import Park, Produce

log = get_logger(stage="contact")
GROUP = "contact-workers"


def handle(message: Dict) -> Union[Produce, Park]:
    lead_id = message["lead_id"]
    trace_id = message.get("trace_id")
    carried = message.get("data") or {}
    company_name = carried.get("company_name") or ""
    domain = contacts._extract_domain(carried.get("website"))

    found: Optional[Dict] = None
    errors = []

    # A contact supplied at upload wins: discovery exists to find unknown
    # contacts, not to second-guess known ones. Also spares provider quota.
    if carried.get("contact_email"):
        found = {
            "contact_email": carried["contact_email"],
            "contact_name": carried.get("contact_name") or "",
            "contact_role": carried.get("contact_role") or "",
            "contact_source": "provided",
        }
        log.info("contact_provided", lead_id=lead_id)

    if not found and domain:
        try:
            found = contacts.hunter_verified_contact(domain)
        except httpx.HTTPError as e:
            errors.append(f"hunter: {e}")
            log.warning("hunter_failed", lead_id=lead_id, error=str(e))
    if not found:
        try:
            found = contacts.apollo_contact(company_name, domain)
        except httpx.HTTPError as e:
            errors.append(f"apollo: {e}")
            log.warning("apollo_failed", lead_id=lead_id, error=str(e))

    if not found:
        return Park(
            to_status="NO_CONTACT_FOUND",
            # materialize the carried research too — the row must hold everything
            # known about the lead when it leaves the stream
            fields={k: v for k, v in carried.items() if k in
                    events.RESEARCH_FIELDS} | (
                    {"error_message": "; ".join(errors)[:500]} if errors else {}),
            log_action="find_contact", log_status="no_results",
            log_details={"errors": errors or None},
        )

    payload = {**carried, **found}
    cmd = events.make_message(events.CMD_DRAFT_EMAIL, lead_id, payload, trace_id)
    evt = events.transition_event(lead_id, "RESEARCH_COMPLETE", "CONTACT_FOUND",
                                  by="contact", trace_id=trace_id,
                                  extra={"source": found.get("contact_source")})
    log.info("contact_found", lead_id=lead_id, source=found.get("contact_source"))
    return Produce(messages=[
        (events.CMD_TOPICS["draft"], lead_id, cmd),
        (events.EVT_LEADS, lead_id, evt),
    ])
