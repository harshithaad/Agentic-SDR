---
name: agentic-sdr
description: Architecture, invariants, deliberate choices, and quality bar for the Agentic AI SDR project (agentic-sdr/ — event-driven Kafka pipeline, FastAPI API, five stage workers, scheduler, Postgres source of truth, React dashboard, Docker/Kubernetes deploy). Use this skill whenever reading, modifying, extending, debugging, or explaining ANY part of this project — stages, topics, transitions, API, dashboard, prompts, schema, or deploy manifests. ALSO read it before proposing infrastructure changes (brokers, orchestration frameworks, LLM subagents, webhooks) — the current shape reflects explicit user decisions. If a request touches cold email, lead states, confidence thresholds, or Kafka topics, read this first.
---

# Agentic AI SDR — Project Skill (v2, event-driven)

Contest entry for the Salesforce Compass Program. Given a CSV of companies the
pipeline researches each one, finds a verified decision-maker, writes a
personalized cold email, sends via Gmail, follows up after 72 h, classifies
replies, and drafts booking emails — with deterministic compliance gates and
human escalation.

**History note:** v1 was a single-process LangGraph monolith. On 2026-07-14 the
user explicitly chose to rebuild production-grade with **Kafka + Kubernetes**
(credibility goal; scale mismatch with free-tier quotas acknowledged and
accepted). LangGraph was removed — each stage is a Kafka consumer; the topic
topology IS the graph. Do not reintroduce it without a new user decision.

Authoritative docs in the repo — read them before structural changes:
- `agentic-sdr/ARCHITECTURE.md` — topics/partitions, outbox, delivery
  semantics, failure matrix, K8s topology, accepted trade-offs
- `agentic-sdr/RUNBOOK.md` — compose + kind instructions, ops queries

## Architecture in one paragraph (two zones, as of 2026-07-19)

**Zone 1 (hot path: research → contact → draft)** is event-carried streaming
under **Kafka EOS transactions**: commands CARRY the accumulated payload
(seed → +research → +contact), workers do consume→work→produce with offsets
committed inside the Kafka transaction, and **Postgres is not touched on the
happy path**. Exits (park/fail/draft-done) **materialize** into Postgres via
`repository.materialize_from_hot_path` (composite CAS accepting any hot-path
status). A projector in the scheduler shadow-advances hot-path statuses in the
read model for the dashboard. **Zone 2 (send → waits → replies → review)** is
the durable workflow: Postgres is the source of truth, thin commands, the
transactional **outbox**, `cas_transition()` CAS guards, processed_events
dedupe, claim leases for Gmail, timers in `next_action_at`, advisory-lock
scheduler leadership. Rule that names the design: **data streams while it
moves and persists when it stops.** User decision 2026-07-19 (mentor +
colleague expectations) — do not collapse the zones without a new decision.

## Repo map

```
agentic-sdr/backend/app/
  api/main.py        REST + SSE; sync-def routes on threadpool; no heavy work ever
  workers/runner.py  poll → handle → commit; infra errors seek back; poison → DLQ
  stages/            research, contact, draft, send, classify (+ common.py gates)
  scheduler.py       outbox relay, 72h timers, gmail poll, bounce scan, reaper
  transitions.py     VALID_TRANSITIONS + CAS SQL builder (pure; unit-tested)
  repository.py      ALL SQL lives here
  integrations/      firecrawl, serper, contacts (hunter verified-only, apollo), gmail
  llm.py             Claude wrapper: tenacity retries, token+latency capture
  prompts.py         versioned prompts — bump version when text changes
agentic-sdr/backend/migrations/   forward-only SQL
agentic-sdr/backend/tests/        python -m unittest discover tests
agentic-sdr/deploy/               docker-compose.yml + k8s/*.yaml
```

## Invariants — never break these

1. **No direct status writes.** Zone 2 uses `repository.cas_transition`;
   zone-1 exits use `repository.materialize_from_hot_path` (targets limited to
   `MATERIALIZE_TARGETS`). Adding a state touches `transitions.py`, the SQL
   CHECK constraint (new migration), and frontend badge colors.
2. **Producer discipline per zone.** Zone-1 workers produce ONLY inside their
   EOS transaction (runner owns it — handlers return `Produce`/`Park`, never
   call the producer). Zone 2 + API never produce directly: commands/events go
   through `repository.outbox_add` in the same tx as the state change; only the
   scheduler's relay and the DLQ paths touch the producer.
3. **Boundary/zone-2 handler transaction = dedupe + transition + logs + outbox.**
   One commit. External calls (Claude/Gmail/HTTP) happen BEFORE the transaction,
   never inside. Zone-1 dedupe is the offsets-in-transaction; a redelivered
   hot-path message re-runs external work (token cost, not a correctness bug).
4. **Send claim lease stays.** `claim_for_send` / `claim_for_follow_up` CAS +
   lease expiry is the double-send defense; Gmail has no idempotency keys.
5. **Deterministic compliance before any LLM:** opt-out regex → suppression
   list + CLOSED_LOST; "are you an AI?" and sensitive keywords → HUMAN_REVIEW;
   suppression + 7-day resend window + address validation before every send;
   Hunter results used only when verification.status == "valid".
6. **Thresholds (user-owned):** research ≥ 0.65; INTERESTED auto ≥ 0.80;
   NOT_INTERESTED auto-close ≥ 0.85; anything < 0.75 → HUMAN_REVIEW.
7. **Observability is spec:** every agent action logs prompt_version, model,
   input/output tokens, latency; dashboard cost is computed from token logs.
8. **Topic changes are infra changes:** partitions are set in BOTH
   deploy/docker-compose.yml (kafka-init) and deploy/k8s/12-kafka-topics-job.yaml;
   consumer parallelism ceiling = partition count (HPA max must match).
9. **Secrets:** `.env` locally, k8s Secret in cluster. APP_PROFILE=prod fails
   boot on missing keys — do not weaken that.

## Environment facts (as of 2026-07-14)

- **Docker Desktop is NOT installed** on this machine — the stack can't run
  locally until the user installs it (WSL2 backend). kind/kubectl also absent.
- **API keys are NOT yet provided** (Anthropic, Firecrawl, Serper, Hunter,
  Apollo, Gmail). `DATABASE_URL` replaced SUPABASE_URL/KEY — a Supabase
  Postgres connection string works fine.
- Local venv (`backend/.venv`, Python 3.14) is for unit tests/py_compile only;
  confluent-kafka has no 3.14 Windows wheel — runtime is the Docker image
  (python:3.12-slim). 19 unit tests green, frontend `npm run build` green.
- `D:\sdrapp` is a junction to `agentic-sdr/` (path-with-space workaround);
  vite.config.ts `preserveSymlinks` + `fs.allow` depend on it.
- Path quoting: `"D:\Salesforce Compass Program"` — always quote; PowerShell
  5.1 (no `&&`).

## Quality bar

Unchanged from spec §13 (research < 45 s/lead, ≥ 90% unaided terminal state,
≥ 85% intent accuracy, 100% actions logged, guardrails on every send) plus:
unit tests must stay green (`python -m unittest discover tests`), `APP_PROFILE=prod`
must fail fast on missing keys, and a lead's full path must be reconstructible
from agent_logs + `sdr.evt.leads` alone.
