"""
test_retry.py — Tests for retry boundary behavior and error classification.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from backend.config import Settings
from backend.cache import QueryCache
from backend.orchestrator import Orchestrator, TaskContext
from backend.models import ExecutionPlan, QueryStatus


@pytest.fixture
def settings():
    return Settings(
        openai_api_key="test-key",
        database_url="sqlite+aiosqlite:///./test_retry.db",
        jwt_secret_key="test-secret",
        max_retries=2,
        cache_ttl_seconds=60,
        cache_max_size=10,
        query_timeout_seconds=5,
    )


@pytest.fixture
def cache(settings):
    return QueryCache(settings)


USER = {"user_id": "u_test", "username": "test", "roles": ["analyst"]}
PLAN_JSON = {
    "analysis_goal": "test goal",
    "relevant_tables": ["sales"],
    "constraints": [],
    "steps": ["step1"],
}


# ── Exhausted retries ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retries_exhausted_returns_failed(settings, cache):
    """When all retries fail, status should be FAILED with a safe error message."""
    orchestrator = Orchestrator(cache, settings)

    import json
    with patch("backend.agents.manager.call_llm", new=AsyncMock(return_value=json.dumps(PLAN_JSON))), \
         patch("backend.agents.coder.call_llm", new=AsyncMock(return_value="SELECT 1")), \
         patch("backend.agents.debugger.call_llm", new=AsyncMock(return_value="SELECT 1")), \
         patch("backend.orchestrator.run_query", new=AsyncMock(side_effect=Exception("DB always fails"))), \
         patch("backend.orchestrator.get_schema", new=AsyncMock(return_value=[])):

        result = await orchestrator.run("What is total revenue?", USER)

    assert result.status == QueryStatus.FAILED
    assert result.error is not None
    # Error must NOT contain SQL, stack traces, or internal details
    assert "SELECT" not in (result.error or "")
    assert "Traceback" not in (result.error or "")


# ── Retry count tracking ──────────────────────────────────────────────────────

def test_task_context_attempt_summary():
    """TaskContext should correctly build attempt summaries and limit history."""
    ctx = TaskContext(question="q", schema_text="s")
    for i in range(7):
        ctx.retry_count = i
        ctx.add_attempt_summary(f"SELECT {i}", f"error {i}")
    # Should be capped at 5
    assert len(ctx.attempt_summaries) == 5
    assert ctx.attempt_summary_text


# ── SQL validation ────────────────────────────────────────────────────────────

def test_validate_sql_allows_select():
    from backend.database import validate_sql
    is_safe, reason = validate_sql("SELECT * FROM sales")
    assert is_safe is True


def test_validate_sql_blocks_drop():
    from backend.database import validate_sql
    is_safe, reason = validate_sql("DROP TABLE sales")
    assert is_safe is False
    assert "Only SELECT" in reason


def test_validate_sql_blocks_delete():
    from backend.database import validate_sql
    is_safe, reason = validate_sql("SELECT id FROM sales WHERE 1=1; DELETE FROM sales")
    assert is_safe is False


def test_validate_sql_blocks_insert():
    from backend.database import validate_sql
    is_safe, _ = validate_sql("INSERT INTO sales VALUES (1, 'x', 100)")
    assert is_safe is False


def test_apply_limit_adds_limit():
    from backend.database import _apply_limit
    sql = "SELECT * FROM sales"
    limited = _apply_limit(sql, 100)
    assert "LIMIT 100" in limited


def test_apply_limit_respects_existing():
    from backend.database import _apply_limit
    sql = "SELECT * FROM sales LIMIT 10"
    limited = _apply_limit(sql, 100)
    assert limited == sql  # Should not double-add LIMIT
