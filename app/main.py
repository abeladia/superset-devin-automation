"""
FastAPI webhook server.

Listens for GitHub `issues` webhook events.
When an issue is labeled with "devin-fix", dispatches a Devin session,
logs it to SQLite, and starts a background monitor that posts a GitHub
comment when Devin finishes.
"""

import asyncio
import hmac
import hashlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

from app import db, devin_client, github_client, monitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
TRIGGER_LABEL = os.environ.get("TRIGGER_LABEL", "devin-fix")
TARGET_REPO = os.environ.get("TARGET_REPO", "abeladia/superset_update")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    logger.info("Database initialised")
    logger.info(f"Listening for label: '{TRIGGER_LABEL}' on repo: {TARGET_REPO}")
    yield


app = FastAPI(
    title="Superset Devin Automation",
    description="Event-driven webhook that dispatches Devin sessions for labeled GitHub issues",
    version="1.0.0",
    lifespan=lifespan,
)


def _verify_signature(body: bytes, signature_header: str | None) -> None:
    """Verify the GitHub webhook HMAC-SHA256 signature."""
    if not WEBHOOK_SECRET:
        return  # skip verification if no secret configured (dev mode)
    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


@app.post("/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receives GitHub webhook events.
    Only acts on `issues` events with action `labeled` and label == TRIGGER_LABEL.
    """
    body = await request.body()
    _verify_signature(body, request.headers.get("X-Hub-Signature-256"))

    event_type = request.headers.get("X-GitHub-Event", "")
    payload = await request.json()

    # Only handle issue label events
    if event_type != "issues":
        return JSONResponse({"status": "ignored", "reason": f"event_type={event_type}"})

    action = payload.get("action")
    if action != "labeled":
        return JSONResponse({"status": "ignored", "reason": f"action={action}"})

    label_name = payload.get("label", {}).get("name", "")
    if label_name != TRIGGER_LABEL:
        return JSONResponse({"status": "ignored", "reason": f"label={label_name}"})

    issue = payload.get("issue", {})
    issue_number = issue.get("number")
    issue_title = issue.get("title", "")
    issue_body = issue.get("body", "")
    issue_url = issue.get("html_url", "")
    repo = payload.get("repository", {}).get("full_name", TARGET_REPO)

    logger.info(f"Triggering Devin for issue #{issue_number}: {issue_title}")

    # Build Devin prompt and create session
    try:
        prompt = devin_client.build_superset_prompt(
            issue_number=issue_number,
            issue_title=issue_title,
            issue_body=issue_body,
            issue_url=issue_url,
            repo=repo,
        )
        session = devin_client.create_session(
            prompt=prompt,
            title=f"[Superset #{issue_number}] {issue_title[:80]}",
            tags=["superset", "auto-dispatch", TRIGGER_LABEL],
        )
    except Exception as e:
        logger.error(f"Failed to create Devin session for issue #{issue_number}: {e}")
        raise HTTPException(status_code=500, detail=f"Devin session creation failed: {e}")

    # Persist to SQLite
    db.insert_session(
        session_id=session.session_id,
        devin_url=session.url,
        issue_number=issue_number,
        issue_url=issue_url,
        issue_title=issue_title,
        repo=repo,
    )
    db.log_event(session.session_id, "dispatched", f"issue=#{issue_number} label={label_name}")

    logger.info(f"Devin session {session.session_id} created → {session.url}")

    # Start background monitor (non-blocking)
    background_tasks.add_task(
        monitor.monitor_session,
        session_id=session.session_id,
        devin_url=session.url,
        issue_number=issue_number,
        issue_url=issue_url,
        repo=repo,
    )

    return JSONResponse(
        {
            "status": "dispatched",
            "session_id": session.session_id,
            "devin_url": session.url,
            "issue_number": issue_number,
        },
        status_code=201,
    )


# ── Observability endpoints ──────────────────────────────────────────────────

@app.get("/sessions")
def list_sessions():
    """Return all tracked Devin sessions with their current status."""
    return db.get_all_sessions()


@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    """Return a single session and all its events."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    events = db.get_events(session_id)
    return {"session": session, "events": events}


@app.get("/health")
def health():
    return {"status": "ok"}
