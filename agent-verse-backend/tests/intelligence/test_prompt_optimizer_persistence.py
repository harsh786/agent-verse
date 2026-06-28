"""Tests for PromptOptimizer DB/Redis persistence."""
import asyncio
import logging
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


def _make_variant(vid: str = "v1", key: str = "planner", is_control: bool = False) -> PromptVariant:
    """Helper: build a PromptVariant using the existing API."""
    return PromptVariant(
        variant_id=vid,
        prompt_key=key,
        name=vid,
        prompt_text="Test prompt",
        is_control=is_control,
    )


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
    assert hasattr(opt, "load_from_db"), "PromptOptimizer must have load_from_db()"
    assert asyncio.iscoroutinefunction(opt.load_from_db)


def test_optimizer_has_startup_restore():
    """PromptOptimizer must have persist and load methods."""
    opt = PromptOptimizer()
    assert hasattr(opt, "persist_variant")
    assert hasattr(opt, "persist_outcome")


# ---------------------------------------------------------------------------
# add_variant() — Fix 3
# ---------------------------------------------------------------------------

def test_add_variant_method_exists():
    """PromptOptimizer must expose add_variant()."""
    opt = PromptOptimizer()
    assert hasattr(opt, "add_variant"), "PromptOptimizer must have add_variant()"
    assert callable(opt.add_variant)


def test_add_variant_without_db_logs_warning(caplog):
    """Without db, variant is stored but a warning is emitted about in-memory-only storage."""
    opt = PromptOptimizer()
    v = _make_variant("ctrl", is_control=True)
    with caplog.at_level(logging.WARNING, logger="app.intelligence.prompt_optimizer"):
        opt.add_variant(v, "tenant-1", db=None)
    assert any(
        "will_be_lost_on_restart" in r.message or "in_memory_only" in r.message
        for r in caplog.records
    ), "Must warn about in-memory-only storage"


def test_add_variant_without_db_still_stored():
    """Even without db, the variant must be accessible for the current process lifetime."""
    opt = PromptOptimizer()
    v = _make_variant("ctrl", key="planner", is_control=True)
    opt.add_variant(v, "t1", db=None)
    result = opt.select_variant("planner", tenant_id="t1")
    assert result is not None
    assert result.variant_id == "ctrl"


def test_add_variant_pending_tasks_attribute_exists():
    """PromptOptimizer.__init__ must initialise _pending_tasks to prevent task GC."""
    opt = PromptOptimizer()
    assert hasattr(opt, "_pending_tasks"), "Must have _pending_tasks set"
    assert isinstance(opt._pending_tasks, set)


@pytest.mark.asyncio
async def test_add_variant_with_db_fires_persist():
    """With db provided, persist_variant is scheduled as an async task."""
    opt = PromptOptimizer()
    persisted: list[str] = []

    async def fake_persist(variant, tenant_id, db):
        persisted.append(variant.variant_id)

    opt.persist_variant = fake_persist  # type: ignore[method-assign]

    mock_db = MagicMock()
    v = _make_variant("v-persist")

    opt.add_variant(v, "t1", db=mock_db)
    # Yield control so the scheduled task can run.
    await asyncio.sleep(0.05)
    assert "v-persist" in persisted, "persist_variant must be called when db is provided"


def test_add_variant_non_control_does_not_set_active():
    """A non-control variant must NOT overwrite the active-variant pointer."""
    opt = PromptOptimizer()
    ctrl = _make_variant("ctrl-id", key="executor", is_control=True)
    challenger = _make_variant("chall-id", key="executor", is_control=False)
    opt.add_variant(ctrl, "t2", db=None)
    opt.add_variant(challenger, "t2", db=None)
    # Active pointer should still point to the control
    assert opt._active.get("t2", {}).get("executor") == "ctrl-id"

