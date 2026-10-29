"""
models.py — Pydantic request/response schemas for the Auto Analyst API.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Auth ──────────────────────────────────────────────────────────────────────

class TokenRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Query ─────────────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000,
                          description="Natural-language analytical question")


class ColumnMeta(BaseModel):
    name: str
    type: str


class TableMeta(BaseModel):
    name: str
    columns: list[ColumnMeta]
    row_count: Optional[int] = None


class ExecutionPlan(BaseModel):
    """Structured plan produced by Manager AI."""
    analysis_goal: str
    relevant_tables: list[str]
    constraints: list[str] = []
    steps: list[str]


class AgentTrace(BaseModel):
    """Minimal trace of what each agent did — for debugging, not user-facing."""
    manager_plan: Optional[ExecutionPlan] = None
    generated_sql: Optional[str] = None
    retry_count: int = 0
    cache_hit: bool = False


class QueryStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CACHED = "cached"


class QueryResponse(BaseModel):
    status: QueryStatus
    question: str
    answer: str
    table_data: Optional[list[dict[str, Any]]] = None
    columns: Optional[list[str]] = None
    cached: bool = False
    retry_count: int = 0
    error: Optional[str] = None          # sanitized, user-safe message


class HealthResponse(BaseModel):
    status: str = "ok"
    database: str = "unknown"
    cache: str = "unknown"
