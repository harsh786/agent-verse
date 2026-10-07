"""PROV-18: model overrides and role maps respect plan tier caps and budget downgrades.

Agent/goal overrides, the role map and the cheapest-configured choice returned
before tier assignment, so a free-plan tenant could pin an expensive model and
budget-driven downgrades never applied to it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.ai_router.model_orchestrator import ModelOrchestratorAdapter, model_quality_tier


pytestmark = pytest.mark.usefixtures("tiered_registry")


def test_model_quality_tier_follows_the_single_pricing_source() -> None:
    assert model_quality_tier("gpt-5.2-pro") == "high"
    assert model_quality_tier("gpt-4o") == "medium"
    assert model_quality_tier("gpt-4o-mini") == "low"


def test_free_plan_enterprise_override_is_clamped() -> None:
    adapter = ModelOrchestratorAdapter().with_override("gpt-5.2-pro")
    adapter.set_plan_tier("free")
    chosen = adapter.model_for("execution")
    assert chosen != "gpt-5.2-pro"
    assert model_quality_tier(chosen) == "low"


def test_enterprise_plan_override_is_kept() -> None:
    adapter = ModelOrchestratorAdapter().with_override("gpt-5.2-pro")
    adapter.set_plan_tier("enterprise")
    assert adapter.model_for("execution") == "gpt-5.2-pro"


def test_role_map_model_is_capped_too() -> None:
    adapter = ModelOrchestratorAdapter()
    adapter.set_role_map({"planner": "gpt-5.2"})
    adapter.set_plan_tier("starter")
    assert model_quality_tier(adapter.model_for("planning")) in ("low", "medium")


def test_budget_downgrade_applies_to_overrides() -> None:
    adapter = ModelOrchestratorAdapter().with_override("gpt-5.2")
    adapter.set_plan_tier("professional")
    adapter._last_budget_ratio = 0.95
    assert model_quality_tier(adapter.model_for("execution")) == "low"


def test_plan_tier_survives_with_override() -> None:
    adapter = ModelOrchestratorAdapter()
    adapter.set_plan_tier("free")
    clone = adapter.with_override("gpt-5.2-pro")
    assert model_quality_tier(clone.model_for("execution")) == "low"


async def test_plan_cap_endpoint_reports_clamping() -> None:
    from app.api.model_registry import model_plan_cap

    request = MagicMock()
    request.state = SimpleNamespace(
        tenant=SimpleNamespace(tenant_id="t", plan=SimpleNamespace(value="free"))
    )
    out: dict[str, Any] = await model_plan_cap(request, model_id="gpt-5.2-pro")
    assert out == {
        "model_id": "gpt-5.2-pro", "model_tier": "high", "plan": "free",
        "plan_cap": "low", "clamped": True,
    }
