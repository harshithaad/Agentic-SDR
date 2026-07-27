"""Prometheus metrics + a heartbeat file for container liveness probes."""
import time
from pathlib import Path

from prometheus_client import Counter, Histogram, start_http_server

MESSAGES_PROCESSED = Counter(
    "sdr_messages_processed_total", "Command messages processed",
    ["stage", "result"],  # result: ok | conflict | duplicate | retryable_error | dlq
)
STAGE_DURATION = Histogram(
    "sdr_stage_duration_seconds", "Stage handler wall time", ["stage"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 45, 90, 180),
)
LLM_TOKENS = Counter(
    "sdr_llm_tokens_total", "Claude tokens used", ["prompt", "direction"],
)
LLM_CALLS = Counter(
    "sdr_llm_calls_total", "Claude API calls", ["prompt", "result"],
)
TRANSITIONS = Counter(
    "sdr_lead_transitions_total", "Lead state transitions", ["from_status", "to_status"],
)
OUTBOX_PUBLISHED = Counter("sdr_outbox_published_total", "Outbox rows published to Kafka")
SCHEDULER_ACTIONS = Counter(
    "sdr_scheduler_actions_total", "Scheduler-initiated actions", ["action"],
)

HEARTBEAT_FILE = Path("/tmp/sdr-heartbeat")


def start_metrics_server(port: int) -> None:
    start_http_server(port)


def beat() -> None:
    """Touch the heartbeat file; k8s liveness uses its age (exec probe)."""
    try:
        HEARTBEAT_FILE.write_text(str(int(time.time())))
    except OSError:
        pass
