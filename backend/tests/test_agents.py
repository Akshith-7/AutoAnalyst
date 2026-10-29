"""
test_agents.py — Unit tests for individual agents with mocked LLM calls.
"""
import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from backend.config import Settings
from backend.models import ExecutionPlan
from backend.agents import manager, coder, debugger, reporter


@pytest.fixture
def settings():
    return Settings(
        openai_api_key="test-key",
        database_url="sqlite+aiosqlite:///./test.db",
        jwt_secret_key="test",
        openai_model="gpt-4o-mini",
    )


SCHEMA_TEXT = "Table `sales`: id (INTEGER), product (TEXT), revenue (FLOAT)"
SAMPLE_PLAN = ExecutionPlan(
    analysis_goal="Find total revenue per product",
    relevant_tables=["sales"],
    constraints=[],
    steps=["Group sales by product", "Sum revenue"],
)


# ── Manager AI ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_manager_creates_plan(settings):
    mock_response = json.dumps({
        "analysis_goal": "Find total revenue per product",
        "relevant_tables": ["sales"],
        "constraints": [],
        "steps": ["Group by product", "Sum revenue"],
    })
    with patch("backend.agents.manager.call_llm", new=AsyncMock(return_value=mock_response)):
        plan = await manager.create_plan("What is revenue by product?", SCHEMA_TEXT, settings)
    assert isinstance(plan, ExecutionPlan)
    assert "sales" in plan.relevant_tables
    assert plan.analysis_goal


@pytest.mark.asyncio
async def test_manager_invalid_json_raises(settings):
    with patch("backend.agents.manager.call_llm", new=AsyncMock(return_value="not json")):
        with pytest.raises(ValueError, match="invalid JSON"):
            await manager.create_plan("revenue?", SCHEMA_TEXT, settings)


@pytest.mark.asyncio
async def test_manager_missing_fields_raises(settings):
    """Missing required Pydantic fields should raise ValueError."""
    incomplete = json.dumps({"analysis_goal": "goal"})  # missing required fields
    with patch("backend.agents.manager.call_llm", new=AsyncMock(return_value=incomplete)):
        with pytest.raises(ValueError):
            await manager.create_plan("revenue?", SCHEMA_TEXT, settings)


# ── Coder AI ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_coder_generates_select(settings):
    mock_sql = "SELECT product, SUM(revenue) FROM sales GROUP BY product"
    with patch("backend.agents.coder.call_llm", new=AsyncMock(return_value=mock_sql)):
        sql = await coder.generate_sql(SAMPLE_PLAN, SCHEMA_TEXT, settings)
    assert sql.strip().upper().startswith("SELECT")


@pytest.mark.asyncio
async def test_coder_strips_markdown_fences(settings):
    mock_sql = "```sql\nSELECT product, SUM(revenue) FROM sales GROUP BY product\n```"
    with patch("backend.agents.coder.call_llm", new=AsyncMock(return_value=mock_sql)):
        sql = await coder.generate_sql(SAMPLE_PLAN, SCHEMA_TEXT, settings)
    assert "```" not in sql
    assert sql.strip().upper().startswith("SELECT")


@pytest.mark.asyncio
async def test_coder_raises_on_non_select(settings):
    """Coder must raise if LLM returns a non-SELECT statement."""
    with patch("backend.agents.coder.call_llm", new=AsyncMock(return_value="DROP TABLE sales")):
        with pytest.raises(ValueError, match="SELECT"):
            await coder.generate_sql(SAMPLE_PLAN, SCHEMA_TEXT, settings)


# ── Debugger AI ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_debugger_returns_corrected_sql(settings):
    corrected = "SELECT product, SUM(revenue) as total FROM sales GROUP BY product"
    with patch("backend.agents.debugger.call_llm", new=AsyncMock(return_value=corrected)):
        sql = await debugger.fix_sql(
            SAMPLE_PLAN,
            "SELECT prduct FROM sales",
            "no such column: prduct",
            SCHEMA_TEXT,
            "Attempt 1: typo in column name",
            settings,
        )
    assert sql.strip().upper().startswith("SELECT")
    assert "prduct" not in sql


@pytest.mark.asyncio
async def test_debugger_raises_on_non_select(settings):
    with patch("backend.agents.debugger.call_llm", new=AsyncMock(return_value="DELETE FROM sales")):
        with pytest.raises(ValueError, match="SELECT"):
            await debugger.fix_sql(SAMPLE_PLAN, "", "", SCHEMA_TEXT, "", settings)


# ── Reporter AI ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reporter_generates_answer(settings):
    rows = [{"product": "Widget A", "total": 2600.0}]
    columns = ["product", "total"]
    expected_answer = "Widget A had the highest revenue at $2,600."
    with patch("backend.agents.reporter.call_llm", new=AsyncMock(return_value=expected_answer)):
        answer = await reporter.generate_report("top product?", rows, columns, settings)
    assert isinstance(answer, str)
    assert len(answer) > 0


@pytest.mark.asyncio
async def test_reporter_handles_empty_data(settings):
    with patch("backend.agents.reporter.call_llm", new=AsyncMock(return_value="No results found.")):
        answer = await reporter.generate_report("anything", [], [], settings)
    assert "No results" in answer
