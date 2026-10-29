"""
main.py — Auto Analyst FastAPI application.

Routes:
  POST /auth/token    — obtain JWT
  GET  /health        — service health check
  GET  /schema        — list database tables (authenticated)
  POST /query         — submit an analytical question (authenticated)
  DELETE /cache/clear — flush the result cache (admin only)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .auth import authenticate_user, create_access_token, get_current_user
from .cache import QueryCache
from .config import Settings, get_settings
from .database import check_db_health, get_schema, schema_to_text, seed_demo_data
from .models import (
    HealthResponse,
    QueryRequest,
    QueryResponse,
    TokenRequest,
    TokenResponse,
)
from .orchestrator import Orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ── App-level singletons ──────────────────────────────────────────────────────
# Cache and orchestrator are created once at startup and reused across requests.

_cache: QueryCache | None = None
_orchestrator: Orchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cache, _orchestrator
    settings = get_settings()
    _cache = QueryCache(settings)
    _orchestrator = Orchestrator(_cache, settings)
    # Seed demo data so the app works out of the box
    await seed_demo_data(settings)
    logger.info("Auto Analyst started. Cache available: %s", _cache.available)
    yield
    logger.info("Auto Analyst shutting down.")


app = FastAPI(
    title="Auto Analyst",
    description="Multi-agent AI system for natural-language data analysis.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Dependencies ──────────────────────────────────────────────────────────────

def get_orchestrator() -> Orchestrator:
    if _orchestrator is None:
        raise RuntimeError("Orchestrator not initialized")
    return _orchestrator


def get_cache() -> QueryCache:
    if _cache is None:
        raise RuntimeError("Cache not initialized")
    return _cache


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if "admin" not in user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return user


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.post("/auth/token", response_model=TokenResponse, tags=["Auth"])
async def login(body: TokenRequest, settings: Settings = Depends(get_settings)):
    """Exchange username/password for a JWT bearer token."""
    user = authenticate_user(body.username, body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token({"sub": user["username"]}, settings)
    return TokenResponse(access_token=token)


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health(settings: Settings = Depends(get_settings)):
    """Service health check. Does not require authentication."""
    db_status = await check_db_health(settings)
    cache_status = "ok" if (_cache and _cache.available) else "unavailable"
    return HealthResponse(database=db_status, cache=cache_status)


@app.get("/schema", tags=["Data"])
async def get_db_schema(
    user: dict = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    """Return available database tables and columns."""
    tables = await get_schema(settings)
    return {"tables": [t.model_dump() for t in tables]}


@app.post("/query", response_model=QueryResponse, tags=["Analysis"])
async def run_query(
    body: QueryRequest,
    user: dict = Depends(get_current_user),
    orchestrator: Orchestrator = Depends(get_orchestrator),
):
    """
    Submit a natural-language analytical question.
    Returns a structured response with a human-readable answer and table data.
    """
    return await orchestrator.run(body.question, user)


@app.delete("/cache/clear", tags=["System"])
async def clear_cache(
    user: dict = Depends(require_admin),
    cache: QueryCache = Depends(get_cache),
):
    """Flush all cached results. Admin only."""
    cache.clear()
    return {"message": "Cache cleared.", "cleared_by": user["username"]}


# ── Static frontend ────────────────────────────────────────────────────────────

import os
_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/static", StaticFiles(directory=_frontend_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_frontend():
        return FileResponse(os.path.join(_frontend_dir, "index.html"))
