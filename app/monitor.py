"""
Background session monitor.
Polls Devin for session status until a terminal state is reached,
then updates the DB and posts a GitHub comment.
"""

import asyncio
import logging
from app import db, devin_client, github_client

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 30   # how often to check Devin status
MAX_POLLS = 120              # give up after 60 minutes (120 × 30s)


async def monitor_session(
    session_id: str,
    devin_url: str,
    issue_number: int,
    issue_url: str,
    repo: str,
) -> None:
    """
    Runs as an asyncio background task.
    Polls the Devin session and writes results to SQLite + GitHub.
    """
    logger.info(f"[monitor] Starting poll loop for session {session_id} (issue #{issue_number})")
    db.log_event(session_id, "monitor_started")

    for poll in range(MAX_POLLS):
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

        try:
            status = devin_client.get_session_status(session_id)
            logger.info(
                f"[monitor] session={session_id} poll={poll + 1} "
                f"status_enum={status.status_enum} pr={status.pr_url}"
            )
            db.log_event(
                session_id,
                "poll",
                f"status_enum={status.status_enum} pr_url={status.pr_url}",
            )

            if devin_client.is_terminal(status):
                logger.info(f"[monitor] Session {session_id} reached terminal status: {status.status_enum}")
                _handle_terminal(session_id, devin_url, status, issue_number, repo)
                return

        except Exception as e:
            logger.error(f"[monitor] Error polling session {session_id}: {e}")
            db.log_event(session_id, "poll_error", str(e))
            # Don't abort the loop — transient network errors shouldn't kill the monitor

    # Timeout — hit MAX_POLLS without terminal status
    logger.warning(f"[monitor] Timed out waiting for session {session_id}")
    db.update_session_status(session_id, "timeout", error="Monitor hit MAX_POLLS without terminal status")
    db.log_event(session_id, "timeout")

    try:
        comment = github_client.build_failure_comment(
            session_id,
            devin_url,
            status="timeout",
            error="Session monitor timed out after 60 minutes.",
        )
        github_client.post_issue_comment(repo, issue_number, comment)
    except Exception as e:
        logger.error(f"[monitor] Failed to post timeout comment: {e}")


def _handle_terminal(
    session_id: str,
    devin_url: str,
    status: devin_client.SessionStatus,
    issue_number: int,
    repo: str,
) -> None:
    if status.status_enum == "finished":
        db.update_session_status(session_id, "finished", pr_url=status.pr_url)
        db.log_event(session_id, "finished", f"pr_url={status.pr_url}")
        comment = github_client.build_success_comment(session_id, devin_url, status.pr_url)
    else:
        db.update_session_status(session_id, status.status_enum or "unknown")
        db.log_event(session_id, "terminal", f"status_enum={status.status_enum}")
        comment = github_client.build_failure_comment(
            session_id, devin_url, status=status.status_enum or "unknown", error=None
        )

    try:
        github_client.post_issue_comment(repo, issue_number, comment)
        db.log_event(session_id, "github_comment_posted")
    except Exception as e:
        logger.error(f"[monitor] Failed to post GitHub comment for session {session_id}: {e}")
        db.log_event(session_id, "github_comment_error", str(e))
