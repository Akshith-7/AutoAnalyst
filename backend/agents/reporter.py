"""
agents/reporter.py — Reporter AI

Responsibility:
  - Receive the verified execution results and the original question.
  - Produce a concise, human-readable answer.
  - Must NOT invent values or claim analyses that were not run.
  - Must NOT expose internal SQL, stack traces, or schema details to the user.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..config import Settings
from .base import call_llm, get_llm_client, get_model

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are Reporter AI, the presentation component of an analytical system.

Given a user's question and the actual query results, write a clear, concise answer in plain English.

Rules:
- Base your answer ONLY on the data provided. Never invent numbers or trends.
- If the data is empty, say so clearly.
- Be concise: 2–4 sentences for the summary. You may mention key numbers.
- Do not reveal the SQL query, table names, or internal system details.
- Do not claim certainty about things not present in the data.
"""


async def generate_report(
    question: str,
    rows: list[dict[str, Any]],
    columns: list[str],
    settings: Settings,
) -> str:
    """
    Call Reporter AI to produce a human-readable answer.
    Receives only: question + actual execution results. No SQL, no schema.
    """
    client = get_llm_client(settings)

    # Summarize data to avoid large token payloads
    data_preview = _format_data(rows, columns, max_rows=20)
    user_message = (
        f"Question: {question}\n\n"
        f"Query results ({len(rows)} rows):\n{data_preview}"
    )

    answer = await call_llm(
        client,
        model=get_model(settings),
        system_prompt=_SYSTEM_PROMPT,
        user_message=user_message,
    )
    return answer


def _format_data(rows: list[dict[str, Any]], columns: list[str], max_rows: int = 20) -> str:
    """Format rows as a compact text table for the reporter prompt."""
    if not rows:
        return "(empty result set)"
    header = " | ".join(columns)
    separator = "-" * len(header)
    data_rows = [" | ".join(str(row.get(c, "")) for c in columns) for row in rows[:max_rows]]
    suffix = f"\n... ({len(rows) - max_rows} more rows)" if len(rows) > max_rows else ""
    return f"{header}\n{separator}\n" + "\n".join(data_rows) + suffix
