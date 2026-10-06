from __future__ import annotations

import importlib.util

import pytest

from app.lifecycle.deletion_orchestrator import DeletionOrchestrator
from app.lifecycle.retention_policy import DataCategory


def test_deletion_orchestrator():
    orch = DeletionOrchestrator()
    result = orch.schedule_deletion("t1", DataCategory.GOAL_ARTIFACT, ["g1", "g2"])
    assert result.scheduled_count == 2 and result.tenant_id == "t1"


@pytest.mark.parametrize(
    "module",
    [
        "app.lifecycle.archive_policy",
        "app.lifecycle.export_policy",
        "app.lifecycle.legal_hold_policy",
    ],
)
def test_dead_policy_modules_are_gone(module: str) -> None:
    """a10-F245-01: in-process policy classes no code consulted (the real legal
    hold is the ``legal_holds`` table the deletion orchestrator checks)."""
    assert importlib.util.find_spec(module) is None


def test_retention_policy_keeps_only_the_live_category_enum() -> None:
    import app.lifecycle.retention_policy as rp

    assert not hasattr(rp, "RetentionPolicy")
    assert not hasattr(rp, "RetentionTier")
