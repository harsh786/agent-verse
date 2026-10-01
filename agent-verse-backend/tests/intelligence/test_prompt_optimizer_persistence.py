"""Tests for PromptOptimizer DB/Redis persistence."""
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

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


def test_legacy_non_rls_persistence_is_gone():
    """MEM-28: persist_variant / persist_outcome / load_from_db wrote and read
    prompt_variants without the tenant RLS context and swallowed errors. The
    DB mode (set_db + a* methods) is the only durable path."""
    opt = PromptOptimizer()
    for name in ("persist_variant", "persist_outcome", "load_from_db"):
        assert not hasattr(opt, name)


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


def test_add_variant_refuses_legacy_db_persistence():
    opt = PromptOptimizer()
    with pytest.raises(ValueError):
        opt.add_variant(_make_variant("v-persist"), "t1", db=MagicMock())
    with pytest.raises(ValueError):
        opt.register_variant("planner", "n", "t", tenant_id="t1", db=MagicMock())


def test_add_variant_non_control_does_not_set_active():
    """A non-control variant must NOT overwrite the active-variant pointer."""
    opt = PromptOptimizer()
    ctrl = _make_variant("ctrl-id", key="executor", is_control=True)
    challenger = _make_variant("chall-id", key="executor", is_control=False)
    opt.add_variant(ctrl, "t2", db=None)
    opt.add_variant(challenger, "t2", db=None)
    # Active pointer should still point to the control
    assert opt._active.get("t2", {}).get("executor") == "ctrl-id"

