# Auto Analyst

**Multi-agent AI system for natural-language data analysis.**

Ask questions about your data in plain English. Auto Analyst uses a pipeline of specialized AI agents — Manager, Coder, Debugger, and Reporter — to plan, generate, execute, and explain SQL queries automatically.

---

## Architecture

```
User → FastAPI → [Cache Check] → Manager AI → Coder AI → SQL Executor
                                                              ↓ (fail)
                                                         Debugger AI → retry
                                                              ↓ (success)
                                                         Reporter AI → Answer
```

### Agents

| Agent | Role |
|---|---|
| **Manager AI** | Understands the question, identifies relevant tables, produces a structured execution plan (no SQL) |
| **Coder AI** | Generates a SELECT SQL query from the approved plan and schema |
| **Debugger AI** | Fixes failed SQL using the error, original goal, schema, and prior attempt summary |
| **Reporter AI** | Translates verified execution results into a plain-English answer |

---

## Stack

- **API**: FastAPI (async)
- **Database**: SQLite (dev) / PostgreSQL (prod) via SQLAlchemy async
- **Cache**: `cachetools` TTLCache (in-process, no external service needed)
- **Auth**: JWT bearer tokens (python-jose + passlib/bcrypt)
- **LLM**: OpenAI-compatible API (configurable endpoint and model)
- **Tests**: pytest + pytest-asyncio

---

## Quick Start

### 1. Install dependencies

```bash
cd /home/sriram/faa
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and set your OPENAI_API_KEY
```

### 3. Run the server

```bash
uvicorn backend.main:app --reload --port 8000
```

Visit http://localhost:8000 for the chat UI, or http://localhost:8000/docs for the API explorer.

---

## Configuration Reference

All settings are environment variables (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | _(required if using Gemini)_ | Google Gemini API Key |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Gemini model name |
| `OPENAI_API_KEY` | _(optional)_ | Alternative OpenAI API key |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | LLM endpoint (swap for Azure/Ollama/etc.) |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model name |
| `DATABASE_URL` | `sqlite+aiosqlite:///./autoanalyst.db` | SQLAlchemy async DB URL |
| `JWT_SECRET_KEY` | _(required)_ | Random 64-char hex string for JWT signing |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | JWT validity period |
| `CACHE_TTL_SECONDS` | `300` | Result cache TTL in seconds |
| `CACHE_MAX_SIZE` | `512` | Max cached entries (LRU eviction) |
| `MAX_RETRIES` | `3` | Max Debugger AI retry attempts per query |
| `QUERY_ROW_LIMIT` | `1000` | Max rows returned per query |
| `QUERY_TIMEOUT_SECONDS` | `30` | DB query timeout in seconds |

### Cache / TTL Behaviour

- **TTL limits cache age only.** It does NOT guarantee the underlying data hasn't changed.
- Cache keys include: `SHA256(normalized_question | user_id | schema_hash)`.
- User-scoped keys prevent cross-user data leakage via cache.
- Authorization is enforced structurally (different users → different keys, always).
- Failures are never cached; only successful results are stored.
- If the cache is unavailable, the main workflow continues unaffected.
- Flush the cache via `DELETE /cache/clear` (admin role required), or when schema changes.

### Memory / Context Strategy

- Each request maintains a compact `TaskContext` containing:
  - Original question
  - Schema text
  - Approved plan
  - Current SQL
  - Last error message
  - Summarized prior attempts (capped at 5 entries)
- Each agent receives only the fields it needs — no full conversation history.
- Task context is scoped to the current request and discarded after completion.

### Retry Behaviour

- `MAX_RETRIES` controls the maximum number of Debugger AI correction attempts (default: 3).
- On each retry: Debugger AI proposes a fix → SQL is re-validated → re-executed.
- Error types are distinguished: validation errors, DB errors, timeouts, model errors.
- On exhausted retries: a safe, non-sensitive error message is returned to the user.
- Internal diagnostics (SQL, errors) are logged but never surfaced to the user.

---

## Demo Accounts

| Username | Password | Role |
|---|---|---|
| `admin` | `admin123` | analyst + admin |
| `analyst` | `analyst123` | analyst |

> Replace `DEMO_USERS` in `backend/auth.py` with a real DB lookup for production.

---

## API Reference

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/token` | None | Get JWT token |
| `GET` | `/health` | None | Service health check |
| `GET` | `/schema` | Bearer | List DB tables/columns |
| `POST` | `/query` | Bearer | Submit analytical question |
| `DELETE` | `/cache/clear` | Bearer (admin) | Flush result cache |

---

## Running Tests

```bash
cd /home/sriram/faa
pytest -v
```

Test files:
- `backend/tests/test_cache.py` — Cache hit/miss/expiry/isolation/unavailability
- `backend/tests/test_agents.py` — Agent unit tests (mocked LLM)
- `backend/tests/test_workflow.py` — End-to-end API + workflow tests
- `backend/tests/test_retry.py` — Retry boundaries, SQL validation

---

## Security Notes

- All secrets loaded from environment variables — never hardcoded.
- Only SELECT statements are allowed; destructive SQL keywords are blocked pre-execution.
- Row limits and timeouts enforced on every query.
- Stack traces, SQL, and internal prompts never returned to users.
- JWT token required for all data endpoints.
- Cache keys are user-scoped; cached data is never shared across users.

---

## Known Limitations

- Demo user store is in-memory (`DEMO_USERS` dict in `auth.py`). Replace with DB-backed auth for production.
- In-process cache is per-process; multiple workers won't share cache. Upgrade to Redis for multi-worker deployments.
- LLM provider is OpenAI-compatible; streaming responses are not yet implemented in the frontend.
- No persistent conversation memory — each question is an independent request.
