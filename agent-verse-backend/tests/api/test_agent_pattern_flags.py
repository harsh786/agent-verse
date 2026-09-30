"""CORE-04: an agent's reasoning-pattern flags can be set, persist, and reach the graph.

enable_cot / enable_reflection / ... / enable_debate were not accepted by the
create/update API, had no column, and were dropped by ``_row_to_dict`` — so the
goal's ``agent_pattern_flags`` snapshot was always empty and those nodes never
ran outside the rollout-gated v2 profile. The snapshot also read the
process-local AgentStore under ``suppress(Exception)``, so an agent created on
another replica silently lost its flags (and was even rejected as unknown).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.pattern_flags import AGENT_PATTERN_FLAG_KEYS
from app.api.agents import AgentStore
from app.api.agents import router as agents_router
from app.services.dedup import GoalDeduplicator
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-flags", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_KEY = "av_test_flagskey"
_H = {"X-API-Key": _KEY}


def _client(store: AgentStore | None = None) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(agents_router)
    app.state.agent_store = store or AgentStore()
    app.state.meta_agent = AsyncMock()
    return TestClient(app, raise_server_exceptions=False)


def test_create_get_update_round_trip_pattern_flags() -> None:
    client = _client()
    resp = client.post(
        "/agents",
        json={"name": "thinker", "enable_cot": True, "enable_debate": True},
        headers=_H,
    )
    assert resp.status_code == 201, resp.text
    agent_id = resp.json()["agent_id"]

    got = client.get(f"/agents/{agent_id}", headers=_H).json()
    assert got["enable_cot"] is True
    assert got["enable_debate"] is True
    assert got["enable_supervisor"] is False
    assert got["pattern_flags"]["enable_cot"] is True

    upd = client.put(f"/agents/{agent_id}", json={"enable_cot": False}, headers=_H)
    assert upd.status_code == 200, upd.text
    body = upd.json()
    assert body["enable_cot"] is False
    assert body["enable_debate"] is True  # untouched flags are kept
    assert body["pattern_flags"] == {k: k == "enable_debate" for k in AGENT_PATTERN_FLAG_KEYS}


def test_row_to_dict_returns_persisted_pattern_flags() -> None:
    row = SimpleNamespace(
        id="a1",
        tenant_id="t1",
        name="n",
        goal_template="",
        autonomy_mode="supervised",
        connector_ids=[],
        trigger_config={},
        pattern_flags={"enable_self_refine": True, "bogus": True},
        created_at=None,
    )
    rec = AgentStore._row_to_dict(row)
    assert rec["enable_self_refine"] is True
    assert rec["enable_cot"] is False
    assert "bogus" not in rec["pattern_flags"]


def test_clone_carries_pattern_flags() -> None:
    client = _client()
    agent_id = client.post(
        "/agents", json={"name": "src", "enable_peer_review": True}, headers=_H
    ).json()["agent_id"]
    clone = client.post(f"/agents/{agent_id}/clone", headers=_H)
    assert clone.status_code == 201, clone.text
    assert clone.json()["pattern_flags"]["enable_peer_review"] is True


class _DbOnlyStore:
    """An agent that exists in Postgres but not in this replica's cache."""

    def __init__(self, record: dict[str, Any]) -> None:
        self._record = record

    def get(self, agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
        return None  # stale replica cache

    async def get_async(self, agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
        return self._record if agent_id == self._record["agent_id"] else None


async def test_submit_snapshots_flags_from_the_db_backed_agent() -> None:
    record = {"agent_id": "agent-db", "name": "db", "enable_cot": True, "enable_debate": True}
    svc = GoalService(task_queue=MagicMock())
    svc._agent_store = _DbOnlyStore(record)
    with (
        patch("app.services.dedup._default_deduplicator", GoalDeduplicator()),
        patch("app.tenancy.limits.check_and_increment_concurrent_goals", AsyncMock()),
    ):
        result = await svc.submit_goal(
            goal="think hard about it",
            priority="normal",
            dry_run=False,
            tenant_ctx=_CTX,
            agent_id="agent-db",
        )
    ctx = svc._goals[result["goal_id"]].execution_context
    assert ctx["agent_pattern_flags"] == {"enable_cot": True, "enable_debate": True}


def test_in_process_graph_gets_every_snapshotted_flag() -> None:
    flags = dict.fromkeys(AGENT_PATTERN_FLAG_KEYS, True)
    loop = GoalService()._make_agent_loop_for_tenant(
        _CTX, None, execution_context={"agent_pattern_flags": flags}
    )
    for key in AGENT_PATTERN_FLAG_KEYS:
        assert getattr(loop, f"_{key}") is True, key


@pytest.mark.parametrize("key", AGENT_PATTERN_FLAG_KEYS)
def test_every_flag_is_accepted_by_the_create_api(key: str) -> None:
    client = _client()
    resp = client.post("/agents", json={"name": f"a-{key}", key: True}, headers=_H)
    assert resp.status_code == 201, resp.text
    assert resp.json()["pattern_flags"][key] is True
