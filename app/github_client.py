"""
GitHub REST API client.
Used to post comments back to issues after Devin finishes.
"""

import os
import httpx

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
BASE_URL = "https://api.github.com"

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def post_issue_comment(repo: str, issue_number: int, body: str) -> None:
    """
    Post a comment on a GitHub issue.
    repo format: "owner/repo"
    """
    url = f"{BASE_URL}/repos/{repo}/issues/{issue_number}/comments"
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, headers=HEADERS, json={"body": body})
        resp.raise_for_status()


def build_success_comment(session_id: str, devin_url: str, pr_url: str | None) -> str:
    pr_line = f"\n**Pull Request:** {pr_url}" if pr_url else "\n*(PR not yet detected — check Devin session for output)*"
    return f"""### Devin session complete ✅

Devin finished working on this issue.
{pr_line}

**Devin session:** {devin_url}
"""


def build_failure_comment(session_id: str, devin_url: str, status: str, error: str | None) -> str:
    error_line = f"\n**Error:** {error}" if error else ""
    return f"""### Devin session ended with status: `{status}` ⚠️

Devin was unable to complete this issue automatically.
{error_line}

**Devin session:** {devin_url}

A human engineer should review the session for context on what blocked Devin.
"""
