# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

FastAPI service for sending views, reactions, and shares to Telegram posts. Uses Redis as a session pool, PostgreSQL for persistent state, and Telethon as the Telegram client.

## Commands

### Local Development
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python api_server.py          # Starts on http://localhost:8000
```

### Docker
```bash
docker compose up -d          # Start all services (PostgreSQL, Redis, app, uploader)
docker compose logs -f app
docker compose run --rm uploader   # Upload sessions from folder to Redis
```

### Database Migrations
```bash
alembic upgrade head
alembic revision --autogenerate -m "description"
alembic downgrade -1
alembic history
```

### CLI Worker (no API, interactive)
```bash
python worker.py              # Prompts for post_link, service type, account count
```

## Architecture

### Request Flow
1. Client sends `POST /task` with `X-API-Key` header
2. Server looks up key's priority from PostgreSQL
3. Task pushed to in-memory `asyncio.PriorityQueue` as `(priority, timestamp, task_dict)`
4. Background `worker_loop()` dequeues tasks and fans out to Telegram accounts
5. Progress stored in both RAM (`task_status` dict) and PostgreSQL (`task_logs` table)

### Key Concurrency Patterns

**Semaphore + Per-Account Lock (in `api_server.py` / `account_queue.py`):**
```python
semaphore = asyncio.Semaphore(PARALLEL_ACCOUNTS)  # caps total concurrency
async with semaphore:
    account_lock = await get_lock(session)         # one lock per account identity
    async with account_lock:
        await action_func(...)
```
The per-account lock (`account_queue.py`) prevents the same Telegram account from being used in parallel across tasks. Registry is protected by `_registry_lock` to avoid races during lock creation.

**Dual-Layer State:**
- RAM (`task_status` dict): fast status queries
- PostgreSQL (`task_logs`): persistent history via `task_log_store.py`

### Session Lifecycle
Sessions are Telegram account credentials stored in Redis (key: `REDIS_KEY`, default `telegram:sessions:full`) as a Set of JSON strings. `session_store.py` handles status transitions:
- **active** → **flood**: temporary block; escalating timeouts (1h extra on 1st/2nd flood, 24h on 3rd+)
- **active** → **banned**: permanent, triggered by `UserDeactivatedBanError` etc.
- `flood_release_loop()` background task re-activates expired flood blocks every 5 minutes.

### Deduplication
`post_session_logs` table with unique constraint on `(session_uid, post_link, service, done_date)` prevents the same account from double-acting on a post on the same day.

### Circuit Breaker
Global `IS_PAUSED` flag. If error count exceeds `CB_THRESHOLD` (default: 10) within `CB_WINDOW` seconds (default: 300), the worker loop stops processing new tasks until manually unpaused.

## Configuration (`.env`)
```
DATABASE_URL=postgresql://postapi:secret123@postgres:5432/postapi
REDIS_URL=redis://redis:6379
REDIS_KEY=telegram:sessions:full
PARALLEL_ACCOUNTS=400    # semaphore limit for concurrent accounts
CB_THRESHOLD=10          # circuit breaker error count
CB_WINDOW=300            # circuit breaker time window (seconds)
```

## Startup Sequence
`entrypoint.sh` → waits for PostgreSQL → `alembic upgrade head` → `python api_server.py`

On startup, `api_server.py` auto-creates `admin/admin123` if no users exist, then launches `worker_loop()` and `flood_release_loop()` as background asyncio tasks.

## Authentication
- **Users** authenticate via `X-Token` header (UUID from `/auth/login`)
- **API clients** submit tasks via `X-API-Key` header (managed by admin)
- Admin role required for `/admin/*` endpoints

## Notes
- README.md and most code comments are in Uzbek
- `sessions/` directory holds JSON files for bulk session import via the uploader service
- `sessions.example.json` shows the expected session format
