"""
agents/coder.py — Coder AI

Responsibility:
  - Receive the approved execution plan and relevant schema.
  - Generate a single, safe SELECT SQL query.
  - Must NOT fabricate data or claim to execute the query.
"""
from __future__ import annotations

import logging
import re

from ..config import Settings
from ..models import ExecutionPlan
from .base import call_llm, get_llm_client, get_model

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are Coder AI, the SQL generation component of an analytical system.

Given an execution plan and database schema, generate a single valid SELECT SQL query.

Rules:
- Output ONLY the SQL query — no markdown, no explanations.
- Only use SELECT statements. Never use INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE, ATTACH, or PRAGMA.
- Only reference tables and columns that exist in the schema provided.
- Do not use subqueries unless necessary.
- Do not add a LIMIT clause; the system will add one automatically.
- Parameterize nothing — write a complete static query.
- The query must exactly answer the analysis goal in the plan.
"""


def _extract_sql(raw: str) -> str:
    """Strip markdown code fences and return clean SQL."""
    # Remove ```sql ... ``` or ``` ... ```
    cleaned = re.sub(r"```(?:sql)?\s*", "", raw, flags=re.IGNORECASE).replace("```", "").strip()
    return cleaned


async def generate_sql(
    plan: ExecutionPlan,
    schema_text: str,
    settings: Settings,
) -> str:
    """
    Call Coder AI to produce SQL from an approved plan + schema.
    Receives only: plan + schema. No raw data, no secrets.
    """
    client = get_llm_client(settings)

    plan_text = (
        f"Goal: {plan.analysis_goal}\n"
        f"Tables: {', '.join(plan.relevant_tables)}\n"
        f"Constraints: {'; '.join(plan.constraints) if plan.constraints else 'None'}\n"
        f"Steps:\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(plan.steps))
    )
    user_message = f"Execution Plan:\n{plan_text}\n\nSchema:\n{schema_text}"

    raw = await call_llm(
        client,
        model=get_model(settings),
        system_prompt=_SYSTEM_PROMPT,
        user_message=user_message,
    )
    sql = _extract_sql(raw)
    if not sql.strip().lower().startswith("select"):
        raise ValueError(f"Coder AI did not return a SELECT statement. Got: {sql[:100]}")
    return sql
