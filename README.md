# Agentic SDR

An AI sales development rep. Give it a CSV of companies and it researches each one, finds a decision-maker, writes a personalized cold email, sends it through Gmail, follows up after 72 hours, reads the reply, and drafts a meeting-booking email if the prospect is interested. Anything it isn't confident about goes to a human review queue.

**Tech stack:** Python, FastAPI, Apache Kafka, PostgreSQL, React, Docker, Kubernetes, Anthropic / Gemini, Firecrawl, Serper, Hunter, Apollo, Gmail API

## How it works

The pipeline has five stages, each running as its own worker:

1. **Research** – scrapes the company website and recent news, then extracts a summary, industry, size, pain points, and a confidence score.
2. **Contact** – finds a decision-maker through Hunter and Apollo. Only verified emails are used. Skipped if the CSV already includes a contact.
3. **Draft** – writes a short, personalized email using the research and the seller's profile (what they sell, to whom, and in what tone).
4. **Send** – runs compliance checks, sends through Gmail, and schedules a follow-up for 72 hours later.
5. **Classify** – reads the reply, decides the prospect's intent, and drafts a booking email if they're interested.

A React dashboard shows each lead's progress live, and lets a person approve, edit, close, or retry leads in the review queue.

## Design

- **Kafka for moving work, Postgres for waiting work.** Research, contact, and draft run back-to-back, passing the lead's data along in Kafka messages using exactly-once transactions. Once a lead stops (draft ready, failed, or needs review) it's saved to Postgres, which is the source of truth for sending, waiting, replies, and human review.
- **Safe status changes.** A lead's status is only updated if it's still in the expected state (`UPDATE ... WHERE status = <expected>`). If two workers, a retry, or a person act on the same lead at once, only one succeeds.
- **Transactional outbox.** A status change and the message it triggers are saved in one database transaction and published to Kafka afterwards, so the database and Kafka never disagree.
- **No duplicate processing.** Each message's ID is recorded when it's handled, so a message delivered twice is skipped.
- **No double emails.** A worker must claim a lead before sending. Gmail can't detect duplicate sends, so this lock is what prevents them.
- **Follow-ups are a timestamp.** Each lead has a `next_action_at` column that the scheduler checks, so there are no sleeping processes and restarts are safe.
- **Bad messages don't block the pipeline.** A message that can't be processed goes to a dead-letter queue.

More detail is in [ARCHITECTURE.md](agentic-sdr/ARCHITECTURE.md).

## Guardrails

Compliance rules are written in code and run before the AI is involved.

- Opt-out replies (STOP, unsubscribe) put the address on a permanent do-not-contact list and close the lead.
- Replies asking "are you an AI?" or mentioning sensitive topics go to human review.
- Before every send: the address is validated, checked against the do-not-contact list, and blocked if it was emailed in the last 7 days.
- Every draft must be under 200 words, include an opt-out line, use a real fact about the prospect, and avoid pricing. A draft that fails is rewritten once, then sent to human review.
- Confidence thresholds: research needs 0.65 or higher to continue; an interested reply needs 0.80 to auto-draft a booking email; a not-interested reply needs 0.85 to auto-close; anything else goes to human review.
- Booking emails are drafted automatically but only sent when a person clicks send.

## Logging and cost tracking

Every AI call logs the prompt version, model, tokens used, latency, and confidence. The dashboard's cost figure is calculated from these real token counts. Prompts are versioned so any output can be traced back to the prompt that produced it.

## Running it

Requires Docker Desktop.

```bash
cd agentic-sdr
cp .env.example .env        # add your API keys
cd deploy
docker compose --env-file ../.env up --build -d
```

Open http://localhost:5173, fill in the Profile tab, and upload a CSV with a `company_name` column (optionally `website`, `contact_email`, `contact_name`, `contact_role`). See `agentic-sdr/demo_leads.csv` for an example.

- `APP_PROFILE=dev` runs without API keys; `APP_PROFILE=prod` won't start unless all keys are set.
- `LLM_PROVIDER` can be `anthropic` or `gemini`.
- Kubernetes manifests are in `agentic-sdr/deploy/k8s/`.

## Tests

```bash
cd agentic-sdr/backend
python -m unittest discover tests -v
```

42 unit tests covering status changes, compliance rules, message formats, and parsing of AI output and email replies.

## Project structure

```
agentic-sdr/
  backend/
    app/
      api/            REST API and live updates
      stages/         research, contact, draft, send, classify
      workers/        shared worker runtime
      integrations/   Firecrawl, Serper, Hunter, Apollo, Gmail
      scheduler.py    follow-up timers, Gmail polling, bounce checks
      transitions.py  allowed status changes
      repository.py   database queries
      llm.py          AI provider wrapper
      prompts.py      versioned prompts
    migrations/       database schema
    tests/
  frontend/           React dashboard
  deploy/             Docker Compose and Kubernetes files
```
