"""
Devin API v1 client.
Docs: https://docs.devin.ai/api-reference/v1/overview
"""

import os
import httpx
from dataclasses import dataclass

DEVIN_API_KEY = os.environ["DEVIN_API_KEY"]
BASE_URL = "https://api.devin.ai/v1"

HEADERS = {
    "Authorization": f"Bearer {DEVIN_API_KEY}",
    "Content-Type": "application/json",
}

# Status values that mean the session has reached a terminal state
TERMINAL_STATUSES = {"finished", "expired", "blocked"}


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

    pr_info = data.get("pull_request")
    pr_url = pr_info["url"] if pr_info else None

    return SessionStatus(
        session_id=data["session_id"],
        status_enum=data.get("status_enum"),
        status=data.get("status", ""),
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
