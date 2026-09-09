"""ABTestingEngine must persist results to DB."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.optimization.ab_testing import ABTestingEngine, ExperimentType


def test_ab_engine_record_result_in_memory():
    """record_result() still writes in-memory (backward compat)."""
    engine = ABTestingEngine()
    engine.record_result("g1", ExperimentType.MODEL_ROUTING, "variant_a", 0.9)
    stats = engine.get_arm_stats(ExperimentType.MODEL_ROUTING, "variant_a")
    assert stats["call_count"] == 1
    assert stats["avg_score"] == 0.9


async def test_ab_engine_record_result_async_writes_to_db():
    """record_result_async() must call DB execute."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock()
    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=mock_begin)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_begin)
    db_factory = MagicMock(return_value=mock_session)

    engine = ABTestingEngine(db_factory=db_factory)
    await engine.record_result_async(
        "g1", ExperimentType.RAG_STRATEGY, "variant_a", 0.85,
        tenant_id="t1",
    )

    assert mock_session.execute.called


async def test_ab_engine_record_result_async_no_db_does_not_raise():
    """record_result_async() without DB must complete silently."""
    engine = ABTestingEngine()
    # Must not raise
    await engine.record_result_async(
        "g1", ExperimentType.PLANNER_PROMPT, "control", 0.7
    )
    stats = engine.get_arm_stats(ExperimentType.PLANNER_PROMPT, "control")
    assert stats["call_count"] == 1


def test_ab_engine_has_db_factory_param():
    """ABTestingEngine.__init__ must accept db_factory."""
    import inspect
    sig = inspect.signature(ABTestingEngine.__init__)
    assert "db_factory" in sig.parameters
