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
from fastapi.responses import JSONResponse, HTMLResponse

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

    # Recovery: re-attach monitors to any sessions that were active before restart
    orphaned = db.get_active_sessions()
    if orphaned:
        logger.info(f"[recovery] Found {len(orphaned)} orphaned session(s) — restarting monitors")
        for s in orphaned:
            logger.info(f"[recovery] Resuming monitor for session {s['session_id']} (issue #{s['issue_number']})")
            asyncio.ensure_future(
                monitor.monitor_session(
                    session_id=s["session_id"],
                    devin_url=s["devin_url"],
                    issue_number=s["issue_number"],
                    issue_url=s["issue_url"],
                    repo=s["repo"],
                )
            )

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


@app.get("/metrics")
def metrics():
    """
    Aggregate observability metrics.
    Answers: is this system working, and how well?
    """
    return db.get_metrics()


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """Human-readable HTML dashboard showing system health at a glance."""
    m = db.get_metrics()
    sessions = db.get_all_sessions()

    status_badge = {
        "dispatched": ("#6366f1", "Dispatched"),
        "running":    ("#f59e0b", "Running"),
        "working":    ("#f59e0b", "Working"),
        "finished":   ("#10b981", "Finished"),
        "blocked":    ("#ef4444", "Blocked"),
        "abandoned":  ("#ef4444", "Abandoned"),
        "expired":    ("#ef4444", "Expired"),
        "suspended":  ("#f59e0b", "Suspended"),
        "timeout":    ("#ef4444", "Timeout"),
        "unknown":    ("#6b7280", "Unknown"),
    }

    rows = ""
    for s in sessions:
        color, label = status_badge.get(s["status"], ("#6b7280", s["status"]))
        pr_cell = f'<a href="{s["pr_url"]}" target="_blank">View PR →</a>' if s.get("pr_url") else "—"
        devin_cell = f'<a href="{s["devin_url"]}" target="_blank">Session →</a>' if s.get("devin_url") else "—"
        rows += f"""
        <tr>
          <td>#{s['issue_number']}</td>
          <td class="title">{s.get('issue_title','')}</td>
          <td><span class="badge" style="background:{color}">{label}</span></td>
          <td>{devin_cell}</td>
          <td>{pr_cell}</td>
          <td class="ts">{s['created_at'][:16].replace('T',' ')}</td>
        </tr>"""

    activity_rows = ""
    for e in m["recent_activity"]:
        activity_rows += f"""
        <tr>
          <td class="ts">{e['created_at'][11:19]}</td>
          <td>#{e['issue_number']}</td>
          <td><code>{e['event_type']}</code></td>
          <td class="title">{e.get('payload','') or ''}</td>
        </tr>"""

    avg_str = f"{m['avg_completion_minutes']} min" if m["avg_completion_minutes"] else "—"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="30">
  <title>Devin Automation Dashboard</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0f172a; color: #e2e8f0; padding: 32px; }}
    h1 {{ font-size: 1.5rem; font-weight: 700; margin-bottom: 4px; }}
    .sub {{ color: #94a3b8; font-size: 0.85rem; margin-bottom: 32px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
              gap: 16px; margin-bottom: 36px; }}
    .card {{ background: #1e293b; border-radius: 12px; padding: 20px; }}
    .card .num {{ font-size: 2.2rem; font-weight: 800; line-height: 1; }}
    .card .lbl {{ color: #94a3b8; font-size: 0.8rem; margin-top: 6px; text-transform: uppercase; letter-spacing: .05em; }}
    .green {{ color: #10b981; }}
    .yellow {{ color: #f59e0b; }}
    .red {{ color: #ef4444; }}
    .purple {{ color: #a78bfa; }}
    h2 {{ font-size: 1rem; font-weight: 600; margin-bottom: 12px; color: #cbd5e1; }}
    table {{ width: 100%; border-collapse: collapse; background: #1e293b;
             border-radius: 12px; overflow: hidden; margin-bottom: 36px; }}
    th {{ text-align: left; padding: 12px 16px; font-size: 0.75rem;
          text-transform: uppercase; letter-spacing: .05em; color: #64748b;
          border-bottom: 1px solid #334155; }}
    td {{ padding: 12px 16px; border-bottom: 1px solid #1e293b; font-size: 0.875rem; vertical-align: middle; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: #263348; }}
    .badge {{ display: inline-block; padding: 2px 10px; border-radius: 99px;
              font-size: 0.75rem; font-weight: 600; color: #fff; }}
    .title {{ max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .ts {{ color: #64748b; font-size: 0.78rem; white-space: nowrap; }}
    a {{ color: #60a5fa; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    code {{ background: #334155; padding: 2px 6px; border-radius: 4px; font-size: 0.8rem; }}
    .footer {{ color: #475569; font-size: 0.75rem; margin-top: 8px; }}
  </style>
</head>
<body>
  <h1>Devin Automation — Superset</h1>
  <p class="sub">Auto-refreshes every 30 seconds &nbsp;·&nbsp; Repo: {TARGET_REPO}</p>

  <div class="cards">
    <div class="card">
      <div class="num purple">{m['total_dispatched']}</div>
      <div class="lbl">Total Dispatched</div>
    </div>
    <div class="card">
      <div class="num yellow">{m['active']}</div>
      <div class="lbl">Active</div>
    </div>
    <div class="card">
      <div class="num green">{m['finished']}</div>
      <div class="lbl">Finished</div>
    </div>
    <div class="card">
      <div class="num red">{m['failed']}</div>
      <div class="lbl">Failed / Blocked</div>
    </div>
    <div class="card">
      <div class="num green">{m['success_rate_pct']}%</div>
      <div class="lbl">Success Rate</div>
    </div>
    <div class="card">
      <div class="num green">{m['pr_rate_pct']}%</div>
      <div class="lbl">PR Rate</div>
    </div>
    <div class="card">
      <div class="num purple">{avg_str}</div>
      <div class="lbl">Avg Completion</div>
    </div>
  </div>

  <h2>Sessions</h2>
  <table>
    <thead>
      <tr>
        <th>Issue</th><th>Title</th><th>Status</th><th>Devin</th><th>PR</th><th>Started</th>
      </tr>
    </thead>
    <tbody>{rows if rows else '<tr><td colspan="6" style="color:#64748b;text-align:center;padding:32px">No sessions yet — trigger an issue label event to get started.</td></tr>'}</tbody>
  </table>

  <h2>Recent Activity</h2>
  <table>
    <thead>
      <tr><th>Time</th><th>Issue</th><th>Event</th><th>Detail</th></tr>
    </thead>
    <tbody>{activity_rows if activity_rows else '<tr><td colspan="4" style="color:#64748b;text-align:center;padding:32px">No activity yet.</td></tr>'}</tbody>
  </table>

  <p class="footer">Raw data: <a href="/metrics">/metrics</a> &nbsp;·&nbsp; <a href="/sessions">/sessions</a> &nbsp;·&nbsp; <a href="/health">/health</a></p>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/health")
def health():
    return {"status": "ok"}
