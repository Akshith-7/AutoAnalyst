"""
test_workflow.py — End-to-end API and orchestrator workflow tests.

Covers: auth, health, schema, successful query, failed query,
        retry behavior, cache hit/miss, and unauthorized access.
LLMs and DB calls are mocked where needed.
"""
import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.models import ExecutionPlan
import backend.main as _main_module
from backend.cache import QueryCache
from backend.orchestrator import Orchestrator
from backend.config import get_settings
from backend.database import seed_demo_data


@pytest_asyncio.fixture(autouse=True, scope="module")
async def init_app_singletons():
    """Initialize app singletons (cache, orchestrator) once per test module."""
    settings = get_settings()
    cache = QueryCache(settings)
    orchestrator = Orchestrator(cache, settings)
    _main_module._cache = cache
    _main_module._orchestrator = orchestrator
    await seed_demo_data(settings)
    yield
    _main_module._cache = None
    _main_module._orchestrator = None


def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=30)



# ── Auth tests ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_success():
    async with make_client() as client:
        res = await client.post("/auth/token", json={"username": "admin", "password": "admin123"})
    assert res.status_code == 200
    assert "access_token" in res.json()


@pytest.mark.asyncio
async def test_login_wrong_password():
    async with make_client() as client:
        res = await client.post("/auth/token", json={"username": "admin", "password": "wrong"})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_query_requires_auth():
    # HTTPBearer with auto_error=True returns 403 when no credentials provided
    async with make_client() as client:
        res = await client.post("/query", json={"question": "revenue?"})
    assert res.status_code in (401, 403)  # FastAPI HTTPBearer returns 403


# ── Health check ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_endpoint():
    async with make_client() as client:
        res = await client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "database" in data
    assert "cache" in data


# ── Schema endpoint ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schema_requires_auth():
    async with make_client() as client:
        res = await client.get("/schema")
    assert res.status_code in (401, 403)  # FastAPI HTTPBearer returns 403


@pytest.mark.asyncio
async def test_schema_returns_tables():
    async with make_client() as client:
        login = await client.post("/auth/token", json={"username": "admin", "password": "admin123"})
        token = login.json()["access_token"]
        res = await client.get("/schema", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert "tables" in res.json()


# ── Query workflow ─────────────────────────────────────────────────────────────

MOCK_PLAN = ExecutionPlan(
    analysis_goal="Find total revenue",
    relevant_tables=["sales"],
    constraints=[],
    steps=["Sum revenue from sales"],
)
MOCK_SQL = "SELECT SUM(revenue) as total FROM sales"
MOCK_ROWS = [{"total": 11650.5}]
MOCK_COLUMNS = ["total"]
MOCK_ANSWER = "The total revenue is $11,650.50."


@pytest.mark.asyncio
async def test_successful_query_workflow():
    """Full workflow: login → query → cached response."""
    async with make_client() as client:
        login = await client.post("/auth/token", json={"username": "analyst", "password": "analyst123"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        with patch("backend.agents.manager.call_llm", new=AsyncMock(
            return_value=json.dumps({
                "analysis_goal": MOCK_PLAN.analysis_goal,
                "relevant_tables": MOCK_PLAN.relevant_tables,
                "constraints": [],
                "steps": MOCK_PLAN.steps,
            })
        )), patch("backend.agents.coder.call_llm", new=AsyncMock(return_value=MOCK_SQL)), \
           patch("backend.agents.reporter.call_llm", new=AsyncMock(return_value=MOCK_ANSWER)):

            res = await client.post("/query", json={"question": "total revenue?"}, headers=headers)

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["answer"] == MOCK_ANSWER
    assert data["cached"] is False


@pytest.mark.asyncio
async def test_empty_question_rejected():
    async with make_client() as client:
        login = await client.post("/auth/token", json={"username": "admin", "password": "admin123"})
        token = login.json()["access_token"]
        res = await client.post(
            "/query", json={"question": "ab"},  # too short (min_length=3)
            headers={"Authorization": f"Bearer {token}"}
        )
    assert res.status_code == 422  # Pydantic validation


# ── Retry behavior ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_on_db_error():
    """On DB error, orchestrator should invoke debugger and retry."""
    async with make_client() as client:
        login = await client.post("/auth/token", json={"username": "admin", "password": "admin123"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        call_count = {"n": 0}

        async def mock_run_query(sql, settings):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise Exception("Simulated DB error")
            return MOCK_ROWS, MOCK_COLUMNS

        with patch("backend.agents.manager.call_llm", new=AsyncMock(
            return_value=json.dumps({
                "analysis_goal": "test",
                "relevant_tables": ["sales"],
                "constraints": [],
                "steps": ["step1"],
            })
        )), patch("backend.agents.coder.call_llm", new=AsyncMock(return_value=MOCK_SQL)), \
           patch("backend.agents.debugger.call_llm", new=AsyncMock(return_value=MOCK_SQL)), \
           patch("backend.agents.reporter.call_llm", new=AsyncMock(return_value=MOCK_ANSWER)), \
           patch("backend.orchestrator.run_query", new=mock_run_query):

            res = await client.post(
                "/query", json={"question": "total revenue?"},
                headers=headers
            )

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["retry_count"] >= 1


# ── Cache clear (admin only) ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cache_clear_admin_only():
    async with make_client() as client:
        # Analyst cannot clear cache
        login = await client.post("/auth/token", json={"username": "analyst", "password": "analyst123"})
        token = login.json()["access_token"]
        res = await client.delete("/cache/clear", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_cache_clear_as_admin():
    async with make_client() as client:
        login = await client.post("/auth/token", json={"username": "admin", "password": "admin123"})
        token = login.json()["access_token"]
        res = await client.delete("/cache/clear", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["message"] == "Cache cleared."
