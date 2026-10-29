"""
test_cache.py — Tests for the TTL cache module.

Covers: cache hit, cache miss, cache expiry, user-scoped isolation,
        authorization-by-key-construction, and cache unavailability.
"""
import time
import pytest

from backend.cache import QueryCache, _build_cache_key, _normalize_question
from backend.config import Settings


@pytest.fixture
def cache_settings():
    return Settings(
        openai_api_key="test",
        database_url="sqlite+aiosqlite:///./test.db",
        jwt_secret_key="test",
        cache_ttl_seconds=2,  # Short TTL for expiry tests
        cache_max_size=10,
    )


@pytest.fixture
def cache(cache_settings):
    return QueryCache(cache_settings)


USER_A = {"user_id": "u_a", "username": "alice", "roles": ["analyst"]}
USER_B = {"user_id": "u_b", "username": "bob", "roles": ["analyst"]}
SCHEMA = "abc123"


# ── Normalization ─────────────────────────────────────────────────────────────

def test_normalize_question():
    assert _normalize_question("  What IS the revenue?  ") == "what is the revenue?"
    assert _normalize_question("Revenue\n\n by region") == "revenue by region"


# ── Cache key isolation ───────────────────────────────────────────────────────

def test_cache_key_user_isolation():
    """Different users produce different keys for the same question."""
    key_a = _build_cache_key("revenue", "u_a", SCHEMA)
    key_b = _build_cache_key("revenue", "u_b", SCHEMA)
    assert key_a != key_b


def test_cache_key_schema_isolation():
    """Different schemas produce different keys."""
    key1 = _build_cache_key("revenue", "u_a", "schema_v1")
    key2 = _build_cache_key("revenue", "u_a", "schema_v2")
    assert key1 != key2


def test_cache_key_question_normalization():
    """Normalized questions should produce the same key."""
    key1 = _build_cache_key("what is the revenue?", "u_a", SCHEMA)
    key2 = _build_cache_key("  WHAT IS THE REVENUE?  ", "u_a", SCHEMA)
    assert key1 == key2


# ── Cache miss ────────────────────────────────────────────────────────────────

def test_cache_miss(cache):
    result = cache.get("what is revenue", USER_A, SCHEMA)
    assert result is None


# ── Cache hit ─────────────────────────────────────────────────────────────────

def test_cache_hit(cache):
    payload = {"answer": "Revenue is $100", "status": "success"}
    cache.set("what is revenue", USER_A, SCHEMA, payload)
    result = cache.get("what is revenue", USER_A, SCHEMA)
    assert result == payload


# ── Cross-user isolation ──────────────────────────────────────────────────────

def test_no_cross_user_leakage(cache):
    """User B cannot retrieve user A's cached result."""
    payload = {"answer": "Secret data", "status": "success"}
    cache.set("revenue", USER_A, SCHEMA, payload)
    result = cache.get("revenue", USER_B, SCHEMA)
    assert result is None


# ── Cache expiry ──────────────────────────────────────────────────────────────

def test_cache_expiry(cache):
    """Entries expire after TTL (2 seconds in this fixture)."""
    cache.set("trend", USER_A, SCHEMA, {"answer": "up"})
    assert cache.get("trend", USER_A, SCHEMA) is not None
    time.sleep(2.1)
    assert cache.get("trend", USER_A, SCHEMA) is None


# ── Invalidation ─────────────────────────────────────────────────────────────

def test_invalidation(cache):
    cache.set("revenue", USER_A, SCHEMA, {"answer": "100"})
    cache.invalidate("revenue", USER_A, SCHEMA)
    assert cache.get("revenue", USER_A, SCHEMA) is None


# ── Cache clear ───────────────────────────────────────────────────────────────

def test_cache_clear(cache):
    cache.set("q1", USER_A, SCHEMA, {"answer": "a1"})
    cache.set("q2", USER_B, SCHEMA, {"answer": "a2"})
    cache.clear()
    assert cache.get("q1", USER_A, SCHEMA) is None
    assert cache.get("q2", USER_B, SCHEMA) is None


# ── Unavailable cache ─────────────────────────────────────────────────────────

def test_unavailable_cache_graceful(monkeypatch):
    """If cache init fails, operations degrade gracefully without exception."""
    settings = Settings(
        openai_api_key="test", database_url="sqlite+aiosqlite:///./test.db",
        jwt_secret_key="test", cache_ttl_seconds=60, cache_max_size=10,
    )
    cache = QueryCache(settings)
    # Force unavailable
    cache._available = False
    # All operations should be no-ops, no exceptions
    cache.set("q", USER_A, SCHEMA, {"answer": "x"})
    assert cache.get("q", USER_A, SCHEMA) is None
    cache.invalidate("q", USER_A, SCHEMA)
    cache.clear()
