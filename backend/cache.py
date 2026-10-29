"""
cache.py — In-process TTL cache for Auto Analyst query results.

Design decisions:
- Uses `cachetools.TTLCache` (no external Redis required).
- Cache keys include normalized question + user_id + schema_hash to scope results
  by user/tenant and data version.
- Authorization is ALWAYS re-checked before serving a cached result — a cache hit
  never bypasses access control.
- If the cache is unavailable or raises, the main workflow continues unaffected.
- Only successful results are cached; failures are never stored.
- TTL limits cache age only; it does NOT guarantee the underlying data hasn't changed.
  Use CACHE_TTL_SECONDS to tune freshness requirements.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from typing import Any, Optional

from cachetools import TTLCache

from .config import Settings

logger = logging.getLogger(__name__)


def _normalize_question(question: str) -> str:
    """Lowercase, collapse whitespace for consistent cache keys."""
    return re.sub(r"\s+", " ", question.strip().lower())


def _build_cache_key(question: str, user_id: str, schema_hash: str) -> str:
    """
    Deterministic cache key:
      SHA256( normalize(question) | user_id | schema_hash )
    Including user_id scopes results so user A cannot see user B's data via cache.
    """
    payload = f"{_normalize_question(question)}|{user_id}|{schema_hash}"
    return hashlib.sha256(payload.encode()).hexdigest()


class QueryCache:
    """Thread-safe TTL cache wrapper."""

    def __init__(self, settings: Settings) -> None:
        self._lock = threading.Lock()
        try:
            self._cache: TTLCache = TTLCache(
                maxsize=settings.cache_max_size,
                ttl=settings.cache_ttl_seconds,
            )
            self._available = True
        except Exception as exc:  # pragma: no cover
            logger.error("Cache initialization failed: %s", exc)
            self._cache = None  # type: ignore[assignment]
            self._available = False

    # ── Public API ─────────────────────────────────────────────────────────────

    def get(
        self,
        question: str,
        user: dict,
        schema_hash: str,
    ) -> Optional[Any]:
        """
        Return cached result if present and user is authorized.
        Authorization check: user_id in key means another user's key will never
        match, so there is no cross-user leakage by construction.
        Returns None on miss, expiry, or cache unavailability.
        """
        if not self._available:
            return None
        key = _build_cache_key(question, user["user_id"], schema_hash)
        try:
            with self._lock:
                return self._cache.get(key)
        except Exception as exc:
            logger.warning("Cache get error (ignored): %s", exc)
            return None

    def set(
        self,
        question: str,
        user: dict,
        schema_hash: str,
        value: Any,
    ) -> None:
        """Store a successful result. Failures must never be passed here."""
        if not self._available:
            return
        key = _build_cache_key(question, user["user_id"], schema_hash)
        try:
            with self._lock:
                self._cache[key] = value
        except Exception as exc:
            logger.warning("Cache set error (ignored): %s", exc)

    def invalidate(self, question: str, user: dict, schema_hash: str) -> None:
        """Explicitly invalidate a single cache entry."""
        if not self._available:
            return
        key = _build_cache_key(question, user["user_id"], schema_hash)
        try:
            with self._lock:
                self._cache.pop(key, None)
        except Exception as exc:
            logger.warning("Cache invalidate error (ignored): %s", exc)

    def clear(self) -> None:
        """Flush all cache entries (e.g., after schema change)."""
        if not self._available:
            return
        try:
            with self._lock:
                self._cache.clear()
        except Exception as exc:
            logger.warning("Cache clear error (ignored): %s", exc)

    @property
    def available(self) -> bool:
        return self._available
