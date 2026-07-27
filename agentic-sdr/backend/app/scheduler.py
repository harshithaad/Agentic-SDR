"""Scheduler / reconciler service.

One process owns all time-driven work, guarded by a Postgres advisory lock so a
second replica is a hot standby, not a double-actor:

- outbox relay (every tick):    unpublished rows → Kafka, then marked published
- timers (every 30 s):          SENT + 72 h → follow-up command;
                                FOLLOW_UP_SENT + 72 h → CLOSED_LOST
- gmail poll (every 60 s):      inbox replies matched on rfc_message_id
                                (thread id as fallback) → REPLY_RECEIVED + classify command
- bounce scan (every 5 min):    MAILER-DAEMON in thread → INVALID_EMAIL + suppression
- reaper (every 60 s):          leads stuck in a *_PENDING/claimable state whose
                                command evidently died → command re-issued
Every action goes through the same outbox + CAS-transition machinery as the
workers; the scheduler never bypasses the state machine.
"""
import signal
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from confluent_kafka import KafkaException

from app import events, kafka_bus, repository
from app.config import settings
from app.db import get_pool, run_migrations
from app.integrations import gmail
from app.logging_setup import get_logger, setup_logging
from app.metrics import OUTBOX_PUBLISHED, SCHEDULER_ACTIONS, beat, start_metrics_server
from app.transitions import TransitionConflict

log = get_logger(component="scheduler")

ADVISORY_LOCK_KEY = 815_2026  # arbitrary, stable

_shutdown = False


def _request_shutdown(signum, frame):
    global _shutdown
    _shutdown = True


# ─── outbox relay ──────────────────────────────────────────────────────────────

def relay_outbox() -> int:
    """Publish claimed outbox rows; mark published in the same transaction that
    claimed them, but only after the broker acked every message (flush)."""
    published = 0
    with get_pool().connection() as conn:
        with conn.transaction():
            rows = repository.outbox_claim_batch(conn, limit=200)
            if not rows:
                return 0
            for row in rows:
                kafka_bus.produce(
                    row["topic"], key=row["key"],
                    value=events.serialize(row["payload"]),
                    headers=row.get("headers"),
                )
            remaining = kafka_bus.flush(10)
            if remaining:
                raise KafkaException(f"{remaining} outbox messages unacked; will retry")
            repository.outbox_mark_published(conn, [r["id"] for r in rows])
            published = len(rows)
    OUTBOX_PUBLISHED.inc(published)
    return published


# ─── timers ────────────────────────────────────────────────────────────────────

def fire_timers() -> None:
    with get_pool().connection() as conn:
        with conn.transaction():
            for lead in repository.due_for_follow_up(conn):
                cmd = events.make_message(
                    events.CMD_SEND_EMAIL, str(lead["id"]), {"kind": "follow_up"}
                )
                repository.outbox_add(conn, events.CMD_TOPICS["send"], str(lead["id"]), cmd)
                SCHEDULER_ACTIONS.labels(action="follow_up_enqueued").inc()

            for lead in repository.due_for_expiry(conn):
                try:
                    repository.cas_transition(
                        conn, str(lead["id"]), "FOLLOW_UP_SENT", "CLOSED_LOST",
                        by="scheduler",
                        intent_reasoning="No reply within the follow-up window.",
                    )
                    SCHEDULER_ACTIONS.labels(action="expired_to_closed_lost").inc()
                except TransitionConflict:
                    pass  # a reply arrived in the same instant — it wins


# ─── gmail reply polling ───────────────────────────────────────────────────────

def poll_gmail() -> None:
    with get_pool().connection() as conn:
        waiting = repository.awaiting_reply(conn)
    if not waiting:
        return

    sent_ats = [l["sent_at"] for l in waiting if l.get("sent_at")]
    since_epoch: Optional[int] = None
    if sent_ats:
        since_epoch = int(min(sent_ats).timestamp())

    try:
        replies = gmail.poll_replies(since_epoch=since_epoch)
    except gmail.GmailNotConfigured:
        return
    except Exception as e:
        log.warning("gmail_poll_failed", error=str(e))
        SCHEDULER_ACTIONS.labels(action="gmail_poll_error").inc()
        return

    by_rfc: Dict[str, Dict] = {
        l["rfc_message_id"]: l for l in waiting if l.get("rfc_message_id")
    }
    by_thread: Dict[str, Dict] = {
        l["gmail_thread_id"]: l for l in waiting if l.get("gmail_thread_id")
    }

    for reply in replies:
        lead = by_rfc.get(reply["in_reply_to"]) or by_thread.get(reply["thread_id"])
        if not lead:
            continue
        with get_pool().connection() as conn:
            with conn.transaction():
                try:
                    repository.cas_transition(
                        conn, str(lead["id"]), lead["status"], "REPLY_RECEIVED",
                        by="scheduler",
                        reply_text=reply["body"][:5000],
                        reply_received_at=datetime.now(timezone.utc),
                        next_action_at=None,
                    )
                except TransitionConflict:
                    continue  # already recorded (e.g. previous poll)
                cmd = events.make_message(events.CMD_CLASSIFY_REPLY, str(lead["id"]))
                repository.outbox_add(conn, events.CMD_TOPICS["classify"], str(lead["id"]), cmd)
                repository.log_agent_action(
                    conn, str(lead["id"]), "scheduler", "reply_received", "success",
                    status_before=lead["status"], status_after="REPLY_RECEIVED",
                    details={"from": reply["from"], "gmail_message_id": reply["message_id"]},
                )
                SCHEDULER_ACTIONS.labels(action="reply_matched").inc()
        # a matched lead leaves the waiting set
        by_rfc.pop(reply["in_reply_to"], None)
        by_thread.pop(reply["thread_id"], None)


# ─── bounce scan ───────────────────────────────────────────────────────────────

def scan_bounces() -> None:
    with get_pool().connection() as conn:
        candidates = conn.execute(
            """
            SELECT * FROM leads
            WHERE status = 'SENT' AND gmail_thread_id IS NOT NULL
              AND sent_at > now() - interval '3 days'
            """
        ).fetchall()

    for lead in candidates:
        try:
            bounced = gmail.check_bounce(lead["gmail_thread_id"])
        except gmail.GmailNotConfigured:
            return
        except Exception as e:
            log.warning("bounce_check_failed", lead_id=str(lead["id"]), error=str(e))
            continue
        if not bounced:
            continue
        with get_pool().connection() as conn:
            with conn.transaction():
                try:
                    repository.cas_transition(
                        conn, str(lead["id"]), "SENT", "INVALID_EMAIL",
                        by="scheduler", next_action_at=None,
                        error_message="Delivery failure notification detected in thread.",
                    )
                except TransitionConflict:
                    continue
                repository.suppress_email(
                    conn, lead.get("contact_email") or "",
                    reason="hard bounce", lead_id=str(lead["id"]),
                )
                SCHEDULER_ACTIONS.labels(action="bounce_detected").inc()


# ─── reaper ────────────────────────────────────────────────────────────────────

def reap_stuck() -> None:
    """Re-issue commands for leads whose message evidently died in flight."""
    with get_pool().connection() as conn:
        with conn.transaction():
            for lead in repository.stuck_in_hot_path(
                conn, settings.STUCK_LEAD_LEASE_MINUTES
            ):
                lead_id = str(lead["id"])
                # shadow statuses reset to the stream's entry state first
                if lead["status"] != "RESEARCH_PENDING":
                    try:
                        repository.cas_transition(
                            conn, lead_id, lead["status"], "RESEARCH_PENDING",
                            by="reaper", emit_event=False,
                        )
                    except TransitionConflict:
                        continue
                seed = {"company_name": lead["company_name"], "website": lead.get("website")}
                cmd = events.make_message(events.CMD_RESEARCH_LEAD, lead_id, seed)
                repository.outbox_add(conn, events.CMD_TOPICS["research"], lead_id, cmd)
                conn.execute(  # bump updated_at so we don't re-issue every minute
                    "UPDATE leads SET retry_count = retry_count + 1 WHERE id = %s",
                    (lead["id"],),
                )
                SCHEDULER_ACTIONS.labels(action="reaped_research").inc()

            stuck_drafts = conn.execute(
                """
                SELECT * FROM leads
                WHERE status = 'DRAFT_READY' AND human_approval_required = false
                  AND gmail_message_id IS NULL
                  AND updated_at < now() - make_interval(mins => %s)
                LIMIT 50
                """,
                (settings.STUCK_LEAD_LEASE_MINUTES,),
            ).fetchall()
            for lead in stuck_drafts:
                cmd = events.make_message(
                    events.CMD_SEND_EMAIL, str(lead["id"]), {"kind": "initial"}
                )
                repository.outbox_add(conn, events.CMD_TOPICS["send"], str(lead["id"]), cmd)
                conn.execute(
                    "UPDATE leads SET retry_count = retry_count + 1 WHERE id = %s",
                    (lead["id"],),
                )
                SCHEDULER_ACTIONS.labels(action="reaped_send").inc()


# ─── projector: shadow hot-path progress into the read model ──────────────────

_projector = None

SHADOW_TRANSITIONS = {
    ("RESEARCH_PENDING", "RESEARCH_COMPLETE"),
    ("RESEARCH_COMPLETE", "CONTACT_FOUND"),
}


def _get_projector():
    global _projector
    if _projector is None:
        from confluent_kafka import Consumer
        _projector = Consumer({
            "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
            "group.id": "sdr-projector",
            "enable.auto.commit": True,
            "auto.offset.reset": "latest",
            "isolation.level": "read_committed",
            "client.id": "sdr-projector",
        })
        _projector.subscribe([events.EVT_LEADS])
    return _projector


def project_hot_path_events(max_batch: int = 200) -> None:
    """Zone-1 leads live in the stream; the dashboard still deserves live rows.
    This consumes the fact stream and shadow-advances hot-path statuses in the
    read model. Purely cosmetic writes — they lose every race by design (the
    boundary materialization CAS accepts any hot-path status)."""
    consumer = _get_projector()
    for _ in range(max_batch):
        msg = consumer.poll(0.05)
        if msg is None:
            return
        if msg.error():
            continue
        try:
            evt = events.deserialize(msg.value())
        except (ValueError, UnicodeDecodeError):
            continue
        data = evt.get("data") or {}
        pair = (data.get("from"), data.get("to"))
        if evt.get("type") != events.EVT_LEAD_TRANSITIONED or pair not in SHADOW_TRANSITIONS:
            continue
        try:
            with get_pool().connection() as conn:
                with conn.transaction():
                    repository.shadow_status(conn, evt["lead_id"], pair[0], pair[1])
            SCHEDULER_ACTIONS.labels(action="projected").inc()
        except Exception as e:
            log.warning("projector_write_failed", lead_id=evt.get("lead_id"), error=str(e))


# ─── main loop ─────────────────────────────────────────────────────────────────

def acquire_leadership(conn) -> bool:
    row = conn.execute("SELECT pg_try_advisory_lock(%s) AS got", (ADVISORY_LOCK_KEY,)).fetchone()
    return bool(row["got"])


def main() -> None:
    setup_logging()
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    run_migrations()
    start_metrics_server(settings.METRICS_PORT)

    # dedicated session holds the advisory lock for the process lifetime
    lock_conn = get_pool().getconn()
    lock_conn.autocommit = True
    while not _shutdown and not acquire_leadership(lock_conn):
        log.info("standby_waiting_for_leadership")
        time.sleep(5)
    if _shutdown:
        return
    log.info("scheduler_leading")

    tick = 0
    while not _shutdown:
        beat()
        started = time.monotonic()
        try:
            relay_outbox()
            project_hot_path_events()
            if tick % settings.TIMER_EVERY_TICKS == 0:
                fire_timers()
            if tick % settings.GMAIL_POLL_EVERY_TICKS == 0:
                poll_gmail()
            if tick % settings.BOUNCE_SCAN_EVERY_TICKS == 0:
                scan_bounces()
            if tick % settings.REAPER_EVERY_TICKS == 0:
                reap_stuck()
        except Exception as e:
            log.error("scheduler_tick_failed", error=str(e), exc_info=True)
        tick += 1
        elapsed = time.monotonic() - started
        time.sleep(max(0.0, settings.SCHEDULER_TICK_SECONDS - elapsed))

    get_pool().putconn(lock_conn)
    log.info("scheduler_stopped")


if __name__ == "__main__":
    main()
