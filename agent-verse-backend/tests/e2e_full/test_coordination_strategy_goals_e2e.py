"""e2e_full (COORD-STRATEGIES-RUNNER): a goal selecting any runner strategy runs end to end.

Through the booted app (real Postgres + Redis, goals inline, strategy runtime
v2 enabled for the tenant): ``POST /goals`` with ``strategy_override`` for each
coordination pattern (magentic, mixture_of_agents, camel, generative_agents,
decentralized_swarm, market_auction, group_chat) and for voyager reaches
``DistributedStrategyLoop -> StrategyRunner -> executor`` and completes — not
refused at admission and not downgraded to the local kernel. The coordination
patterns (now including group_chat) run on a goal-linked coordination session;
voyager publishes its skill into the Postgres skill library. A strategy with no
goal driver (rewoo) is a 422 naming why and listing the runnable strategies.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from tests.coordination.pattern_run_support import ScriptedProvider

from .conftest import collect_sse, wait_for_status

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_COORDINATION = (
    "magentic",
    "mixture_of_agents",
    "camel",
    "generative_agents",
    "decentralized_swarm",
    "market_auction",
    "group_chat",
)


@pytest.fixture
def _inline_v2(app: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.runtime_flags import get_runtime_flags

    gs = app.state.goal_service
    prev_override = getattr(app.state, "_llm_provider_override", None)
    prev_queue = gs._task_queue
    app.state._llm_provider_override = ScriptedProvider()
    gs._task_queue = None
    # v2 for every tenant this test creates: the env allowlist is read on demand.
    monkeypatch.setattr(
        "app.orchestration.strategy_certification.RolloutController.choose",
        _always_v2,
    )
    get_runtime_flags.cache_clear()
    try:
        yield
    finally:
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue
        get_runtime_flags.cache_clear()


def _always_v2(self: Any, tenant_id: str, legacy: Any, candidate: Any) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(path="v2", shadow_comparison=None)


def _types(raw_events: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in raw_events:
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


@pytest.mark.parametrize("strategy_id", [*_COORDINATION, "voyager"])
async def test_goal_selecting_the_strategy_runs_end_to_end(
    tenant_client: Any, _inline_v2: None, strategy_id: str
) -> None:
    submit = await tenant_client.post(
        "/goals",
        json={
            "goal": f"Write a short market report ({uuid.uuid4().hex[:6]})",
            "strategy_override": strategy_id,
        },
    )
    assert submit.status_code == 202, submit.text
    goal_id = submit.json()["goal_id"]

    final = await wait_for_status(
        tenant_client, goal_id, {"complete", "failed"}, timeout=60.0
    )
    events = _types(await collect_sse(tenant_client, goal_id, until="goal_", timeout=15.0))
    assert final["status"] == "complete", (final, events[-3:])
    done = next(e for e in events if e.get("type") == "goal_complete")
    assert done.get("strategy_id") == strategy_id, done
    assert done.get("execution_tier") == "distributed", done

    selection = await tenant_client.get(f"/goals/{goal_id}/pattern-selection")
    assert selection.status_code == 200, selection.text
    assert selection.json()["primary_pattern"] == strategy_id

    if strategy_id in _COORDINATION:
        assert any(e.get("type") == "coordination_session" for e in events), events
        assert any(e.get("type") == "coordination_progress" for e in events), events


async def test_strategy_without_a_goal_driver_is_a_422_that_says_why(
    tenant_client: Any, _inline_v2: None
) -> None:
    resp = await tenant_client.post(
        "/goals", json={"goal": "Plan the launch", "strategy_override": "rewoo"}
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "INVALID_STRATEGY"
    assert "rewoo" in detail["message"] and "no goal execution driver" in detail["message"]
    assert {"supervisor", "group_chat", "voyager"} <= set(detail["valid_strategies"])
