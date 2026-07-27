# Runbook

## Prerequisites
- Docker Desktop (WSL2 backend on Windows) — required for both paths
- `kubectl` + `kind` only for the Kubernetes path

## Path A — docker-compose (fastest full stack)

```powershell
cd "D:\Salesforce Compass Program\agentic-sdr"
copy .env.example .env          # fill in keys; leave APP_PROFILE=dev until you have them
cd deploy
docker compose --env-file ..\.env up --build -d
docker compose ps               # everything should be running/healthy
```

Open http://localhost:5173 — header badge should read **Live** (SSE connected).

Smoke test without any API keys (`APP_PROFILE=dev`):
```powershell
# upload a CSV; the lead will move UPLOADED -> RESEARCH_PENDING -> RESEARCH_FAILED
# (research parks it with a clear 'key not set' error) — proving the event flow,
# outbox relay, workers, DLQ-safety and live dashboard without spending quota.
curl.exe -F "file=@..\demo_leads.csv" http://localhost:8000/api/leads/upload
```

With real keys in `.env`: set `APP_PROFILE=prod`, `docker compose up -d` again —
prod boot fails fast if any required key is missing.

Useful:
```powershell
docker compose logs -f worker-research      # one stage's structured logs
docker compose logs -f scheduler            # relay/timers/poll/reaper
curl.exe http://localhost:8000/api/metrics  # real token cost included
```

## Path B — Kubernetes (kind)

```powershell
kind create cluster --name sdr
docker build -t agentic-sdr-backend:local  "D:\Salesforce Compass Program\agentic-sdr\backend"
docker build -t agentic-sdr-frontend:local "D:\Salesforce Compass Program\agentic-sdr\frontend"
kind load docker-image agentic-sdr-backend:local  --name sdr
kind load docker-image agentic-sdr-frontend:local --name sdr

cd "D:\Salesforce Compass Program\agentic-sdr\deploy\k8s"
copy 01-secret.example.yaml 01-secret.yaml   # fill in stringData values
kubectl apply -f 00-namespace.yaml
kubectl apply -f 01-secret.yaml -f 02-configmap.yaml
kubectl apply -f 10-postgres.yaml -f 11-kafka.yaml
kubectl -n agentic-sdr wait --for=condition=ready pod -l app=kafka --timeout=180s
kubectl apply -f 12-kafka-topics-job.yaml
kubectl apply -f 20-api.yaml -f 21-workers.yaml -f 22-scheduler.yaml -f 23-frontend.yaml
kubectl apply -f 31-hpa-research.yaml        # 30-ingress.yaml only if you run ingress-nginx

kubectl -n agentic-sdr get pods              # watch it come up
kubectl -n agentic-sdr port-forward svc/frontend 5173:80
```

## Operations quick reference

| Question | Command |
|---|---|
| What's stuck? | `SELECT id, company_name, status, retry_count, updated_at FROM leads WHERE status NOT IN ('CLOSED_LOST','BOOKING_DRAFTED','NO_CONTACT_FOUND','RESEARCH_FAILED','INVALID_EMAIL') ORDER BY updated_at;` |
| Pending timers | `SELECT id, status, next_action_at FROM leads WHERE next_action_at IS NOT NULL;` |
| Unrelayed outbox | `SELECT count(*) FROM outbox WHERE published_at IS NULL;` (should hover near 0) |
| Poison messages | consume `sdr.dlq` — headers carry stage + error |
| Who's suppressed | `SELECT * FROM suppression_list;` |
| Real spend | `GET /api/metrics` → `estimated_cost_usd` (from token logs) |
| Scale research | `kubectl -n agentic-sdr scale deploy worker-research --replicas=4` (≤6) |

## Gmail refresh token (one time)
1. Put `GMAIL_CLIENT_ID`/`GMAIL_CLIENT_SECRET` in `.env`, start the API.
2. `POST /api/gmail/auth` → open `auth_url` in a browser → consent.
3. Callback page returns `refresh_token` → paste into `.env` / k8s secret.
