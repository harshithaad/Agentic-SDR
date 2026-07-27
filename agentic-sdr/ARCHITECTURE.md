# Agentic SDR — Production Architecture

Event-driven, multi-agent sales development pipeline: CSV of companies in →
researched, personalized, sent, followed-up, replies classified, meetings
drafted — with humans pulled in exactly where confidence or compliance demands.

```
                                ┌───────────────────────────── Kubernetes ──────────────────────────────┐
 Browser ── SSE + REST ──▶ API (FastAPI ×2) ── outbox ──▶ ┌──────────── Kafka (KRaft) ────────────┐     │
    ▲                          │      ▲                   │ sdr.cmd.research  ─▶ research-worker  │     │
    │                          ▼      │                   │ sdr.cmd.contact   ─▶ contact-worker   │     │
    └── live lead events ── Postgres ◀┴── CAS writes ──── │ sdr.cmd.draft     ─▶ draft-worker     │     │
        (sdr.evt.leads)     (source of truth:             │ sdr.cmd.send      ─▶ send-worker      │     │
                             leads / outbox /             │ sdr.cmd.classify  ─▶ classify-worker  │     │
                             processed_events /           │ sdr.evt.leads     ─▶ API SSE bridge   │     │
                             suppression / logs)          │ sdr.dlq           ─▶ operator         │     │
                               ▲                          └───────────────────────────────────────┘     │
                               │ timers · gmail poll · bounce scan · reaper · outbox relay              │
                            scheduler (advisory-lock leader)                                            │
                                                                                                        │
 External: Anthropic Claude · Firecrawl · Serper · Hunter · Apollo · Gmail ─────────────────────────────┘
```

## 1. The two-plane design

**Postgres is the source of truth. Kafka is the transport.** Every fact about a
lead lives in one row; Kafka messages are *instructions to act* (`sdr.cmd.*`)
or *notifications that something happened* (`sdr.evt.leads`). This ordering of
authority resolves the classic dual-write problem via the **transactional
outbox**: a stage commits its state change AND its intent-to-publish in one
Postgres transaction; the scheduler's relay drains the outbox to Kafka and
marks rows published only after broker acks. If the relay dies mid-batch, rows
stay unpublished and are re-sent — consumers dedupe (see §3). The two systems
can lag, but they cannot drift.

## 1b. The two zones — stream while moving, persist when stopping

The pipeline has two consistency models, split where the workload changes
character:

**Zone 1 — the moving pipeline (research → contact → draft).** Event-carried
state transfer under **Kafka exactly-once transactions**: each command CARRIES
the accumulated lead data (seed → +research → +contact), and each worker runs
consume → external work → produce(next command + fact event) with the consumer
offset committed inside the same Kafka transaction. **No Postgres reads or
writes on the happy path.** Consumers run `isolation.level=read_committed`;
producers use a stable `transactional.id` per worker so restarts fence their
zombie predecessors. Kafka-to-Kafka is the one place exactly-once is real, and
this is it.

**The boundary.** The moment a lead *stops moving* — draft completed, guardrail
failure, low confidence, no contact — it **materializes** into Postgres in one
composite write (`materialize_from_hot_path`): all accumulated fields + the
target status, CAS-guarded against any hot-path status. From that write onward
the lead is zone-2 property.

**Zone 2 — the parking lot (send → waits → replies → review).** Durable
workflow state exactly as below: CAS state machine, transactional outbox,
claim lease, suppression, timers in `next_action_at`. This zone exists because
Kafka cannot park a lead for 72 hours, humans need random-access reads and
writes, Gmail has no idempotency keys, and compliance must be queryable.

Two supporting pieces: a **projector** (in the scheduler) tails the fact stream
and shadow-advances hot-path statuses in the read model so the dashboard stays
live — cosmetic writes that lose every race by design; and the **reaper**
re-drives a wedged hot-path lead from its seed (safe: send-side guards live in
zone 2; the cost is re-spent tokens).

Accepted zone-1 trade-offs, stated plainly: a crash after the external calls
but before the Kafka commit re-runs the stage on redelivery (re-pays Claude
tokens — there is no processed_events dedupe inside the stream, offsets ARE the
dedupe); and in-flight dashboard statuses are eventually consistent by
roughly a second via the projector.

## 2. Topics, partitions, ordering

| Topic | Partitions | Key | Consumers | Retention |
|---|---|---|---|---|
| `sdr.cmd.research` | 6 | lead_id | `research-workers` (EOS) | default |
| `sdr.cmd.contact` | 6 | lead_id | `contact-workers` (EOS) | default |
| `sdr.cmd.draft` | 6 | lead_id | `draft-workers` (boundary) | default |
| `sdr.cmd.send` | 6 | lead_id | `send-workers` (1 replica: mail rate control) | default |
| `sdr.cmd.classify` | 6 | lead_id | `classify-workers` | default |
| `sdr.evt.leads` | 6 | lead_id | SSE bridge (broadcast); future: CRM sync, analytics | 7 days |
| `sdr.dlq` | 3 | stage | operator tooling | 14 days |

Keying by `lead_id` guarantees **per-lead ordering** (all messages for one lead
hit one partition, processed serially) while distinct leads fan out across
partitions for parallelism. Partition count (6) is the per-stage parallelism
ceiling — the research HPA maxes at 6 replicas for exactly this reason.
Auto-topic-creation is disabled; topics are provisioned infrastructure.

## 3. Delivery semantics — honest version

- **Producer:** idempotent (`enable.idempotence`, `acks=all`) — broker-side
  dedupe of producer retries.
- **Consumer:** manual offset commit, only after the handler's DB transaction
  commits. Redeliveries therefore happen (at-least-once)…
- **…but effects are exactly-once** because every handler transaction starts by
  inserting the message's `event_id` into `processed_events` (PK on
  `(event_id, consumer_group)`); a redelivered message conflicts and is skipped.
- **The irreducible edge:** Gmail has no idempotency keys, so a crash in the
  window between "email accepted by Gmail" and "transaction committed" can, on
  redelivery, produce a duplicate email. We narrow the window with a **claim
  lease** (`claimed_at` CAS with expiry): claim → send → finalize. A live claim
  blocks all peers; an expired claim (crashed worker) self-heals. This is the
  correct engineering answer — anyone promising exactly-once side effects on an
  external SMTP API is selling something.

## 4. The state machine is enforced, not documented

Every status write compiles to
`UPDATE leads SET status=<to>, version=version+1 WHERE id=<id> AND status=<from>`.
Zero rows updated ⇒ `TransitionConflict` ⇒ the caller loses the race and stops.
Stale workers, duplicate messages, and concurrent human actions are *resolved
by the database*, not by hoping processes behave. The matrix lives in
`app/transitions.py` (unit-tested), and the DB has a CHECK constraint on the
status enum as a second fence.

## 5. Time is data

One indexed column — `next_action_at` — drives all scheduled behavior. Send
sets it to +72 h; the scheduler pops due rows with
`FOR UPDATE SKIP LOCKED` (atomically clearing the timer) and enqueues follow-up
commands; `FOLLOW_UP_SENT` + 72 h expires to `CLOSED_LOST`. No sleeping code,
no cron drift, restart-safe, and visible in SQL (`WHERE next_action_at IS NOT
NULL` is the pending-timer list).

## 6. Failure handling

| Failure | Mechanism |
|---|---|
| Claude/HTTP transient errors | bounded exponential retries in-process (tenacity) |
| DB or broker down | worker seeks back to the same offset, sleeps, retries — outage delays, never skips |
| Poison message | DLQ (`sdr.dlq`) with stage + error headers; offset advances; partition never wedges |
| Worker crash mid-message | offset not committed → redelivery → `processed_events` dedupe |
| Command lost after commit | reaper re-issues commands for leads stuck in claimable states past a lease |
| Low LLM confidence / sensitive keywords / "are you an AI?" | `HUMAN_REVIEW` with the reason persisted (`review_reason`) |
| Hard bounce | scheduler bounce scan → `INVALID_EMAIL` + suppression list |
| Opt-out (STOP/unsubscribe) | deterministic regex BEFORE any LLM; permanent suppression row; CAN-SPAM never depends on a confidence score |

## 7. Compliance gates (deterministic, pre-send)

1. Syntactic address validation.
2. `suppression_list` (permanent; fed by opt-outs and bounces).
3. 7-day resend window per address (SQL check across all leads).
4. Duplicate company per batch rejected by a partial unique index at ingest.
5. Draft guardrails validated in code (<200 words, opt-out line, one
   personalization fact, no pricing); one retry *with the rejection reason fed
   back*, then human review.

## 8. Observability

- `agent_logs`: every action with `prompt_version`, `model`, `input_tokens`,
  `output_tokens`, `latency_ms`, `confidence` — the dashboard's cost figure is
  **computed from real token usage × price table**, not an estimate constant.
- Prometheus on every service (`/metrics`): message throughput per stage/result,
  stage latency histograms, LLM token counters, transition counters, DLQ rate.
- Structured JSON logs with `lead_id` / `event_id` / `trace_id` bound — one
  lead's story is a single grep across all services.
- Health: API `/healthz` + `/readyz` (DB ping); workers/scheduler touch a
  heartbeat file the kubelet checks with an exec probe.

## 9. Kubernetes topology

One backend image, many roles (command overrides). API ×2 behind a Service;
five worker Deployments scaled independently (HPA on research, ceiling = 
partition count); scheduler `Recreate` + Postgres advisory lock = leader
election without new infrastructure; Kafka/Postgres as single-node StatefulSets
locally with an explicit note that production swaps in Strimzi/managed
services; secrets in a `Secret`, tunables in a `ConfigMap`; graceful shutdown
everywhere (SIGTERM → finish in-flight message → commit → leave group).

## 10. Trade-offs we accepted knowingly

- **Gmail polling (60 s), not push** — Gmail push needs a public HTTPS endpoint
  + Google Pub/Sub; the poll window is bounded by the oldest awaiting lead.
- **Single-broker Kafka locally** — replication factor 1 is a dev posture; the
  manifests say so and the app is broker-count agnostic.
- **The scheduler is a singleton** — leadership via advisory lock is boring and
  correct at this scale; sharding timers across workers would be resume-padding.
- **LangGraph was removed** — each stage consumes one topic and does one job;
  the graph IS the topic topology now. A framework whose checkpointing and
  interrupts we replaced with Postgres rows had no remaining job.
- **Free-tier quotas still bound throughput** (Hunter 25/mo) — Kafka buys
  operational properties (isolation, replay, independent scaling, DLQ), not
  more quota. Scale-mismatch acknowledged and accepted for credibility goals.
