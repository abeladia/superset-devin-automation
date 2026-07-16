# superset-devin-automation

Event-driven automation that dispatches **Devin AI sessions** whenever a GitHub issue is labeled `devin-fix` in the [abeladia/superset_update](https://github.com/abeladia/superset_update) fork.

Built as part of a Cognition Deployed Engineer take-home assignment.

---

## Architecture

```
GitHub Issue labeled "devin-fix"
        │
        ▼ GitHub Webhook (POST /webhook)
┌───────────────────────┐
│   FastAPI server      │  ← runs in Docker
│   (app/main.py)       │
└───────────┬───────────┘
            │ create session
            ▼
     Devin API v1
     POST /v1/sessions
            │
            │ session_id + URL
            ▼
┌───────────────────────┐
│  Background monitor   │  ← asyncio task
│  (app/monitor.py)     │  polls every 30s
└───────────┬───────────┘
            │ on terminal status
            ▼
   GitHub Issues API
   POST comment with PR link
            │
            ▼
   SQLite (observability)
   /data/sessions.db
```

**Every step is logged to SQLite** — session created, each poll, PR URL detected, comment posted, or any error.

---

## Quickstart

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_USERNAME/superset-devin-automation
cd superset-devin-automation
cp .env.example .env
# Fill in DEVIN_API_KEY and GITHUB_TOKEN in .env
```

### 2. Run with Docker

```bash
docker compose up --build
```

Server starts on `http://localhost:8000`.

### 3. Expose via ngrok (for webhook delivery)

In a second terminal:

```bash
ngrok http 8000
```

Copy the `https://xxxx.ngrok-free.app` URL.

### 4. Register the webhook on GitHub

Go to `https://github.com/abeladia/superset_update/settings/hooks` → **Add webhook**:

| Field | Value |
|---|---|
| Payload URL | `https://xxxx.ngrok-free.app/webhook` |
| Content type | `application/json` |
| Secret | (paste your `GITHUB_WEBHOOK_SECRET` if set) |
| Events | **Issues** only |

### 5. Trigger it

Go to any issue in `abeladia/superset_update` and add the `devin-fix` label (they're already labeled — you can remove and re-add to re-trigger).

Watch the server logs. Within ~1 minute you'll see the Devin session URL in the logs. Within ~30-60 minutes Devin posts a PR and the automation comments the PR link on the issue.

---

## Observability API

| Endpoint | Description |
|---|---|
| `GET /health` | Health check |
| `GET /sessions` | All dispatched sessions with status |
| `GET /sessions/{session_id}` | Single session + full event log |

Example:

```bash
curl http://localhost:8000/sessions | python3 -m json.tool
```

Sample response:

```json
[
  {
    "id": 1,
    "session_id": "session_abc123",
    "devin_url": "https://app.devin.ai/sessions/abc123",
    "issue_number": 3,
    "issue_title": "Migrate SqlLab tests away from deprecated describe() nesting",
    "repo": "abeladia/superset_update",
    "status": "finished",
    "pr_url": "https://github.com/abeladia/superset_update/pull/8",
    "created_at": "2026-07-15T14:22:00+00:00",
    "updated_at": "2026-07-15T15:01:33+00:00"
  }
]
```

---

## Project structure

```
superset-devin-automation/
├── app/
│   ├── main.py           # FastAPI app — webhook endpoint + observability routes
│   ├── devin_client.py   # Devin API v1 wrapper (create session, poll status)
│   ├── github_client.py  # GitHub API (post issue comments)
│   ├── db.py             # SQLite schema + read/write helpers
│   └── monitor.py        # Background asyncio poller
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

---

## Design decisions

**Why FastAPI?** Async-native, so background polling tasks don't block webhook responses. The monitor runs as an `asyncio` background task — no separate worker process needed.

**Why SQLite?** Zero-dependency, file-backed, human-readable. Perfect for a single-node demo. In production you'd swap for Postgres.

**Why Devin v1 API?** Personal API keys work out of the box. v3 adds RBAC and session attribution — a natural next step for multi-user team use.

**Poll interval:** 30 seconds, max 60 minutes. Devin typically finishes in 20–40 minutes for scoped tasks like these.

**Webhook signature verification:** Enabled when `GITHUB_WEBHOOK_SECRET` is set. Skip it for local dev; always enable it in production.

---

## Devin prompt strategy

The prompt passed to each Devin session includes:
- The repo URL
- The full issue title and body
- Explicit instructions to clone, fix, test, and open a PR referencing the issue number

This keeps Devin focused on one issue at a time and ensures the PR is traceable back to the originating issue.
