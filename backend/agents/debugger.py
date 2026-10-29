"""
agents/debugger.py — Debugger AI

Responsibility:
  - Receive: original goal, current SQL, concise error details, schema, attempt summary.
  - Propose a corrected SQL query.
  - Must NOT bypass validation, invent data, or ignore safety rules.

Context is kept minimal — only what is needed to fix the specific error.
"""
from __future__ import annotations

import logging

from ..config import Settings
from ..models import ExecutionPlan
from .base import call_llm, get_llm_client, get_model
from .coder import _extract_sql

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are Debugger AI, the error-correction component of an analytical system.

Given a failed SQL query, a concise error message, the original analysis goal, and the schema,
produce a corrected SELECT SQL query.

Rules:
- Output ONLY the corrected SQL query — no markdown, no explanations.
- Only use SELECT statements. Never use INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE.
- Only reference tables and columns that exist in the provided schema.
- Fix the specific error described; do not change unrelated parts of the query.
- The corrected query must still address the original analysis goal.
- Do not add a LIMIT clause.
"""


async def fix_sql(
    plan: ExecutionPlan,
    failed_sql: str,
    error_message: str,
    schema_text: str,
    attempt_summary: str,
    settings: Settings,
) -> str:
    """
    Call Debugger AI to produce a corrected SQL query.
    Receives only: goal, failed SQL, sanitized error, schema, attempt summary.
    No full conversation history. No secrets.
    """
    client = get_llm_client(settings)

    user_message = (
        f"Analysis goal: {plan.analysis_goal}\n\n"
        f"Failed SQL:\n{failed_sql}\n\n"
        f"Error: {error_message}\n\n"
        f"Schema:\n{schema_text}\n\n"
        f"Prior attempt summary: {attempt_summary}"
    )

    raw = await call_llm(
        client,
        model=get_model(settings),
        system_prompt=_SYSTEM_PROMPT,
        user_message=user_message,
    )
    corrected_sql = _extract_sql(raw)
    if not corrected_sql.strip().lower().startswith("select"):
        raise ValueError(
            f"Debugger AI did not return a SELECT statement. Got: {corrected_sql[:100]}"
        )
    return corrected_sql
