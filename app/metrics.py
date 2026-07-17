"""
Aggregate metrics for the observability layer.

Reads the same SQLite tables written by app/db.py and rolls them up into the
numbers an engineering leader actually asks about:

    - How many issues have we dispatched to Devin?
    - How many are in flight right now, and what phase are they in?
    - What share of completed sessions actually produced a PR? (success rate)
    - How long does Devin take to get to a PR? (cycle time)
    - What's our throughput day over day?

Everything here is derived from `sessions` + `events`, so there is no second
source of truth to keep in sync. Pure read path — safe to call on every request.
"""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean, median

from app import db

# A session in one of these states is finished for good.
FAILED_STATUSES = {"expired", "blocked", "timeout", "error"}
SUCCESS_STATUS = "finished"


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def classify(session: dict) -> str:
    """Bucket a session into one of: success | finished_no_pr | failed | active."""
    status = (session.get("status") or "").lower()
    if status == SUCCESS_STATUS:
        return "success" if session.get("pr_url") else "finished_no_pr"
    if status in FAILED_STATUSES:
        return "failed"
    return "active"


def _live_phase(session_id: str) -> str | None:
    """
    Infer the current phase of an in-flight session from its most recent poll
    event. The sessions table only flips on terminal states, so for active work
    the freshest signal lives in the event log (payload like
    'status_enum=working pr_url=None').
    """
    for event in reversed(db.get_events(session_id)):
        if event.get("event_type") == "poll" and event.get("payload"):
            for token in event["payload"].split():
                if token.startswith("status_enum="):
                    value = token.split("=", 1)[1]
                    return None if value in ("None", "") else value
    return None


def _duration_minutes(session: dict) -> float | None:
    """Wall-clock minutes from dispatch to last status change (terminal only)."""
    start = _parse(session.get("created_at"))
    end = _parse(session.get("updated_at"))
    if not start or not end or end <= start:
        return None
    return round((end - start).total_seconds() / 60.0, 1)


def compute_metrics() -> dict:
    sessions = db.get_all_sessions()

    by_bucket = {"success": 0, "finished_no_pr": 0, "failed": 0, "active": 0}
    by_status: dict[str, int] = {}
    durations: list[float] = []
    throughput: dict[str, dict[str, int]] = {}
    enriched: list[dict] = []

    for s in sessions:
        bucket = classify(s)
        by_bucket[bucket] += 1

        status = (s.get("status") or "unknown").lower()
        by_status[status] = by_status.get(status, 0) + 1

        duration = _duration_minutes(s) if bucket != "active" else None
        if bucket == "success" and duration is not None:
            durations.append(duration)

        # Throughput by calendar day: dispatched on created_at, resolved on updated_at.
        created = _parse(s.get("created_at"))
        if created:
            day = created.date().isoformat()
            throughput.setdefault(day, {"dispatched": 0, "completed": 0})
            throughput[day]["dispatched"] += 1
        if bucket != "active":
            resolved = _parse(s.get("updated_at"))
            if resolved:
                day = resolved.date().isoformat()
                throughput.setdefault(day, {"dispatched": 0, "completed": 0})
                throughput[day]["completed"] += 1

        enriched.append(
            {
                "session_id": s.get("session_id"),
                "issue_number": s.get("issue_number"),
                "issue_title": s.get("issue_title"),
                "issue_url": s.get("issue_url"),
                "repo": s.get("repo"),
                "status": status,
                "bucket": bucket,
                "phase": _live_phase(s["session_id"]) if bucket == "active" else None,
                "pr_url": s.get("pr_url"),
                "devin_url": s.get("devin_url"),
                "duration_minutes": duration,
                "created_at": s.get("created_at"),
                "updated_at": s.get("updated_at"),
            }
        )

    total = len(sessions)
    completed = by_bucket["success"] + by_bucket["finished_no_pr"] + by_bucket["failed"]
    active = by_bucket["active"]

    # Success rate = sessions that produced a PR, over everything that finished.
    success_rate = (by_bucket["success"] / completed) if completed else None
    pr_rate = (by_bucket["success"] / total) if total else None

    duration_stats = None
    if durations:
        ordered = sorted(durations)
        p90_index = max(0, min(len(ordered) - 1, round(0.9 * (len(ordered) - 1))))
        duration_stats = {
            "count": len(durations),
            "avg_minutes": round(mean(durations), 1),
            "median_minutes": round(median(durations), 1),
            "p90_minutes": ordered[p90_index],
            "fastest_minutes": ordered[0],
            "slowest_minutes": ordered[-1],
        }

    throughput_series = [
        {"date": day, **counts} for day, counts in sorted(throughput.items())
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "totals": {
            "total": total,
            "active": active,
            "completed": completed,
            "success": by_bucket["success"],
            "finished_no_pr": by_bucket["finished_no_pr"],
            "failed": by_bucket["failed"],
        },
        "by_status": by_status,
        "by_bucket": by_bucket,
        "success_rate": success_rate,
        "pr_rate": pr_rate,
        "time_to_pr": duration_stats,
        "throughput": throughput_series,
        "sessions": enriched,
    }
