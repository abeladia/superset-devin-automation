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


def is_pr_merged(pr_url: str) -> bool:
    """
    Check whether a GitHub PR has been merged.
    pr_url format: https://github.com/owner/repo/pull/N
    """
    try:
        # Convert HTML URL to API URL
        # https://github.com/owner/repo/pull/N → /repos/owner/repo/pulls/N
        parts = pr_url.rstrip("/").split("/")
        owner, repo_name, _, pr_number = parts[-4], parts[-3], parts[-2], parts[-1]
        api_url = f"{BASE_URL}/repos/{owner}/{repo_name}/pulls/{pr_number}"
        with httpx.Client(timeout=15) as client:
            resp = client.get(api_url, headers=HEADERS)
            if resp.status_code != 200:
                return False
            return resp.json().get("merged", False)
    except Exception:
        return False


def find_pr_for_issue(repo: str, issue_number: int) -> str | None:
    """
    Find a PR linked to this issue via the GitHub Issues timeline API.
    Looks for 'cross-referenced' events where the source is a pull request.
    More reliable than search since it uses GitHub's own issue linkage tracking.
    """
    headers = {**HEADERS, "Accept": "application/vnd.github.mockingbird-preview+json"}
    url = f"{BASE_URL}/repos/{repo}/issues/{issue_number}/timeline"
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, headers=headers, params={"per_page": 100})
            if resp.status_code != 200:
                return None
            events = resp.json()

        # Walk timeline events looking for cross-referenced PRs
        for event in reversed(events):  # most recent first
            if event.get("event") == "cross-referenced":
                source = event.get("source", {})
                issue = source.get("issue", {})
                if issue.get("pull_request"):
                    return issue.get("html_url")
    except Exception:
        pass
    return None


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
