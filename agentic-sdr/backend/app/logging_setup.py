"""Structured JSON logging. Every log line carries service, and handlers bind
lead_id / event_id / stage so a single lead's path is greppable across services."""
import logging
import sys
import structlog

from app.config import settings


def setup_logging() -> None:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
    # third-party noise down
    for noisy in ("httpx", "googleapiclient", "kafka", "aiokafka", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )


def get_logger(**initial):
    return structlog.get_logger().bind(service=settings.SERVICE_NAME, **initial)
