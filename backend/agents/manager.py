"""
agents/manager.py — Manager AI

Responsibility:
  - Understand the user's natural-language question.
  - Identify relevant tables/metrics and constraints.
  - Produce a structured execution plan (no code execution here).

The manager must NOT generate SQL or execute queries.
"""
from __future__ import annotations

import logging

from ..config import Settings
from ..models import ExecutionPlan
from .base import call_llm, get_llm_client, get_model, safe_parse_json

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are Manager AI, the planning component of an analytical system.

Given a user's analytical question and available database schema, you produce a structured JSON plan.
You do NOT generate SQL. You do NOT execute anything. You ONLY plan.

Respond with a JSON object matching exactly:
{
  "analysis_goal": "<one sentence describing what we want to find out>",
  "relevant_tables": ["<table1>", "<table2>"],
  "constraints": ["<any filters, date ranges, groupings>"],
  "steps": ["<step 1>", "<step 2>", "..."]
}

Rules:
- Only reference tables that exist in the schema provided.
- Be concise. Steps should be high-level analytical steps, not SQL.
- If the question is ambiguous, plan for the most common interpretation.
- If the question cannot be answered from the schema, set steps to ["Question cannot be answered from available data."]
"""


async def create_plan(
    question: str,
    schema_text: str,
    settings: Settings,
) -> ExecutionPlan:
    """
    Call Manager AI to produce an execution plan.
    Receives only: question + schema. No execution results, no secrets.
    """
    client = get_llm_client(settings)
    user_message = f"Question: {question}\n\nAvailable schema:\n{schema_text}"

    raw = await call_llm(
        client,
        model=get_model(settings),
        system_prompt=_SYSTEM_PROMPT,
        user_message=user_message,
        response_format="json",
    )
    data = safe_parse_json(raw)
    try:
        return ExecutionPlan(**data)
    except Exception as exc:
        raise ValueError(f"Manager AI returned unexpected structure: {exc}") from exc
