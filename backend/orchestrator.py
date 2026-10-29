"""
orchestrator.py — End-to-end multi-agent workflow for Auto Analyst.

Workflow (matches spec steps 1–15):
  1. Validate input.
  2. Establish user identity (caller's responsibility via auth dependency).
  3. Check cache (user-scoped, auth-safe key).
  4. Cache hit → return cached result (auth already enforced by key scoping).
  5. Cache miss → Manager AI creates execution plan.
  6. Coder AI generates SQL from plan + schema.
  7. Validate SQL before execution.
  8. Execute query (read-only, with limits).
  9. Success → Reporter AI creates human-readable answer.
  10. Failure → Debugger AI proposes fix → revalidate → retry (up to MAX_RETRIES).
  11. Retries exhausted → log diagnostic, return safe error.
  12. Cache successful result with TTL.
  13. Return final answer.

Error types are distinguished: model errors, validation errors, DB errors, timeouts, exhausted retries.
Secrets, SQL, and stack traces are never surfaced to the user.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .agents import debugger, manager, reporter
from .agents import coder as coder_agent
from .cache import QueryCache
from .config import Settings
from .database import get_schema, run_query, schema_hash, schema_to_text
from .models import ExecutionPlan, QueryResponse, QueryStatus

logger = logging.getLogger(__name__)


# ── Task context (minimal per-request memory) ─────────────────────────────────

@dataclass
class TaskContext:
    """
    Compact, per-request context passed between agents.
    Each agent receives only the fields it needs.
    Secrets, credentials, and full conversation history are never stored here.
    """
    question: str
    schema_text: str
    plan: Optional[ExecutionPlan] = None
    current_sql: Optional[str] = None
    last_error: Optional[str] = None
    attempt_summaries: list[str] = field(default_factory=list)
    retry_count: int = 0

    def add_attempt_summary(self, sql: str, error: str) -> None:
        """Record a failed attempt. Keep list short to avoid context bloat."""
        summary = f"Attempt {self.retry_count}: SQL error — {error[:120]}"
        self.attempt_summaries.append(summary)
        # Keep only last 5 summaries to limit context size
        self.attempt_summaries = self.attempt_summaries[-5:]

    @property
    def attempt_summary_text(self) -> str:
        return "; ".join(self.attempt_summaries) if self.attempt_summaries else "First attempt."


# ── Main orchestrator ─────────────────────────────────────────────────────────

class Orchestrator:
    def __init__(self, cache: QueryCache, settings: Settings) -> None:
        self._cache = cache
        self._settings = settings

    async def run(self, question: str, user: dict) -> QueryResponse:
        """
        Execute the full multi-agent workflow and return a QueryResponse.
        user dict must contain: user_id, username, roles.
        """
        settings = self._settings

        # ── Step 1: Input validation ──────────────────────────────────────────
        question = question.strip()
        if not question:
            return self._error_response(question, "Question cannot be empty.")

        # ── Step 2: Introspect schema (needed for cache key + agents) ─────────
        try:
            tables = await asyncio.wait_for(
                get_schema(settings),
                timeout=settings.query_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.error("Schema introspection timed out for user=%s", user["user_id"])
            return self._error_response(question, "Database unavailable. Please try again.")
        except Exception as exc:
            logger.error("Schema introspection error: %s", exc)
            return self._error_response(question, "Database unavailable. Please try again.")

        s_hash = schema_hash(tables)
        schema_text = schema_to_text(tables)

        # ── Step 3-4: Cache check ─────────────────────────────────────────────
        cached = self._cache.get(question, user, s_hash)
        if cached is not None:
            logger.info("Cache hit for user=%s", user["user_id"])
            cached["cached"] = True
            return QueryResponse(**cached)

        # ── Step 5: Manager AI — create plan ──────────────────────────────────
        ctx = TaskContext(question=question, schema_text=schema_text)
        try:
            ctx.plan = await manager.create_plan(question, schema_text, settings)
        except Exception as exc:
            logger.error("Manager AI error: %s", exc)
            return self._error_response(question, "Could not understand your question. Please rephrase.")

        # Check if the manager determined the question is unanswerable
        if ctx.plan.steps and "cannot be answered" in ctx.plan.steps[0].lower():
            return self._error_response(question, ctx.plan.steps[0])

        # ── Step 6-11: Coder → Execute → Debug loop ───────────────────────────
        rows: list[dict[str, Any]] = []
        columns: list[str] = []
        succeeded = False

        while ctx.retry_count <= settings.max_retries:
            # Generate or fix SQL
            try:
                if ctx.retry_count == 0:
                    # First attempt: Coder AI
                    ctx.current_sql = await coder_agent.generate_sql(
                        ctx.plan, schema_text, settings
                    )
                else:
                    # Retry: Debugger AI
                    ctx.current_sql = await debugger.fix_sql(
                        ctx.plan,
                        ctx.current_sql or "",
                        ctx.last_error or "Unknown error",
                        schema_text,
                        ctx.attempt_summary_text,
                        settings,
                    )
            except Exception as exc:
                logger.error("Agent SQL generation error (attempt %d): %s", ctx.retry_count, exc)
                ctx.last_error = "SQL generation failed"
                ctx.retry_count += 1
                continue

            # Execute (includes pre-execution SQL validation)
            try:
                rows, columns = await asyncio.wait_for(
                    run_query(ctx.current_sql, settings),
                    timeout=settings.query_timeout_seconds,
                )
                succeeded = True
                break  # ← success

            except asyncio.TimeoutError:
                logger.warning("Query timeout (attempt %d) user=%s", ctx.retry_count, user["user_id"])
                ctx.last_error = "Query timed out"
                ctx.add_attempt_summary(ctx.current_sql or "", ctx.last_error)
                ctx.retry_count += 1

            except ValueError as exc:
                # Validation error — log but don't expose SQL details to user
                logger.warning("SQL validation error (attempt %d): %s", ctx.retry_count, exc)
                ctx.last_error = str(exc)
                ctx.add_attempt_summary(ctx.current_sql or "", ctx.last_error)
                ctx.retry_count += 1

            except Exception as exc:
                logger.error("DB execution error (attempt %d): %s", ctx.retry_count, exc)
                ctx.last_error = "Database query failed"
                ctx.add_attempt_summary(ctx.current_sql or "", ctx.last_error)
                ctx.retry_count += 1

        # ── Step 12: Retries exhausted ────────────────────────────────────────
        if not succeeded:
            logger.error(
                "All retries exhausted for user=%s question_len=%d retries=%d",
                user["user_id"], len(question), ctx.retry_count,
            )
            return QueryResponse(
                status=QueryStatus.FAILED,
                question=question,
                answer="",
                retry_count=ctx.retry_count,
                error="Could not complete the analysis after multiple attempts. Please try rephrasing your question.",
            )

        # ── Step 13: Reporter AI ──────────────────────────────────────────────
        try:
            answer = await reporter.generate_report(question, rows, columns, settings)
        except Exception as exc:
            logger.error("Reporter AI error: %s", exc)
            answer = f"Analysis complete. Found {len(rows)} result(s)."

        # ── Step 14: Cache successful result ──────────────────────────────────
        response_data = {
            "status": QueryStatus.SUCCESS,
            "question": question,
            "answer": answer,
            "table_data": rows,
            "columns": columns,
            "cached": False,
            "retry_count": ctx.retry_count,
            "error": None,
        }
        self._cache.set(question, user, s_hash, response_data)

        # ── Step 15: Return ───────────────────────────────────────────────────
        return QueryResponse(**response_data)

    @staticmethod
    def _error_response(question: str, message: str) -> QueryResponse:
        return QueryResponse(
            status=QueryStatus.FAILED,
            question=question,
            answer="",
            error=message,
        )
