"""
conftest.py — Shared fixtures for Auto Analyst tests.
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.config import Settings


@pytest.fixture
def settings():
    return Settings(
        openai_api_key="test-key",
        database_url="sqlite+aiosqlite:///./test_autoanalyst.db",
        jwt_secret_key="test-secret-key-for-testing-only",
        cache_ttl_seconds=60,
        cache_max_size=10,
        max_retries=2,
        query_row_limit=100,
        query_timeout_seconds=10,
    )


@pytest_asyncio.fixture
async def client():
    """Async HTTP test client for the FastAPI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_headers(client):
    """Get JWT headers for the admin user."""
    res = await client.post("/auth/token", json={"username": "admin", "password": "admin123"})
    assert res.status_code == 200
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def analyst_headers(client):
    """Get JWT headers for the analyst user."""
    res = await client.post("/auth/token", json={"username": "analyst", "password": "analyst123"})
    assert res.status_code == 200
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
