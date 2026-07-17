"""
Observability router — drop-in analytics for the automation.

Adds two routes on top of the existing FastAPI app:

    GET /metrics     JSON rollups (counts, success rate, cycle time, throughput)
    GET /dashboard   A self-contained HTML dashboard that renders those metrics

Wire it into app/main.py with two lines:

    from app.observability import router as observability_router
    app.include_router(observability_router)

Nothing else changes — it reads the same SQLite tables the webhook already writes.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from app import metrics

router = APIRouter(tags=["observability"])

_DASHBOARD_HTML = Path(__file__).parent / "dashboard.html"


@router.get("/metrics")
def get_metrics():
    """Aggregate metrics for dashboards, alerts, or a leader's status check."""
    return JSONResponse(metrics.compute_metrics())


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """Human-facing dashboard. Polls /metrics and /sessions client-side."""
    return HTMLResponse(_DASHBOARD_HTML.read_text(encoding="utf-8"))
