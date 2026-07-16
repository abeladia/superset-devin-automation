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
