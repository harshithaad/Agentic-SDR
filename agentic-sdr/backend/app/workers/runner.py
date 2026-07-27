"""Worker runtime with two execution modes — the two zones of the architecture.

EOS mode (zone 1: research, contact) — event-carried streaming:
    consume → external work → produce(next cmd + fact evt) with the consumer
    offset committed INSIDE the same Kafka transaction. Exactly-once from topic
    to topic; Postgres is not touched on the happy path. A Park result exits the
    stream: the runner materializes the lead into Postgres at the boundary
    (dedupe + composite CAS + logs in one DB transaction), then commits the
    offset plainly.

DB mode (zone 2 + boundary: draft, send, classify) — durable workflow:
    handler owns a Postgres transaction (dedupe + CAS + logs + outbox), offset
    commits after. Unchanged semantics: at-least-once delivery, exactly-once
    effects.

Both modes: infra failures seek back and retry (outage delays, never loses);
poison messages go to the DLQ so a partition can never wedge; SIGTERM finishes
the in-flight message and leaves the group cleanly.
"""
import signal
import socket
import time
from typing import Dict

import psycopg
from confluent_kafka import Consumer, KafkaException, Producer, TopicPartition
from psycopg_pool import PoolTimeout

from app import events, kafka_bus, prompts, repository
from app.config import settings
from app.db import get_pool, run_migrations
from app.logging_setup import get_logger, setup_logging
from app.metrics import MESSAGES_PROCESSED, STAGE_DURATION, beat, start_metrics_server
from app.stages import classify, contact, draft, research, send
from app.stages.common import InfraError, Park, Produce, SkipMessage, tx
from app.transitions import TransitionConflict

STAGES: Dict[str, dict] = {
    "research": {"handler": research.handle, "group": research.GROUP, "mode": "eos",
                 "prompt_version": prompts.RESEARCH_PROMPT_VERSION},
    "contact":  {"handler": contact.handle,  "group": contact.GROUP,  "mode": "eos",
                 "prompt_version": None},
    "draft":    {"handler": draft.handle,    "group": draft.GROUP,    "mode": "db"},
    "send":     {"handler": send.handle,     "group": send.GROUP,     "mode": "db"},
    "classify": {"handler": classify.handle, "group": classify.GROUP, "mode": "db"},
}

_shutdown = False


def _request_shutdown(signum, frame):
    global _shutdown
    _shutdown = True


def run_worker(stage: str) -> None:
    setup_logging()
    log = get_logger(component="worker", stage=stage)
    spec = STAGES[stage]

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    run_migrations()
    get_pool()
    start_metrics_server(settings.METRICS_PORT)

    topic = events.CMD_TOPICS[stage]
    if spec["mode"] == "eos":
        _run_eos(stage, spec, topic, log)
    else:
        _run_db(stage, spec, topic, log)


# ─── zone 1: EOS streaming loop ───────────────────────────────────────────────

def _run_eos(stage: str, spec: dict, topic: str, log) -> None:
    tid = f"sdr-{stage}-{socket.gethostname()}"
    producer = kafka_bus.make_transactional_producer(tid)
    consumer = kafka_bus.make_consumer(spec["group"], [topic], read_committed=True)
    log.info("worker_started", topic=topic, group=spec["group"], mode="eos",
             transactional_id=tid)

    try:
        while not _shutdown:
            beat()
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                log.error("consumer_error", error=str(msg.error()))
                continue

            try:
                message = events.deserialize(msg.value())
            except (ValueError, UnicodeDecodeError) as e:
                _dlq_in_txn(producer, consumer, msg, stage, topic, f"malformed: {e}")
                MESSAGES_PROCESSED.labels(stage=stage, result="dlq").inc()
                continue

            mlog = log.bind(lead_id=message["lead_id"], event_id=message["event_id"])
            started = time.monotonic()
            try:
                result = spec["handler"](message)
                if isinstance(result, Produce):
                    _commit_stream_txn(producer, consumer, msg, result)
                    MESSAGES_PROCESSED.labels(stage=stage, result="ok").inc()
                    mlog.info("streamed", duration_s=round(time.monotonic() - started, 2))
                elif isinstance(result, Park):
                    _materialize_park(stage, spec, message, result)
                    consumer.commit(msg)
                    MESSAGES_PROCESSED.labels(stage=stage, result="parked").inc()
                    mlog.info("parked", to_status=result.to_status)
                else:
                    raise RuntimeError(f"handler returned {type(result)}")
            except SkipMessage as e:
                consumer.commit(msg)
                MESSAGES_PROCESSED.labels(stage=stage, result="duplicate").inc()
                mlog.info("message_skipped", reason=str(e))
            except (psycopg.OperationalError, PoolTimeout, InfraError) as e:
                MESSAGES_PROCESSED.labels(stage=stage, result="retryable_error").inc()
                mlog.warning("infra_error_retrying", error=str(e))
                _seek_back(consumer, msg)
                time.sleep(5)
            except KafkaException as e:
                MESSAGES_PROCESSED.labels(stage=stage, result="retryable_error").inc()
                mlog.warning("kafka_txn_error", error=str(e))
                _try_abort(producer)
                _seek_back(consumer, msg)
                time.sleep(5)
            except Exception as e:
                MESSAGES_PROCESSED.labels(stage=stage, result="dlq").inc()
                mlog.error("message_poisoned", error=str(e), exc_info=True)
                _try_abort(producer)
                _dlq_in_txn(producer, consumer, msg, stage, topic, str(e))
            finally:
                STAGE_DURATION.labels(stage=stage).observe(time.monotonic() - started)
    finally:
        log.info("worker_stopping")
        _close_quietly(consumer)


def _commit_stream_txn(producer: Producer, consumer: Consumer, msg, result: Produce) -> None:
    producer.begin_transaction()
    for out_topic, key, payload in result.messages:
        producer.produce(out_topic, key=str(key).encode(), value=events.serialize(payload))
    producer.send_offsets_to_transaction(
        [TopicPartition(msg.topic(), msg.partition(), msg.offset() + 1)],
        consumer.consumer_group_metadata(),
    )
    producer.commit_transaction()


def _materialize_park(stage: str, spec: dict, message: Dict, park: Park) -> None:
    with tx() as conn:
        if not repository.try_mark_processed(conn, message["event_id"], spec["group"]):
            return
        try:
            repository.materialize_from_hot_path(
                conn, message["lead_id"], park.to_status, by=stage,
                trace_id=message.get("trace_id"), **park.fields,
            )
        except TransitionConflict:
            return
        if park.llm_usage:
            for u in park.llm_usage:
                repository.log_agent_action(
                    conn, message["lead_id"], stage, park.log_action, park.log_status,
                    status_after=park.to_status,
                    prompt_version=spec.get("prompt_version"), model=u.model,
                    input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                    latency_ms=u.latency_ms, details=park.log_details,
                )
        else:
            repository.log_agent_action(
                conn, message["lead_id"], stage, park.log_action, park.log_status,
                status_after=park.to_status, details=park.log_details,
            )


def _dlq_in_txn(producer: Producer, consumer: Consumer, msg, stage: str,
                topic: str, error: str) -> None:
    """Poison exits via the DLQ atomically with the offset advance."""
    try:
        producer.begin_transaction()
        producer.produce(events.DLQ_TOPIC, key=stage.encode(),
                         value=msg.value() or b"{}",
                         headers=[("stage", stage.encode()),
                                  ("error", error[:500].encode()),
                                  ("original_topic", topic.encode())])
        producer.send_offsets_to_transaction(
            [TopicPartition(msg.topic(), msg.partition(), msg.offset() + 1)],
            consumer.consumer_group_metadata(),
        )
        producer.commit_transaction()
    except KafkaException:
        _try_abort(producer)
        consumer.commit(msg)  # advance anyway; DLQ delivery is best-effort


def _try_abort(producer: Producer) -> None:
    try:
        producer.abort_transaction()
    except KafkaException:
        pass


# ─── zone 2 + boundary: durable workflow loop (unchanged semantics) ───────────

def _run_db(stage: str, spec: dict, topic: str, log) -> None:
    consumer = kafka_bus.make_consumer(spec["group"], [topic], read_committed=True)
    log.info("worker_started", topic=topic, group=spec["group"], mode="db")

    try:
        while not _shutdown:
            beat()
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                log.error("consumer_error", error=str(msg.error()))
                continue

            try:
                message = events.deserialize(msg.value())
            except (ValueError, UnicodeDecodeError) as e:
                _send_to_dlq_plain(stage, msg.value(), f"malformed: {e}", topic)
                MESSAGES_PROCESSED.labels(stage=stage, result="dlq").inc()
                consumer.commit(msg)
                continue

            mlog = log.bind(lead_id=message["lead_id"], event_id=message["event_id"])
            started = time.monotonic()
            try:
                spec["handler"](message)
                MESSAGES_PROCESSED.labels(stage=stage, result="ok").inc()
                mlog.info("message_handled", duration_s=round(time.monotonic() - started, 2))
            except SkipMessage as e:
                MESSAGES_PROCESSED.labels(stage=stage, result="duplicate").inc()
                mlog.info("message_skipped", reason=str(e))
            except (psycopg.OperationalError, PoolTimeout, KafkaException, InfraError) as e:
                MESSAGES_PROCESSED.labels(stage=stage, result="retryable_error").inc()
                mlog.warning("infra_error_retrying", error=str(e))
                _seek_back(consumer, msg)
                time.sleep(5)
                continue
            except Exception as e:
                MESSAGES_PROCESSED.labels(stage=stage, result="dlq").inc()
                mlog.error("message_poisoned", error=str(e), exc_info=True)
                _send_to_dlq_plain(stage, msg.value(), str(e), topic)
            finally:
                STAGE_DURATION.labels(stage=stage).observe(time.monotonic() - started)

            consumer.commit(msg)
    finally:
        log.info("worker_stopping")
        _close_quietly(consumer)
        kafka_bus.flush(10)


def _send_to_dlq_plain(stage: str, raw_value: bytes, error: str, original_topic: str) -> None:
    kafka_bus.produce(
        events.DLQ_TOPIC, key=stage, value=raw_value if raw_value is not None else b"{}",
        headers={"stage": stage, "error": error[:500], "original_topic": original_topic},
    )
    kafka_bus.flush(5)


def _seek_back(consumer, msg) -> None:
    try:
        consumer.seek(TopicPartition(msg.topic(), msg.partition(), msg.offset()))
    except KafkaException:
        pass  # partition revoked during rebalance; poll() resumes correctly


def _close_quietly(consumer) -> None:
    try:
        consumer.close()
    except KafkaException:
        pass
