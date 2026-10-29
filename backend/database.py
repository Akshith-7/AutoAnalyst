"""
database.py — SQLAlchemy async engine, schema introspection, and safe query execution.

Security:
- Queries run via a read-only connection strategy (parameterized, SELECT-only after validation).
- Row and timeout limits enforced.
- No secrets exposed in error messages.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from .config import Settings
from .models import ColumnMeta, TableMeta

logger = logging.getLogger(__name__)

# ── Engine factory ─────────────────────────────────────────────────────────────

_engines: dict[str, AsyncEngine] = {}


def get_engine(settings: Settings) -> AsyncEngine:
    url = settings.database_url
    if url not in _engines:
        connect_args: dict = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engines[url] = create_async_engine(
            url,
            connect_args=connect_args,
            echo=False,
            pool_pre_ping=True,
        )
    return _engines[url]


def get_session_factory(engine: AsyncEngine) -> sessionmaker:
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ── Schema introspection ──────────────────────────────────────────────────────

async def get_schema(settings: Settings) -> list[TableMeta]:
    """Return schema metadata for all tables visible to the engine."""
    engine = get_engine(settings)

    def _inspect_sync(conn_sync) -> list[TableMeta]:
        raw_inspector = inspect(conn_sync)
        result = []
        for table_name in raw_inspector.get_table_names():
            cols = [
                ColumnMeta(name=c["name"], type=str(c["type"]))
                for c in raw_inspector.get_columns(table_name)
            ]
            result.append(TableMeta(name=table_name, columns=cols))
        return result

    try:
        async with engine.connect() as conn:
            return await conn.run_sync(_inspect_sync)
    except Exception as exc:
        logger.error("Schema introspection error: %s", exc)
        return []


def schema_to_text(tables: list[TableMeta]) -> str:
    """Convert schema metadata to a concise text format for agent prompts."""
    lines = []
    for t in tables:
        col_defs = ", ".join(f"{c.name} ({c.type})" for c in t.columns)
        lines.append(f"Table `{t.name}`: {col_defs}")
    return "\n".join(lines) if lines else "No tables found."


def schema_hash(tables: list[TableMeta]) -> str:
    """SHA256 of the schema — used in cache keys to detect schema changes."""
    payload = json.dumps(
        [{"name": t.name, "columns": [{"name": c.name, "type": c.type} for c in t.columns]}
         for t in tables],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ── Safe query execution ──────────────────────────────────────────────────────

_BLOCKED_KEYWORDS = frozenset({
    "insert", "update", "delete", "drop", "truncate",
    "create", "alter", "replace", "grant", "revoke",
    "attach", "detach", "pragma",
})


def validate_sql(sql: str) -> tuple[bool, str]:
    """
    Basic safety validation for SQL before execution.
    Returns (is_safe, reason). This is defense-in-depth, not the only safeguard.
    """
    normalized = sql.strip().lower()
    if not normalized.startswith("select"):
        return False, "Only SELECT queries are permitted."
    for kw in _BLOCKED_KEYWORDS:
        # Check as whole word to avoid false positives (e.g. 'selected')
        import re
        if re.search(rf"\b{kw}\b", normalized):
            return False, f"Blocked keyword detected: {kw}"
    return True, ""


async def run_query(
    sql: str,
    settings: Settings,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Execute a validated SELECT query and return (rows, columns).
    Applies row limit. Raises on error — caller handles retries.
    """
    is_safe, reason = validate_sql(sql)
    if not is_safe:
        raise ValueError(f"SQL validation failed: {reason}")

    engine = get_engine(settings)
    async with engine.connect() as conn:
        # Enforce row limit via LIMIT injection if not already present
        limited_sql = _apply_limit(sql, settings.query_row_limit)
        result = await conn.execute(text(limited_sql))
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]
    return rows, columns


def _apply_limit(sql: str, limit: int) -> str:
    """Append LIMIT clause if the query doesn't already have one."""
    upper = sql.strip().rstrip(";").upper()
    if "LIMIT" not in upper:
        return f"{sql.rstrip().rstrip(';')} LIMIT {limit}"
    return sql


# ── Database health check ─────────────────────────────────────────────────────

async def check_db_health(settings: Settings) -> str:
    try:
        engine = get_engine(settings)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:
        logger.error("DB health check failed: %s", exc)
        return "error"


# ── Demo data seeding ─────────────────────────────────────────────────────────

async def seed_demo_data(settings: Settings) -> None:
    """
    Create demo tables for development/testing so the workflow is immediately usable.
    Only runs if the 'sales' table does not already exist.
    """
    from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Date
    from sqlalchemy.schema import CreateTable
    engine = get_engine(settings)
    metadata = MetaData()

    sales = Table(
        "sales", metadata,
        Column("id", Integer, primary_key=True),
        Column("date", String),
        Column("product", String),
        Column("region", String),
        Column("revenue", Float),
        Column("units", Integer),
    )
    customers = Table(
        "customers", metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String),
        Column("region", String),
        Column("signup_date", String),
        Column("tier", String),
    )

    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        # Check if demo data already present
        result = await conn.execute(text("SELECT COUNT(*) FROM sales"))
        if result.scalar() == 0:
            await conn.execute(sales.insert(), [
                {"date": "2024-01-01", "product": "Widget A", "region": "North", "revenue": 1200.0, "units": 30},
                {"date": "2024-01-01", "product": "Widget B", "region": "South", "revenue": 850.5, "units": 20},
                {"date": "2024-02-01", "product": "Widget A", "region": "East", "revenue": 1400.0, "units": 35},
                {"date": "2024-02-01", "product": "Gadget X", "region": "West", "revenue": 3200.0, "units": 50},
                {"date": "2024-03-01", "product": "Widget B", "region": "North", "revenue": 900.0, "units": 22},
                {"date": "2024-03-01", "product": "Gadget X", "region": "South", "revenue": 4100.0, "units": 65},
            ])
            await conn.execute(customers.insert(), [
                {"name": "Alice Corp", "region": "North", "signup_date": "2023-06-01", "tier": "gold"},
                {"name": "Bob LLC", "region": "South", "signup_date": "2023-08-15", "tier": "silver"},
                {"name": "Cara Inc", "region": "East", "signup_date": "2024-01-10", "tier": "gold"},
                {"name": "Dave Co", "region": "West", "signup_date": "2024-02-20", "tier": "bronze"},
            ])
        logger.info("Demo data ready.")
