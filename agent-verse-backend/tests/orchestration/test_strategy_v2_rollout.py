"""STRATEGY-V2-ROLLOUT: allowlist "*" and never-silent override downgrades.

An explicit ``strategy_override`` only runs on the strategy runtime v2, which is
enabled per tenant by ``STRATEGY_RUNTIME_V2_TENANT_ALLOWLIST``. With an empty
allowlist every override quietly ran the legacy kernel instead: only a
``runtime_profile_fallback`` record deep in execution_context said so. Now:

* ``*`` in the allowlist enables v2 for every tenant (kill switch / shadow
  still win);
* a downgraded override is logged (``strategy_override_downgraded``) and the
  goal carries ``strategy_downgraded: true`` + the reason in its submit
  result, its ``GET /goals/{id}`` payload and a ``strategy_downgraded`` event.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.runtime_flags import get_runtime_flags
from app.orchestration.strategy_certification import RolloutController
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-rollout", plan=PlanTier.ENTERPRISE, api_key_id="k")


@pytest.fixture(autouse=True)
def _fresh_flags() -> Any:
    get_runtime_flags.cache_clear()
    yield
    get_runtime_flags.cache_clear()


def test_star_allowlist_enables_v2_for_every_tenant() -> None:
    star = RolloutController(allowlist=frozenset({"*"}))
    assert star.choose("any-tenant", {}, {}).path == "v2"
    assert RolloutController(allowlist=frozenset()).choose("any-tenant", {}, {}).path == "legacy"
    assert RolloutController(allowlist=frozenset({"t1"})).choose("t2", {}, {}).path == "legacy"
    # The kill switch and shadow mode still win over "*".
    assert (
        RolloutController(allowlist=frozenset({"*"}), kill_switch=True).choose("x", {}, {}).path
        == "rejected"
    )
    assert (
        RolloutController(allowlist=frozenset({"*"}), shadow=True).choose("x", {}, {}).path
        == "legacy"
    )


def test_env_star_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRATEGY_RUNTIME_V2_TENANT_ALLOWLIST", "*")
    assert "*" in get_runtime_flags().strategy_runtime_v2_tenant_allowlist


async def _submit(svc: GoalService, strategy: str) -> dict[str, Any]:
    return await svc.submit_goal(
        goal="Summarise the onboarding guide",
        priority="normal",
        dry_run=True,
        tenant_ctx=CTX,
        execution_context={"strategy_runtime": {"primary_strategy": strategy}},
    )


@pytest.mark.asyncio
async def test_downgraded_override_is_reported_on_result_goal_and_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STRATEGY_RUNTIME_V2_TENANT_ALLOWLIST", raising=False)
    warnings: list[tuple[str, dict[str, Any]]] = []
    import app.services.goal_service as gs_mod

    real_warning = gs_mod._svc_logger.warning

    def _spy(event: str, *args: Any, **kwargs: Any) -> Any:
        warnings.append((event, kwargs))
        return real_warning(event, *args, **kwargs)

    monkeypatch.setattr(gs_mod._svc_logger, "warning", _spy)
    svc = GoalService()
    result = await _submit(svc, "plan_execute")

    assert result["strategy_downgraded"] is True
    assert result["strategy_downgrade"]["reason"] == "strategy_runtime_v2_not_enabled"
    assert result["strategy_downgrade"]["requested_strategy"] == "plan_execute"
    assert "STRATEGY_RUNTIME_V2_TENANT_ALLOWLIST" in result["strategy_downgrade"]["detail"]

    goal = await svc.get_goal(result["goal_id"], CTX)
    assert goal["strategy_downgraded"] is True
    assert goal["strategy_downgrade"]["reason"] == "strategy_runtime_v2_not_enabled"

    events = await svc.get_events(result["goal_id"], CTX)
    downgrade = [e for e in events if e.get("type") == "strategy_downgraded"]
    assert len(downgrade) == 1
    assert downgrade[0]["strategy_downgraded"] is True
    assert downgrade[0]["reason"] == "strategy_runtime_v2_not_enabled"

    logged = [kw for name, kw in warnings if name == "strategy_override_downgraded"]
    assert logged and logged[0]["requested_strategy"] == "plan_execute"


@pytest.mark.asyncio
async def test_star_allowlist_runs_the_override_without_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRATEGY_RUNTIME_V2_TENANT_ALLOWLIST", "*")
    svc = GoalService()
    result = await _submit(svc, "plan_execute")
    assert result["strategy_downgraded"] is False
    assert result.get("strategy_downgrade") is None
    events = await svc.get_events(result["goal_id"], CTX)
    assert not [e for e in events if e.get("type") == "strategy_downgraded"]


@pytest.mark.asyncio
async def test_goal_without_override_is_not_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STRATEGY_RUNTIME_V2_TENANT_ALLOWLIST", raising=False)
    svc = GoalService()
    result = await svc.submit_goal(
        goal="Summarise the onboarding guide", priority="normal", dry_run=True, tenant_ctx=CTX
    )
    assert result["strategy_downgraded"] is False
