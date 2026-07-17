"""
GitHub REST API client.
Used to post comments back to issues after Devin finishes.
"""

import logging
import os
import httpx

logger = logging.getLogger(__name__)

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
    Find a PR linked to this issue.
    Strategy 1: issue timeline cross-reference events (works when PR body says "Closes #N")
    Strategy 2: scan recent merged PRs in the repo for any that mention the issue number
    """
    # --- Strategy 1: timeline cross-reference ---
    try:
        headers = {**HEADERS, "Accept": "application/vnd.github.mockingbird-preview+json"}
        url = f"{BASE_URL}/repos/{repo}/issues/{issue_number}/timeline"
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, headers=headers, params={"per_page": 100})
        if resp.status_code == 200:
            for event in reversed(resp.json()):
                if event.get("event") == "cross-referenced":
                    source_issue = event.get("source", {}).get("issue", {})
                    if source_issue.get("pull_request"):
                        pr_url = source_issue.get("html_url")
                        logger.info(f"[github] Found PR via timeline for issue #{issue_number}: {pr_url}")
                        return pr_url
        else:
            logger.warning(f"[github] Timeline API returned {resp.status_code} for issue #{issue_number}")
    except Exception as e:
        logger.warning(f"[github] Timeline lookup failed for issue #{issue_number}: {e}")

    # --- Strategy 2: scan recent PRs for mention of this issue number ---
    try:
        url = f"{BASE_URL}/repos/{repo}/pulls"
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, headers=HEADERS, params={"state": "all", "per_page": 30, "sort": "updated", "direction": "desc"})
        if resp.status_code == 200:
            needle = f"#{issue_number}"
            for pr in resp.json():
                body = pr.get("body") or ""
                title = pr.get("title") or ""
                if needle in body or needle in title:
                    pr_url = pr.get("html_url")
                    logger.info(f"[github] Found PR via repo scan for issue #{issue_number}: {pr_url}")
                    return pr_url
    except Exception as e:
        logger.warning(f"[github] Repo PR scan failed for issue #{issue_number}: {e}")

    logger.info(f"[github] No PR found for issue #{issue_number}")
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
