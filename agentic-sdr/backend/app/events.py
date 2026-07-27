"""Topic map and event envelope.

Command topics are work queues (one consumer group each). The event topic is a
fact stream (state transitions) that anything may observe — the API's SSE bridge
consumes it today; CRM sync or analytics could consume it tomorrow without
touching the pipeline. Messages are keyed by lead_id, so all events for one lead
land on one partition and are processed in order, while distinct leads fan out
across partitions for parallelism.
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# stage name -> command topic
CMD_TOPICS: Dict[str, str] = {
    "research": "sdr.cmd.research",
    "contact":  "sdr.cmd.contact",
    "draft":    "sdr.cmd.draft",
    "send":     "sdr.cmd.send",
    "classify": "sdr.cmd.classify",
}

EVT_LEADS = "sdr.evt.leads"
DLQ_TOPIC = "sdr.dlq"

ALL_TOPICS = list(CMD_TOPICS.values()) + [EVT_LEADS, DLQ_TOPIC]

# command types carried on cmd topics
CMD_RESEARCH_LEAD = "ResearchLead"    # data: seed {company_name, website}
CMD_FIND_CONTACT = "FindContact"      # data: seed + research fields (event-carried)
CMD_DRAFT_EMAIL = "DraftEmail"        # data: seed + research + contact fields
CMD_SEND_EMAIL = "SendEmail"          # data.kind: "initial" | "follow_up" (thin — zone 2)
CMD_CLASSIFY_REPLY = "ClassifyReply"  # thin — zone 2

# Zone 1 (hot path) payload contract: research/contact/draft commands CARRY the
# accumulated lead data so hot-path workers never read Postgres. Zone 2 commands
# stay thin (lead_id only) because the row has been materialized by then.
RESEARCH_FIELDS = ("company_summary", "industry", "employee_size_estimate",
                   "pain_points", "recent_news", "research_confidence")
CONTACT_FIELDS = ("contact_name", "contact_email", "contact_role", "contact_source")
SEED_FIELDS = ("company_name", "website")

# event types carried on the fact stream
EVT_LEAD_TRANSITIONED = "LeadTransitioned"


def make_message(
    msg_type: str,
    lead_id: str,
    data: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "type": msg_type,
        "lead_id": str(lead_id),
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "trace_id": trace_id or str(uuid.uuid4()),
        "data": data or {},
    }


def serialize(message: Dict[str, Any]) -> bytes:
    return json.dumps(message, separators=(",", ":"), default=str).encode("utf-8")


def deserialize(raw: bytes) -> Dict[str, Any]:
    msg = json.loads(raw.decode("utf-8"))
    for required in ("event_id", "type", "lead_id"):
        if required not in msg:
            raise ValueError(f"malformed message: missing {required}")
    return msg


def transition_event(lead_id: str, from_status: str, to_status: str,
                     by: str, trace_id: Optional[str] = None,
                     extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = {"from": from_status, "to": to_status, "by": by}
    if extra:
        data.update(extra)
    return make_message(EVT_LEAD_TRANSITIONED, lead_id, data, trace_id)
