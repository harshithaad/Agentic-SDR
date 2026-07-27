"""Optional sample data (SEED_DEMO_DATA=true) so the dashboard tells a story
before real keys are configured. Statuses are chosen to be inert: nothing here
is picked up by the reaper or the timers (next_action_at stays NULL)."""
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb


def _ago(hours: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


ROWS = [
    dict(
        company_name="Acme Robotics", website="https://acmerobotics.example",
        status="BOOKING_DRAFTED",
        company_summary="Acme Robotics builds collaborative robot arms for mid-size manufacturers.",
        industry="Industrial Automation", employee_size_estimate="200-500",
        pain_points=["Long sales cycles", "Manual lead qualification"],
        recent_news=["Acme opens new assembly plant in Austin"], research_confidence=0.88,
        contact_name="Dana Whitfield", contact_email="dana@acmerobotics.example",
        contact_role="VP of Sales", contact_source="hunter",
        subject_line="Congrats on the Austin plant",
        email_body="Hi Dana — saw the Austin plant news. Worth a quick call?\n\nTo opt out, reply STOP.",
        personalisation_fact_used="New assembly plant in Austin", word_count=16,
        gmail_message_id="seed-001", sent_at=_ago(72),
        reply_text="Yes, this is timely. Tuesday afternoon?",
        intent="INTERESTED", intent_confidence=0.94,
        intent_reasoning="Confirms pain point and proposes a time.",
        booking_email_draft="Hi Dana, Tuesday 2:00-2:30 PM CT works — invite coming.\n\nTo opt out, reply STOP.",
    ),
    dict(
        company_name="Vertex Manufacturing", website="https://vertexmfg.example",
        status="HUMAN_REVIEW",
        company_summary="Precision-machined aerospace components.",
        industry="Aerospace", employee_size_estimate="500-1000",
        pain_points=["Compliance-heavy procurement"], recent_news=[], research_confidence=0.71,
        contact_name="Marcus Cole", contact_email="m.cole@vertexmfg.example",
        contact_role="Director of Operations", contact_source="hunter",
        subject_line="Vendor onboarding friction",
        email_body="Hi Marcus — quick question about onboarding.\n\nTo opt out, reply STOP.",
        personalisation_fact_used="Compliance-heavy procurement", word_count=12,
        gmail_message_id="seed-002", sent_at=_ago(48),
        reply_text="I need to understand your pricing model and whether you have SOC 2.",
        intent="NEEDS_FOLLOW_UP", intent_confidence=0.55,
        intent_reasoning="Sensitive keywords (pricing, security) require human review.",
        review_reason="reply contains sensitive keywords: pricing, security",
        human_approval_required=True,
    ),
    dict(
        company_name="Blue Harbor Logistics", website="https://blueharbor.example",
        status="SENT",
        company_summary="Regional cold-chain trucking.",
        industry="Logistics", employee_size_estimate="200-500",
        pain_points=["Dispatchers double as sales reps"], recent_news=["Fleet expansion"],
        research_confidence=0.79,
        contact_name="Elena Vasquez", contact_email="elena@blueharbor.example",
        contact_role="VP Business Development", contact_source="apollo",
        subject_line="40 new reefers — filling them profitably",
        email_body="Hi Elena — congrats on the fleet expansion.\n\nTo opt out, reply STOP.",
        personalisation_fact_used="Fleet expansion", word_count=11,
        gmail_message_id="seed-003", sent_at=_ago(26),
    ),
    dict(
        company_name="Copperleaf Health", website="https://copperleafhealth.example",
        status="FOLLOW_UP_SENT",
        company_summary="Outpatient physical therapy clinics.",
        industry="Healthcare", employee_size_estimate="1000+",
        pain_points=["Referral partnerships in spreadsheets"], recent_news=["Acquired three clinics"],
        research_confidence=0.75,
        contact_name="Tom Okafor", contact_email="t.okafor@copperleaf.example",
        contact_role="Chief Growth Officer", contact_source="hunter",
        subject_line="Scaling referral outreach",
        email_body="Hi Tom — following up on referral outreach.\n\nTo opt out, reply STOP.",
        personalisation_fact_used="Three clinic acquisitions", word_count=10,
        gmail_message_id="seed-004", sent_at=_ago(120), follow_up_sent_at=_ago(20),
    ),
    dict(
        company_name="Starlight Ventures", website="https://starlightvc.example",
        status="CLOSED_LOST",
        company_summary="Early-stage climate fund.", industry="Venture Capital",
        employee_size_estimate="10-50", pain_points=["Deal-flow triage"], recent_news=[],
        research_confidence=0.66,
        contact_name="Rachel Kim", contact_email="rachel@starlightvc.example",
        contact_role="Operating Partner", contact_source="hunter",
        subject_line="Deal-flow triage", word_count=9,
        email_body="Hi Rachel — triage without another analyst hire.\n\nTo opt out, reply STOP.",
        personalisation_fact_used="Deal-flow growth",
        gmail_message_id="seed-005", sent_at=_ago(150),
        reply_text="Not a fit — please remove me from your list.",
        intent="NOT_INTERESTED", intent_confidence=1.0,
        intent_reasoning="Deterministic opt-out detected; address suppressed permanently.",
    ),
    dict(
        company_name="Juniper Foods", website=None, status="NO_CONTACT_FOUND",
        company_summary="Small-batch condiment producer.", industry="Food & Beverage CPG",
        employee_size_estimate="10-50", pain_points=["Two-state distribution"],
        recent_news=[], research_confidence=0.72,
    ),
]

JSONB_KEYS = {"pain_points", "recent_news"}


def seed_if_empty(conn) -> int:
    existing = conn.execute("SELECT count(*) AS n FROM leads").fetchone()
    if existing["n"] > 0:
        return 0
    for row in ROWS:
        cols, vals = zip(*[
            (k, Jsonb(v) if k in JSONB_KEYS and v is not None else v)
            for k, v in row.items()
        ])
        placeholders = ", ".join(["%s"] * len(cols))
        conn.execute(
            f"INSERT INTO leads ({', '.join(cols)}) VALUES ({placeholders})", vals
        )
    conn.execute(
        "INSERT INTO suppression_list (email, reason) VALUES "
        "('rachel@starlightvc.example', 'opt-out reply') ON CONFLICT DO NOTHING"
    )
    return len(ROWS)
