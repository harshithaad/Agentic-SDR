"""All database access. Functions take an open connection so the caller owns the
transaction boundary — a stage handler composes its dedupe insert, transition,
agent log, and outbox rows into ONE commit."""
from typing import Any, Dict, List, Optional

from psycopg.types.json import Jsonb

from app import events, transitions
from app.config import settings
from app.metrics import TRANSITIONS

JSONB_COLUMNS = {"pain_points", "recent_news"}


def _prep(fields: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in fields.items():
        out[k] = Jsonb(v) if k in JSONB_COLUMNS and v is not None else v
    return out


# ─── leads ─────────────────────────────────────────────────────────────────────

def create_lead(conn, company_name: str, website: Optional[str], batch_id: str) -> Optional[Dict]:
    """Insert as UPLOADED. Returns None on duplicate company within the batch."""
    row = conn.execute(
        """
        INSERT INTO leads (company_name, website, batch_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (batch_id, lower(company_name)) WHERE batch_id IS NOT NULL DO NOTHING
        RETURNING *
        """,
        (company_name, website, batch_id),
    ).fetchone()
    return row


def get_lead(conn, lead_id: str) -> Optional[Dict]:
    return conn.execute("SELECT * FROM leads WHERE id = %s", (lead_id,)).fetchone()


def all_leads(conn, status: Optional[str] = None) -> List[Dict]:
    if status:
        return conn.execute(
            "SELECT * FROM leads WHERE status = %s ORDER BY created_at DESC", (status,)
        ).fetchall()
    return conn.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()


def cas_transition(conn, lead_id: str, from_status: str, to_status: str,
                   by: str, trace_id: Optional[str] = None, emit_event: bool = True,
                   **fields) -> Dict:
    """Atomic guarded transition + fact event via outbox, in the caller's tx.
    Raises TransitionConflict if the row is not in from_status anymore."""
    sql, names = transitions.build_transition_sql(from_status, to_status, fields.keys())
    params = {"lead_id": lead_id, "from_status": from_status, "to_status": to_status,
              **_prep({n: fields[n] for n in names})}
    row = conn.execute(sql, params).fetchone()
    if row is None:
        current = conn.execute(
            "SELECT status FROM leads WHERE id = %s", (lead_id,)
        ).fetchone()
        raise transitions.TransitionConflict(
            f"lead {lead_id}: expected {from_status}, "
            f"found {current['status'] if current else 'MISSING'} (wanted -> {to_status})"
        )
    TRANSITIONS.labels(from_status=from_status, to_status=to_status).inc()
    if emit_event:
        evt = events.transition_event(lead_id, from_status, to_status, by, trace_id)
        outbox_add(conn, events.EVT_LEADS, lead_id, evt)
    return row


def materialize_from_hot_path(conn, lead_id: str, to_status: str, by: str,
                              trace_id: Optional[str] = None, **fields) -> Dict:
    """Zone-boundary write: a lead leaves the event-carried stream and lands in
    Postgres with all its accumulated fields in ONE update. The CAS guard accepts
    any hot-path status because the projector may have shadow-advanced the row.
    Emits the fact event via the outbox like every other transition."""
    if to_status not in transitions.MATERIALIZE_TARGETS:
        raise transitions.TransitionError(f"not a zone-boundary target: {to_status}")
    names = []
    set_clauses = ["status = %(to_status)s", "version = version + 1"]
    prepped = _prep(fields)
    for f in fields:
        if f not in transitions.UPDATABLE_COLUMNS:
            raise transitions.TransitionError(f"column not updatable: {f}")
        names.append(f)
        set_clauses.append(f"{f} = %({f})s")
    row = conn.execute(
        f"UPDATE leads SET {', '.join(set_clauses)} "
        f"WHERE id = %(lead_id)s AND status = ANY(%(hot)s) "
        f"RETURNING id, status, version",
        {"lead_id": lead_id, "to_status": to_status,
         "hot": list(transitions.HOT_PATH_STATUSES), **prepped},
    ).fetchone()
    if row is None:
        current = conn.execute("SELECT status FROM leads WHERE id = %s", (lead_id,)).fetchone()
        raise transitions.TransitionConflict(
            f"lead {lead_id}: not in hot path "
            f"(found {current['status'] if current else 'MISSING'}, wanted -> {to_status})"
        )
    TRANSITIONS.labels(from_status="HOT_PATH", to_status=to_status).inc()
    evt = events.transition_event(lead_id, "HOT_PATH", to_status, by, trace_id)
    outbox_add(conn, events.EVT_LEADS, lead_id, evt)
    return row


def shadow_status(conn, lead_id: str, from_status: str, to_status: str) -> bool:
    """Projector-only: advance the dashboard's view of a hot-path lead. Loses all
    races on purpose (plain CAS, conflicts ignored by the caller)."""
    row = conn.execute(
        "UPDATE leads SET status = %s, version = version + 1 "
        "WHERE id = %s AND status = %s RETURNING id",
        (to_status, lead_id, from_status),
    ).fetchone()
    return row is not None


# ─── seller profile ────────────────────────────────────────────────────────────

def get_seller_profile(conn) -> Optional[Dict]:
    return conn.execute("SELECT * FROM seller_profile WHERE id = 1").fetchone()


def upsert_seller_profile(conn, **fields) -> Dict:
    cols = ["company_name", "product_description", "value_proposition",
            "target_customer", "sender_name", "sender_title", "meeting_link", "tone"]
    values = {c: fields.get(c) for c in cols}
    return conn.execute(
        f"""
        INSERT INTO seller_profile (id, {', '.join(cols)}, updated_at)
        VALUES (1, {', '.join('%(' + c + ')s' for c in cols)}, now())
        ON CONFLICT (id) DO UPDATE SET
            {', '.join(c + ' = EXCLUDED.' + c for c in cols)}, updated_at = now()
        RETURNING *
        """,
        values,
    ).fetchone()


def seller_context_block(profile: Dict) -> Dict:
    """Compact seller context carried inside zone-1 command payloads."""
    return {
        "company": profile.get("company_name"),
        "product": profile.get("product_description"),
        "value_proposition": profile.get("value_proposition"),
        "target_customer": profile.get("target_customer"),
        "sender_name": profile.get("sender_name"),
        "sender_title": profile.get("sender_title"),
        "meeting_link": profile.get("meeting_link"),
        "tone": profile.get("tone"),
    }


# ─── outbox ────────────────────────────────────────────────────────────────────

def outbox_add(conn, topic: str, key: str, payload: Dict, headers: Optional[Dict] = None) -> None:
    conn.execute(
        "INSERT INTO outbox (topic, key, payload, headers) VALUES (%s, %s, %s, %s)",
        (topic, str(key), Jsonb(payload), Jsonb(headers) if headers else None),
    )


def outbox_claim_batch(conn, limit: int = 200) -> List[Dict]:
    """Claim unpublished rows with SKIP LOCKED so multiple relays never collide."""
    return conn.execute(
        """
        SELECT id, topic, key, payload, headers FROM outbox
        WHERE published_at IS NULL
        ORDER BY id
        LIMIT %s
        FOR UPDATE SKIP LOCKED
        """,
        (limit,),
    ).fetchall()


def outbox_mark_published(conn, ids: List[int]) -> None:
    if ids:
        conn.execute("UPDATE outbox SET published_at = now() WHERE id = ANY(%s)", (ids,))


# ─── idempotent consumer ───────────────────────────────────────────────────────

def try_mark_processed(conn, event_id: str, consumer_group: str) -> bool:
    """True if this event is new for this group; False if it's a redelivery."""
    row = conn.execute(
        """
        INSERT INTO processed_events (event_id, consumer_group) VALUES (%s, %s)
        ON CONFLICT DO NOTHING RETURNING event_id
        """,
        (event_id, consumer_group),
    ).fetchone()
    return row is not None


def was_processed(conn, event_id: str, consumer_group: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM processed_events WHERE event_id = %s AND consumer_group = %s",
        (event_id, consumer_group),
    ).fetchone()
    return row is not None


# ─── suppression / compliance ──────────────────────────────────────────────────

def suppress_email(conn, email: str, reason: str, lead_id: Optional[str] = None) -> None:
    conn.execute(
        """
        INSERT INTO suppression_list (email, reason, lead_id) VALUES (lower(%s), %s, %s)
        ON CONFLICT (email) DO NOTHING
        """,
        (email, reason, lead_id),
    )


def is_suppressed(conn, email: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM suppression_list WHERE email = lower(%s)", (email,)
    ).fetchone()
    return row is not None


def recently_contacted(conn, email: str, days: int) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM leads
        WHERE lower(contact_email) = lower(%s)
          AND sent_at IS NOT NULL AND sent_at > now() - make_interval(days => %s)
        LIMIT 1
        """,
        (email, days),
    ).fetchone()
    return row is not None


# ─── send claim lease ──────────────────────────────────────────────────────────

def claim_for_send(conn, lead_id: str, lease_minutes: int) -> bool:
    """Claim the exclusive right to send for this lead. The lease expires so a
    crash between claim and finalize self-heals; a live claim blocks everyone else."""
    row = conn.execute(
        """
        UPDATE leads SET claimed_at = now()
        WHERE id = %s
          AND gmail_message_id IS NULL
          AND (claimed_at IS NULL OR claimed_at < now() - make_interval(mins => %s))
        RETURNING id
        """,
        (lead_id, lease_minutes),
    ).fetchone()
    return row is not None


def claim_for_follow_up(conn, lead_id: str, lease_minutes: int) -> bool:
    row = conn.execute(
        """
        UPDATE leads SET claimed_at = now()
        WHERE id = %s
          AND follow_up_sent_at IS NULL
          AND (claimed_at IS NULL OR claimed_at < now() - make_interval(mins => %s))
        RETURNING id
        """,
        (lead_id, lease_minutes),
    ).fetchone()
    return row is not None


# ─── scheduler queries ─────────────────────────────────────────────────────────

def due_for_follow_up(conn, limit: int = 50) -> List[Dict]:
    """Atomically pop due SENT leads (clear the timer in the same statement)."""
    return conn.execute(
        """
        UPDATE leads SET next_action_at = NULL
        WHERE id IN (
            SELECT id FROM leads
            WHERE status = 'SENT' AND next_action_at IS NOT NULL AND next_action_at <= now()
            ORDER BY next_action_at LIMIT %s
            FOR UPDATE SKIP LOCKED
        )
        RETURNING *
        """,
        (limit,),
    ).fetchall()


def due_for_expiry(conn, limit: int = 50) -> List[Dict]:
    return conn.execute(
        """
        UPDATE leads SET next_action_at = NULL
        WHERE id IN (
            SELECT id FROM leads
            WHERE status = 'FOLLOW_UP_SENT' AND next_action_at IS NOT NULL AND next_action_at <= now()
            ORDER BY next_action_at LIMIT %s
            FOR UPDATE SKIP LOCKED
        )
        RETURNING *
        """,
        (limit,),
    ).fetchall()


def awaiting_reply(conn) -> List[Dict]:
    return conn.execute(
        "SELECT * FROM leads WHERE status IN ('SENT','FOLLOW_UP_SENT')"
    ).fetchall()


def stuck_in_hot_path(conn, lease_minutes: int, limit: int = 50) -> List[Dict]:
    """Hot-path leads whose stream evidently died (lost message, crashed worker
    with no redelivery). The reaper restarts them from the seed — re-running the
    stream is safe (send-side guards live in zone 2) and merely re-spends tokens."""
    return conn.execute(
        """
        SELECT * FROM leads
        WHERE status IN ('RESEARCH_PENDING', 'RESEARCH_COMPLETE', 'CONTACT_FOUND')
          AND updated_at < now() - make_interval(mins => %s)
        ORDER BY updated_at LIMIT %s
        """,
        (lease_minutes, limit),
    ).fetchall()


# ─── observability ─────────────────────────────────────────────────────────────

def log_agent_action(conn, lead_id: Optional[str], agent: str, action: str, status: str,
                     status_before: Optional[str] = None, status_after: Optional[str] = None,
                     prompt_version: Optional[str] = None, model: Optional[str] = None,
                     input_tokens: Optional[int] = None, output_tokens: Optional[int] = None,
                     latency_ms: Optional[int] = None, confidence: Optional[float] = None,
                     details: Optional[Dict] = None) -> None:
    conn.execute(
        """
        INSERT INTO agent_logs (lead_id, agent, action, status, status_before, status_after,
                                prompt_version, model, input_tokens, output_tokens,
                                latency_ms, confidence, details)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (lead_id, agent, action, status, status_before, status_after, prompt_version, model,
         input_tokens, output_tokens, latency_ms, confidence, Jsonb(details) if details else None),
    )


def recent_logs(conn, limit: int = 100) -> List[Dict]:
    return conn.execute(
        "SELECT * FROM agent_logs ORDER BY created_at DESC LIMIT %s", (limit,)
    ).fetchall()


def metrics(conn) -> Dict:
    counts = {
        r["status"]: r["n"]
        for r in conn.execute("SELECT status, count(*) AS n FROM leads GROUP BY status").fetchall()
    }
    total = sum(counts.values())
    sent_statuses = {"SENT", "FOLLOW_UP_SENT", "REPLY_RECEIVED", "INTERESTED", "BOOKING_DRAFTED"}
    terminal = transitions.TERMINAL_STATUSES | {"UPLOADED"}

    usage = conn.execute(
        "SELECT coalesce(sum(input_tokens),0) AS tin, coalesce(sum(output_tokens),0) AS tout "
        "FROM agent_logs"
    ).fetchone()
    real_cost = (
        usage["tin"] / 1_000_000 * settings.CLAUDE_INPUT_PRICE_PER_MTOK
        + usage["tout"] / 1_000_000 * settings.CLAUDE_OUTPUT_PRICE_PER_MTOK
    )

    return {
        "total_leads": total,
        "in_progress": sum(n for s, n in counts.items() if s not in terminal),
        "emails_sent": sum(n for s, n in counts.items() if s in sent_statuses),
        "replies": sum(n for s, n in counts.items()
                       if s in {"REPLY_RECEIVED", "INTERESTED", "BOOKING_DRAFTED"}),
        "meetings_booked": counts.get("BOOKING_DRAFTED", 0),
        "pending_review": counts.get("HUMAN_REVIEW", 0),
        "status_breakdown": counts,
        "llm_input_tokens": usage["tin"],
        "llm_output_tokens": usage["tout"],
        "estimated_cost_usd": round(real_cost, 4),
    }
