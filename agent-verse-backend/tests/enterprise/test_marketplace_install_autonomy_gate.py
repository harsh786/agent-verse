"""MARKETPLACE-EVAL-GATE: a template install cannot mint a fully-autonomous agent.

MEM-22 made ``fully-autonomous`` reachable only through the rollout gate: POST
/agents and PUT /agents/{id} require an eval suite whose latest completed run
passes (409 ROLLOUT_GATE_FAILED otherwise). ``MarketplaceV2.install`` wrote the
template's ``autonomy_mode`` straight into ``agents`` — so installing a
fully-autonomous template produced a fully-autonomous agent with no eval suite
and no gate.

Behaviour (consistent with MEM-22 and the NL-create rule): the install
succeeds but the agent is created ``bounded-autonomous``; the response reports
the requested mode and why. Upgrading it to fully-autonomous is a PUT
/agents/{id}, which enforces the rollout gate.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.enterprise.marketplace_v2 import MarketplaceV2
from app.tenancy.context import PlanTier, TenantContext

T_A = TenantContext(tenant_id="tenant-a", plan=PlanTier.ENTERPRISE, api_key_id="ka")


def _template(mode: str) -> dict[str, Any]:
    return {
        "template_id": f"tpl-{mode}",
        "name": "Autonomy Test",
        "slug": f"autonomy-test-{mode}",
        "domain": "testing",
        "description": "autonomy gate test",
        "required_connectors": [],
        "autonomy_mode": mode,
        "template_config": {"goal_template": "Run {thing}", "autonomy_mode": mode},
        "parameters_schema": {},
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": False,
        "version": "1.0.0",
    }


@pytest.mark.asyncio
async def test_fully_autonomous_template_installs_bounded_in_memory() -> None:
    svc = MarketplaceV2(db_factory=None)
    svc._cache["tpl-fully-autonomous"] = _template("fully-autonomous")

    result = await svc.install(template_id="tpl-fully-autonomous", params={}, tenant_ctx=T_A)

    assert result["success"] is True, result
    assert result["autonomy_mode"] == "bounded-autonomous"
    assert result["requested_autonomy_mode"] == "fully-autonomous"
    assert result["autonomy_downgraded"] is True
    assert "rollout gate" in result["autonomy_note"]
    assert svc._installs[0]["autonomy_mode"] == "bounded-autonomous"


@pytest.mark.asyncio
async def test_other_modes_are_installed_as_requested() -> None:
    svc = MarketplaceV2(db_factory=None)
    svc._cache["tpl-supervised"] = _template("supervised")
    result = await svc.install(template_id="tpl-supervised", params={}, tenant_ctx=T_A)
    assert result["autonomy_mode"] == "supervised"
    assert result["autonomy_downgraded"] is False


@pytest.mark.asyncio
async def test_db_install_writes_bounded_agent_row() -> None:
    executed: list[tuple[str, dict[str, Any]]] = []
    session = AsyncMock()

    async def _execute(stmt: Any, params: dict[str, Any] | None = None) -> Any:
        executed.append((str(stmt), dict(params or {})))
        res = MagicMock()
        res.scalar_one = MagicMock(return_value="agent-1")
        res.scalar = MagicMock(return_value=1)
        res.rowcount = 1
        return res

    session.execute = _execute
    session.commit = AsyncMock()

    @asynccontextmanager
    async def _db() -> Any:
        yield session

    svc = MarketplaceV2(db_factory=_db)
    svc._cache["tpl-fully-autonomous"] = _template("fully-autonomous")
    svc.get_template = AsyncMock(return_value=_template("fully-autonomous"))  # type: ignore[method-assign]
    svc._require_counter_update = AsyncMock()  # type: ignore[method-assign]

    result = await svc.install(template_id="tpl-fully-autonomous", params={}, tenant_ctx=T_A)

    assert result["success"] is True, result
    agent_inserts = [p for sql, p in executed if "INSERT INTO agents" in sql]
    assert agent_inserts and agent_inserts[0]["mode"] == "bounded-autonomous"
    assert result["requested_autonomy_mode"] == "fully-autonomous"
