"""
Devin API v3 client.
Docs: https://docs.devin.ai/api-reference/overview
"""

import logging
import os
import httpx
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEVIN_API_KEY = os.environ["DEVIN_API_KEY"]   # starts with cog_
DEVIN_ORG_ID = os.environ["DEVIN_ORG_ID"]
BASE_URL = f"https://api.devin.ai/v3/organizations/{DEVIN_ORG_ID}"

HEADERS = {
    "Authorization": f"Bearer {DEVIN_API_KEY}",
    "Content-Type": "application/json",
}

# v3 status values that mean the session has reached a terminal state
# Active states: "running"
TERMINAL_STATUSES = {"finished", "abandoned", "blocked", "expired", "suspended"}


@dataclass
class SessionCreated:
    session_id: str
    url: str


@dataclass
class SessionStatus:
    session_id: str
    status_enum: str | None   # working | blocked | expired | finished | ...
    status: str               # free-text status message from Devin
    pr_url: str | None        # present when Devin opened a PR


def create_session(prompt: str, title: str | None = None, tags: list[str] | None = None) -> SessionCreated:
    """
    Start a new Devin session.
    Returns the session_id and the URL to watch it in the Devin UI.
    """
    payload: dict = {"prompt": prompt}
    if title:
        payload["title"] = title
    if tags:
        payload["tags"] = tags

    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{BASE_URL}/sessions", headers=HEADERS, json=payload)
        resp.raise_for_status()
        data = resp.json()

    return SessionCreated(session_id=data["session_id"], url=data["url"])


def get_session_status(session_id: str) -> SessionStatus:
    """
    Poll the status of an existing Devin session.
    Extracts PR URL from pull_request.url if Devin opened one.
    """
    with httpx.Client(timeout=30) as client:
        resp = client.get(f"{BASE_URL}/sessions/{session_id}", headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()

    # v3: status is a flat string field ("running", "finished", "blocked", etc.)
    status_val = data.get("status", "")
    logger.info(f"[devin] session={session_id} status={status_val}")

    # v3: pull_requests is an array; grab the first URL if present
    prs = data.get("pull_requests") or []
    pr_url = prs[0].get("url") if prs else None

    return SessionStatus(
        session_id=data.get("session_id", session_id),
        status_enum=status_val,   # use status directly as the enum
        status=status_val,
        pr_url=pr_url,
    )


def is_terminal(status: SessionStatus) -> bool:
    return status.status_enum in TERMINAL_STATUSES


def build_superset_prompt(
    issue_number: int,
    issue_title: str,
    issue_body: str,
    issue_url: str,
    repo: str = "abeladia/superset_update",
) -> str:
    """
    Build a Devin prompt that provides full context for the Superset issue.
    """
    return f"""You are working on a fork of Apache Superset: https://github.com/{repo}

## Your Task
Fix or implement the changes described in GitHub Issue #{issue_number}:

**Title:** {issue_title}

**Issue URL:** {issue_url}

**Issue Description:**
{issue_body or "(No description provided)"}

## Instructions
1. Clone or open the repository at https://github.com/{repo}
2. Understand the issue fully before making any changes
3. Implement the fix or improvement described in the issue
4. Ensure your changes do not break existing tests
5. Run any relevant tests to validate your work
6. Open a Pull Request against the `main` branch of {repo} with:
   - A clear title referencing issue #{issue_number}
   - A description summarizing what you changed and why
   - Reference to the issue: "Closes #{issue_number}"

Keep changes focused and minimal — only touch what the issue requires.
"""
