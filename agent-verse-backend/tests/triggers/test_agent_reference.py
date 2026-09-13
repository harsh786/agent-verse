"""A trigger may reference an existing agent instead of carrying a goal template.

When an operator creates a goal-bearing agent separately and then a trigger that
just points at it, firing that trigger (on ANY trigger type / category) must:

  1. route the created goal to the referenced agent, and
  2. run the agent's own configured goal_template as the goal text.

These are unit tests for that wiring: the store binds the record-level refs onto
the spec (so every dispatch path sees them), and the dispatcher resolves + routes.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tenancy.context import PlanTier, TenantContext
from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore, bind_refs_to_spec


def _tenant() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


# ── store binds record-level refs onto the spec ───────────────────────────────


def test_bind_refs_sets_watch_agent_and_goal_template():
    spec = TriggerSpec(trigger_type=TriggerType.CRON)
    bind_refs_to_spec(spec, agent_id="agent-7", goal_template="do the thing")
    assert spec.watch_agent_id == "agent-7"
    assert spec.goal_template == "do the thing"


def test_bind_refs_does_not_override_explicit_spec_values():
    spec = TriggerSpec(
        trigger_type=TriggerType.CRON,
        watch_agent_id="explicit-agent",
        goal_template="explicit goal",
    )
    bind_refs_to_spec(spec, agent_id="agent-7", goal_template="record goal")
    assert spec.watch_agent_id == "explicit-agent"
    assert spec.goal_template == "explicit goal"


def test_store_create_makes_agent_only_spec_self_contained():
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.CRON)  # no goal_template
    sid = store.create(
        spec=spec, tenant_ctx=_tenant(), goal_id="", agent_id="agent-42", goal_template=""
    )
    rec = store.get(sid, tenant_ctx=_tenant())
    assert rec is not None
    # The referenced agent id is now readable off the spec itself, so every
    # dispatch path (consumers, beat, API) can route to it.
    assert rec["spec"].watch_agent_id == "agent-42"


def test_store_create_binds_trigger_id_for_idempotency():
    """The spec must carry the real schedule id as trigger_id so the dispatcher's
    idempotency key is per-trigger — otherwise distinct triggers dedup against
    each other as 'unknown'."""
    store = ScheduleStore()
    s1 = store.create(
        spec=TriggerSpec(trigger_type=TriggerType.CRON),
        tenant_ctx=_tenant(), goal_id="", agent_id="a1", goal_template="",
    )
    s2 = store.create(
        spec=TriggerSpec(trigger_type=TriggerType.CRON),
        tenant_ctx=_tenant(), goal_id="", agent_id="a1", goal_template="",
    )
    r1 = store.get(s1, tenant_ctx=_tenant())
    r2 = store.get(s2, tenant_ctx=_tenant())
    assert r1["spec"].trigger_id == s1
    assert r2["spec"].trigger_id == s2
    assert r1["spec"].trigger_id != r2["spec"].trigger_id


# ── dispatcher resolves the agent's goal + routes across categories ───────────


class _AgentStore:
    def __init__(self, goal_template: str) -> None:
        self._goal_template = goal_template

    def get(self, agent_id, tenant_ctx=None):
        return {"agent_id": agent_id, "goal_template": self._goal_template}


class _GoalService:
    """Captures how the fired goal was created."""

    def __init__(self, agent_store: _AgentStore) -> None:
        self._agent_store = agent_store
        self.created: dict[str, object] = {}

    def _get_agent_store(self):
        return self._agent_store

    async def create_goal(self, *, tenant_ctx, goal_text, agent_id, idempotency_key):
        self.created = {"goal_text": goal_text, "agent_id": agent_id}
        return SimpleNamespace(goal_id="goal-created")


@pytest.mark.parametrize(
    "trigger_type",
    [
        TriggerType.CRON,          # A: time/schedule
        TriggerType.GOAL_COMPLETED,  # B: goal chain
        TriggerType.WEBHOOK,       # E: external event
        TriggerType.DB_ROW_CHANGE,  # F: data/file
        TriggerType.MQTT,          # I: IoT
    ],
)
async def test_agent_only_trigger_routes_and_runs_agent_goal(trigger_type):
    """An agent-referencing trigger with NO goal template runs the agent's own
    goal and routes to that agent — regardless of the trigger category."""
    agent_goal = "Analyze the latest NovaCache telemetry and brief the team."
    goal_service = _GoalService(_AgentStore(agent_goal))
    dispatcher = TriggerDispatcher(goal_service=goal_service)

    # Mirror what the store produces: agent bound onto the spec, no goal template.
    spec = TriggerSpec(trigger_type=trigger_type)
    bind_refs_to_spec(spec, agent_id="agent-99", goal_template="")

    event = await dispatcher.dispatch(spec, {"k": "v"}, _tenant())

    assert event.goal_created is True
    assert event.goal_id == "goal-created"
    # Routed to the referenced agent …
    assert goal_service.created["agent_id"] == "agent-99"
    # … and ran the agent's own configured goal (not the generic default).
    assert goal_service.created["goal_text"] == agent_goal


async def test_explicit_goal_template_still_wins_over_agent_goal():
    goal_service = _GoalService(_AgentStore("agent's own goal"))
    dispatcher = TriggerDispatcher(goal_service=goal_service)

    spec = TriggerSpec(trigger_type=TriggerType.CRON)
    bind_refs_to_spec(spec, agent_id="agent-99", goal_template="explicit trigger goal")

    await dispatcher.dispatch(spec, {}, _tenant())
    assert goal_service.created["goal_text"] == "explicit trigger goal"
    assert goal_service.created["agent_id"] == "agent-99"
