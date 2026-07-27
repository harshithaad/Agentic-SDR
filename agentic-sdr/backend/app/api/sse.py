"""SSE bridge: one aiokafka consumer per API pod tails the fact stream
(sdr.evt.leads) and fans events out to connected dashboards. group_id=None means
every pod sees every event — this is a broadcast, not a work queue."""
import asyncio
import json
from typing import Set

from aiokafka import AIOKafkaConsumer

from app import events
from app.config import settings
from app.logging_setup import get_logger

log = get_logger(component="sse")


class Broadcast:
    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, item: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                # a stalled browser must not backpressure the pipeline view
                self._subscribers.discard(q)


broadcast = Broadcast()


async def consume_lead_events(stop: asyncio.Event) -> None:
    consumer = AIOKafkaConsumer(
        events.EVT_LEADS,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id=None,
        auto_offset_reset="latest",
        enable_auto_commit=False,
    )
    while not stop.is_set():
        try:
            await consumer.start()
            break
        except Exception as e:
            log.warning("sse_consumer_start_failed", error=str(e))
            await asyncio.sleep(5)
    else:
        return

    log.info("sse_consumer_started")
    try:
        while not stop.is_set():
            batch = await consumer.getmany(timeout_ms=1000)
            for _tp, msgs in batch.items():
                for msg in msgs:
                    try:
                        broadcast.publish(json.loads(msg.value.decode("utf-8")))
                    except (ValueError, UnicodeDecodeError):
                        continue
    finally:
        await consumer.stop()
        log.info("sse_consumer_stopped")
