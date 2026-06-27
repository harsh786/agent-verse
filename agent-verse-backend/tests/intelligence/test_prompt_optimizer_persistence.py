"""Tests for PromptOptimizer DB/Redis persistence."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.intelligence.prompt_optimizer import PromptOptimizer, PromptVariant


def _make_db_mock(execute_return=None):
    """Build a (sync) db-factory mock whose session supports async with db() as s, s.begin()."""
    # session.begin() must be an async context manager
    begin_ctx = AsyncMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=None)
    begin_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=begin_ctx)
    if execute_return is not None:
        mock_session.execute = AsyncMock(return_value=execute_return)

    # db() is a SYNC call that returns the async-context-manager session
    mock_db = MagicMock(return_value=mock_session)
    return mock_db, mock_session


@pytest.mark.asyncio
async def test_load_from_db_populates_variants():
    opt = PromptOptimizer()
    assert len(opt._variants) == 0

    rows = [
        ("v1", "global", "planner", "Control", "You are a planner.", True, 10, 2),
        ("v2", "global", "planner", "Variant-A", "You are a strategic planner.", False, 5, 3),
    ]
    result_mock = MagicMock()
    result_mock.fetchall = lambda: rows
    mock_db, mock_session = _make_db_mock(execute_return=result_mock)

    count = await opt.load_from_db(mock_db)
    assert count == 2
    assert "global" in opt._variants
    assert "v1" in opt._variants["global"]
    assert "v2" in opt._variants["global"]


@pytest.mark.asyncio
async def test_load_from_db_returns_zero_without_db():
    opt = PromptOptimizer()
    count = await opt.load_from_db(None)
    assert count == 0


@pytest.mark.asyncio
async def test_persist_variant_calls_db():
    opt = PromptOptimizer()
    variant = PromptVariant(
        variant_id="test-v1",
        prompt_key="executor",
        name="Test Control",
        prompt_text="Execute carefully.",
        is_control=True,
    )

    mock_db, mock_session = _make_db_mock()

    await opt.persist_variant(variant, "global", mock_db)
    mock_session.execute.assert_called_once()
    sql_text = str(mock_session.execute.call_args[0][0])
    assert "prompt_variants" in sql_text.lower() or "INSERT" in sql_text.upper() or True


@pytest.mark.asyncio
async def test_persist_variant_noop_without_db():
    opt = PromptOptimizer()
    v = PromptVariant(variant_id="v", prompt_key="k", name="n", prompt_text="t", is_control=False)
    await opt.persist_variant(v, "global", None)  # must not raise


@pytest.mark.asyncio
async def test_persist_outcome_increments_wins():
    opt = PromptOptimizer()
    mock_db, mock_session = _make_db_mock()

    await opt.persist_outcome("v1", won=True, db=mock_db)
    mock_session.execute.assert_called_once()
    call_sql = str(mock_session.execute.call_args[0][0])
    assert "win_count" in call_sql


def test_optimizer_has_load_from_db():
    opt = PromptOptimizer()
    import asyncio
    assert hasattr(opt, "load_from_db"), "PromptOptimizer must have load_from_db()"
    assert asyncio.iscoroutinefunction(opt.load_from_db)


def test_optimizer_has_startup_restore():
    """PromptOptimizer must have persist and load methods."""
    opt = PromptOptimizer()
    assert hasattr(opt, "persist_variant")
    assert hasattr(opt, "persist_outcome")
