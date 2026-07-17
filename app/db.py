"""
SQLite observability layer.
Tracks every Devin session dispatched from a GitHub webhook event.
"""

import sqlite3
import os
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = os.environ.get("DB_PATH", "/data/sessions.db")


def _conn() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      TEXT UNIQUE NOT NULL,
                devin_url       TEXT,
                issue_number    INTEGER NOT NULL,
                issue_url       TEXT,
                issue_title     TEXT,
                repo            TEXT,
                status          TEXT NOT NULL DEFAULT 'dispatched',
                pr_url          TEXT,
                error           TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                event_type  TEXT NOT NULL,
                payload     TEXT,
                created_at  TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_session(
    session_id: str,
    devin_url: str,
    issue_number: int,
    issue_url: str,
    issue_title: str,
    repo: str,
) -> None:
    now = _now()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO sessions
                (session_id, devin_url, issue_number, issue_url, issue_title, repo, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'dispatched', ?, ?)
            """,
            (session_id, devin_url, issue_number, issue_url, issue_title, repo, now, now),
        )
        conn.commit()


def update_session_status(
    session_id: str,
    status: str,
    pr_url: str | None = None,
    error: str | None = None,
) -> None:
    now = _now()
    with _conn() as conn:
        conn.execute(
            """
            UPDATE sessions
            SET status = ?, pr_url = ?, error = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (status, pr_url, error, now, session_id),
        )
        conn.commit()


def log_event(session_id: str, event_type: str, payload: str | None = None) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO events (session_id, event_type, payload, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, event_type, payload, _now()),
        )
        conn.commit()


def get_active_sessions() -> list[dict]:
    """Return sessions that are not yet in a terminal state — used for startup recovery."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM sessions
            WHERE status NOT IN ('finished', 'abandoned', 'blocked', 'expired', 'suspended', 'timeout', 'unknown')
            ORDER BY created_at
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_sessions() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_session(session_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def get_events(session_id: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_metrics() -> dict:
    """
    Aggregate metrics for the observability dashboard and /metrics endpoint.
    Answers: is this system working, and how well?
    """
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        by_status = conn.execute(
            "SELECT status, COUNT(*) as count FROM sessions GROUP BY status"
        ).fetchall()
        status_counts = {row["status"]: row["count"] for row in by_status}

        finished = status_counts.get("finished", 0)
        failed = sum(
            status_counts.get(s, 0) for s in ("blocked", "abandoned", "expired", "suspended", "timeout", "unknown")
        )
        active = sum(
            status_counts.get(s, 0) for s in ("dispatched", "running", "working")
        )

        # Average duration for completed sessions (created_at → updated_at)
        avg_row = conn.execute(
            """
            SELECT AVG(
                (julianday(updated_at) - julianday(created_at)) * 24 * 60
            ) as avg_minutes
            FROM sessions
            WHERE status = 'finished'
            """
        ).fetchone()
        avg_minutes = round(avg_row["avg_minutes"], 1) if avg_row["avg_minutes"] else None

        # PR rate — of finished sessions, how many produced a PR?
        pr_count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE pr_url IS NOT NULL AND pr_url != ''"
        ).fetchone()[0]

        # Last 5 events across all sessions (activity feed)
        recent_events = conn.execute(
            """
            SELECT e.event_type, e.payload, e.created_at, s.issue_number, s.issue_title
            FROM events e
            JOIN sessions s ON s.session_id = e.session_id
            ORDER BY e.created_at DESC
            LIMIT 10
            """
        ).fetchall()

    success_rate = round((finished / total * 100), 1) if total > 0 else 0
    pr_rate = round((pr_count / finished * 100), 1) if finished > 0 else 0

    return {
        "total_dispatched": total,
        "active": active,
        "finished": finished,
        "failed": failed,
        "success_rate_pct": success_rate,
        "pr_rate_pct": pr_rate,
        "avg_completion_minutes": avg_minutes,
        "status_breakdown": status_counts,
        "recent_activity": [dict(r) for r in recent_events],
    }
