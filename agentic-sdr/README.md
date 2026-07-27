# Agentic SDR

Production-grade, event-driven multi-agent SDR system. Upload a CSV of target
companies; the pipeline researches each company, finds a verified
decision-maker, writes a personalized cold email, sends it via Gmail, follows
up after 72 h, classifies replies, and drafts meeting-booking emails —
escalating to a human whenever confidence or compliance rules demand.

**Stack:** FastAPI · Kafka (KRaft) · PostgreSQL · React/Vite · Docker · Kubernetes ·
Anthropic Claude · Firecrawl · Serper · Hunter/Apollo · Gmail API

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — topics/partitions, transactional
  outbox, delivery semantics, enforced state machine, failure matrix, K8s topology
- **[RUNBOOK.md](RUNBOOK.md)** — how to run it (docker-compose or kind),
  operate it, and bootstrap the Gmail token

## 60-second start

```powershell
copy .env.example .env      # add keys when you have them; dev profile runs without
cd deploy
docker compose --env-file ..\.env up --build -d
# open http://localhost:5173
```

## Repository layout

```
backend/
  app/
    api/            FastAPI service (REST + SSE live events)
    workers/        generic consumer runtime (one image, five stages)
    stages/         research | contact | draft | send | classify handlers
    integrations/   firecrawl, serper, hunter/apollo, gmail
    scheduler.py    outbox relay, timers, gmail poll, bounce scan, reaper
    transitions.py  the enforced state machine (CAS at the database)
    repository.py   every SQL statement in the system
  migrations/       forward-only SQL migrations
  tests/            state machine, compliance rules, event contracts
frontend/           React dashboard (SSE-driven, polling fallback)
deploy/
  docker-compose.yml
  k8s/              namespace, secrets, kafka, postgres, services, HPA, ingress
```

## Tests

```powershell
cd backend
python -m unittest discover tests -v
```
