"""API service. Every mutation is a short transaction: guarded transition +
outbox command. No route ever calls Claude, Gmail, or a scraper — heavy work
belongs to workers, so the API stays responsive no matter what the pipeline is
doing. DB routes are sync `def` (FastAPI runs them on the threadpool);
the event loop only handles SSE and health checks."""
import asyncio
import csv
import io
import json
import uuid
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app import events, repository
from app.api import seed
from app.api.sse import broadcast, consume_lead_events
from app.config import settings
from app.db import get_pool, ping, run_migrations
from app.logging_setup import get_logger, setup_logging
from app.transitions import TransitionConflict, TransitionError

log = get_logger(component="api")

app = FastAPI(title="Agentic SDR", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/metrics", make_asgi_app())

_stop_sse = asyncio.Event()
_sse_task: Optional[asyncio.Task] = None


@app.on_event("startup")
async def startup() -> None:
    global _sse_task
    setup_logging()
    settings.validate_profile()
    run_migrations()
    if settings.SEED_DEMO_DATA:
        with get_pool().connection() as conn:
            with conn.transaction():
                n = seed.seed_if_empty(conn)
        if n:
            log.info("seeded_demo_data", rows=n)
    _sse_task = asyncio.create_task(consume_lead_events(_stop_sse))
    log.info("api_started", profile=settings.APP_PROFILE)


@app.on_event("shutdown")
async def shutdown() -> None:
    _stop_sse.set()
    if _sse_task:
        await _sse_task


# ─── leads ─────────────────────────────────────────────────────────────────────

@app.post("/api/leads/upload")
def upload_leads(file: UploadFile = File(...)):
    try:
        text = file.file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "CSV must be UTF-8 encoded")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "company_name" not in [f.strip() for f in reader.fieldnames]:
        raise HTTPException(400, "CSV must have a company_name column")

    batch_id = str(uuid.uuid4())
    created, skipped = [], []
    with get_pool().connection() as conn:
        profile = repository.get_seller_profile(conn)
        if profile is None:
            raise HTTPException(
                409,
                "Set your seller profile first (Profile tab): the pipeline needs to "
                "know what you sell before it can research fit or write outreach.",
            )
        seller = repository.seller_context_block(profile)
        for i, row in enumerate(reader, start=2):
            company = (row.get("company_name") or "").strip()
            if not company:
                skipped.append({"row": i, "reason": "empty company_name"})
                continue
            website = (row.get("website") or "").strip() or None
            with conn.transaction():
                lead = repository.create_lead(conn, company, website, batch_id)
                if lead is None:
                    skipped.append({"row": i, "reason": f"duplicate in batch: {company}"})
                    continue
                lead_id = str(lead["id"])
                repository.cas_transition(
                    conn, lead_id, "UPLOADED", "RESEARCH_PENDING", by="api",
                )
                # zone-1 commands are event-carried: seed + seller context ride along
                cmd = events.make_message(
                    events.CMD_RESEARCH_LEAD, lead_id,
                    {"company_name": company, "website": website, "seller": seller},
                )
                repository.outbox_add(conn, events.CMD_TOPICS["research"], lead_id, cmd)
                created.append(lead_id)
    return {"batch_id": batch_id, "created": len(created), "skipped": skipped,
            "lead_ids": created}


@app.get("/api/leads")
def list_leads(status: Optional[str] = None):
    with get_pool().connection() as conn:
        leads = repository.all_leads(conn, status)
    return {"leads": leads, "count": len(leads)}


@app.get("/api/leads/{lead_id}")
def get_lead(lead_id: str):
    with get_pool().connection() as conn:
        lead = repository.get_lead(conn, lead_id)
    if lead is None:
        raise HTTPException(404, "Lead not found")
    return lead


class HumanAction(BaseModel):
    action: str                       # approve | edit | skip | close | retry_research
    edited_draft: Optional[str] = None


@app.patch("/api/leads/{lead_id}/human-action")
def human_action(lead_id: str, body: HumanAction):
    with get_pool().connection() as conn:
        lead = repository.get_lead(conn, lead_id)
        if lead is None:
            raise HTTPException(404, "Lead not found")
        from_status = lead["status"]
        try:
            with conn.transaction():
                if body.action in ("approve", "edit"):
                    fields = {"human_approval_required": False, "review_reason": None}
                    if body.action == "edit":
                        if not body.edited_draft:
                            raise HTTPException(400, "edited_draft required for edit")
                        lines = body.edited_draft.strip().splitlines()
                        fields["subject_line"] = lines[0] if lines else lead.get("subject_line")
                        fields["email_body"] = "\n".join(lines[1:]).strip() or body.edited_draft
                        fields["word_count"] = len((fields["email_body"] or "").split())
                    repository.cas_transition(
                        conn, lead_id, from_status, "DRAFT_READY", by="human", **fields,
                    )
                    cmd = events.make_message(
                        events.CMD_SEND_EMAIL, lead_id, {"kind": "initial"}
                    )
                    repository.outbox_add(conn, events.CMD_TOPICS["send"], lead_id, cmd)
                elif body.action in ("skip", "close"):
                    repository.cas_transition(
                        conn, lead_id, from_status, "CLOSED_LOST", by="human",
                        review_reason=None,
                    )
                elif body.action == "retry_research":
                    repository.cas_transition(
                        conn, lead_id, from_status, "RESEARCH_PENDING", by="human",
                        review_reason=None, human_approval_required=False,
                    )
                    prof = repository.get_seller_profile(conn)
                    cmd = events.make_message(
                        events.CMD_RESEARCH_LEAD, lead_id,
                        {"company_name": lead["company_name"], "website": lead.get("website"),
                         "seller": repository.seller_context_block(prof) if prof else {}},
                    )
                    repository.outbox_add(conn, events.CMD_TOPICS["research"], lead_id, cmd)
                else:
                    raise HTTPException(400, f"Unknown action: {body.action}")
                repository.log_agent_action(
                    conn, lead_id, "human", body.action, "success",
                    status_before=from_status,
                )
        except TransitionConflict:
            raise HTTPException(409, f"Lead moved from {from_status} concurrently; refresh")
        except TransitionError as e:
            raise HTTPException(422, str(e))
    return {"status": "ok", "action": body.action, "lead_id": lead_id}


# ─── seller profile ────────────────────────────────────────────────────────────

class SellerProfile(BaseModel):
    company_name: str
    product_description: str
    value_proposition: str
    sender_name: str
    target_customer: Optional[str] = None
    sender_title: Optional[str] = None
    meeting_link: Optional[str] = None
    tone: Optional[str] = None


@app.get("/api/profile")
def get_profile():
    with get_pool().connection() as conn:
        profile = repository.get_seller_profile(conn)
    return profile or {}


@app.put("/api/profile")
def put_profile(body: SellerProfile):
    with get_pool().connection() as conn:
        with conn.transaction():
            saved = repository.upsert_seller_profile(conn, **body.model_dump())
    return saved


# ─── observability ─────────────────────────────────────────────────────────────

@app.get("/api/metrics")
def api_metrics():
    with get_pool().connection() as conn:
        return repository.metrics(conn)


@app.get("/api/logs")
def api_logs(limit: int = 100):
    with get_pool().connection() as conn:
        return {"logs": repository.recent_logs(conn, min(limit, 500))}


@app.get("/api/events")
async def sse_events():
    """Live lead-transition stream for the dashboard."""
    queue = broadcast.subscribe()

    async def gen():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {"event": "lead", "data": json.dumps(item, default=str)}
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}
        finally:
            broadcast.unsubscribe(queue)

    return EventSourceResponse(gen())


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/readyz")
async def readyz():
    if not ping():
        raise HTTPException(503, "database unreachable")
    return {"ok": True}


# ─── gmail oauth bootstrap (one-time setup helper) ─────────────────────────────

def _oauth_flow():
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.GMAIL_CLIENT_ID,
                "client_secret": settings.GMAIL_CLIENT_SECRET,
                "redirect_uris": ["http://localhost:8000/api/gmail/callback"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )
    flow.redirect_uri = "http://localhost:8000/api/gmail/callback"
    return flow


# PKCE verifiers survive between /auth and /callback (keyed by state). Fine for
# a single API instance; a multi-replica deployment would move this to the DB.
_pkce_verifiers: dict = {}


@app.post("/api/gmail/auth")
def gmail_auth():
    if not settings.GMAIL_CLIENT_ID:
        raise HTTPException(400, "GMAIL_CLIENT_ID not configured")
    flow = _oauth_flow()
    auth_url, state = flow.authorization_url(prompt="consent", access_type="offline")
    _pkce_verifiers[state] = flow.code_verifier
    if len(_pkce_verifiers) > 20:
        _pkce_verifiers.pop(next(iter(_pkce_verifiers)))
    return {"auth_url": auth_url}


@app.get("/api/gmail/callback")
def gmail_callback(code: str, state: str = ""):
    flow = _oauth_flow()
    verifier = _pkce_verifiers.pop(state, None)
    if verifier:
        flow.code_verifier = verifier
    flow.fetch_token(code=code)
    return {
        "refresh_token": flow.credentials.refresh_token,
        "message": "Copy this into GMAIL_REFRESH_TOKEN in your environment/secret.",
    }


@app.get("/")
def root():
    return {"service": "Agentic SDR API", "version": "2.0.0", "docs": "/docs"}
