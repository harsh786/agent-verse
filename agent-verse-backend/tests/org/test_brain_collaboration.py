"""Task 9 — ``CollaborationTick`` ("the team talks"): capped, low-cost lead
chatter emitted as ``org.collaboration.message`` events.

Fully faked ``model_gateway``/``event_publisher``/``counters`` — no real LLM
calls, no DB, no Redis. Pins the guards (disabled / autonomy < 3 /
collaboration-budget-exhausted -> 0, nothing published, model never called),
the cap (``collab_messages_per_tick`` bounds emitted messages regardless of
how many leads are supplied), the fail-closed behavior (a model error
mid-tick stops the tick immediately; whatever was already emitted stays
emitted), and (Fix 2) that ``collaboration_daily_budget_usd`` is a real,
separately-tracked cap rather than a dead setting.
"""

from __future__ import annotations

import pytest

from app.org.brain_collaboration import (
    EVENT_TYPE_COLLABORATION_MESSAGE,
    CollaborationTick,
)
from app.org.brain_settings import AutonomySettings


def _settings(
    *,
    collaboration_enabled: bool = True,
    daily_budget_usd: float = 10.0,
    collab_messages_per_tick: int = 3,
    collaboration_daily_budget_usd: float = 1.0,
) -> AutonomySettings:
    return AutonomySettings(
        paused=False,
        cadence_seconds=300,
        min_interval_seconds=600,
        max_concurrent=2,
        max_missions_per_day=8,
        daily_budget_usd=daily_budget_usd,
        per_mission_cost_ceiling_usd=1.0,
        blocked_threshold=5,
        failed_threshold=2,
        idle_threshold=1,
        collaboration_enabled=collaboration_enabled,
        collaboration_daily_budget_usd=collaboration_daily_budget_usd,
        collab_messages_per_tick=collab_messages_per_tick,
    )


class _FakeModelGateway:
    """Returns a canned short line; can be told to raise on a given call
    index (0-based) to exercise the fail-closed path."""

    def __init__(self, *, raise_on_call: int | None = None) -> None:
        self.calls: list[tuple[str, int]] = []
        self._raise_on_call = raise_on_call

    async def complete_short(self, prompt: str, *, max_tokens: int) -> str:
        idx = len(self.calls)
        self.calls.append((prompt, max_tokens))
        if self._raise_on_call is not None and idx == self._raise_on_call:
            raise RuntimeError("model provider unavailable")
        return "Shipping the integration by end of day."


class _FakeEventPublisher:
    """Mirrors the REAL, production-wired ``app.org.events.OrgEventPublisher
    .publish`` contract (keyword-only here to force call sites to match it by
    name, not position) -- NOT the dead ``app.org.event_publisher`` module's
    positional shape. This is the exact bug class Finding 1 covers: calling
    the real singleton with the dead module's positional order silently
    scrambles ``org_id``/``tenant_id``/``payload``.
    """

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(
        self,
        *,
        event_type: str,
        org_id: str,
        tenant_id: str,
        payload: dict | None = None,
    ) -> str:
        self.events.append(
            {
                "event_type": event_type,
                "org_id": org_id,
                "tenant_id": tenant_id,
                "payload": payload or {},
            }
        )
        return "corr-id"


class _FakeCounters:
    """Fake ``BrainCounters``-shaped collaboration-spend tracker.

    Mirrors the real ``snapshot_collab_spend``/``record_collab_spend`` pair
    (Fix 2) so ``CollaborationTick`` can gate on and record spend against
    ``collaboration_daily_budget_usd`` without touching Redis.
    """

    def __init__(self, collab_spend: float = 0.0) -> None:
        self.collab_spend = collab_spend
        self.recorded: list[float] = []

    async def snapshot_collab_spend(self) -> float:
        return self.collab_spend

    async def record_collab_spend(self, est_cost_usd: float) -> None:
        self.recorded.append(est_cost_usd)
        self.collab_spend += est_cost_usd


@pytest.mark.asyncio
async def test_disabled_emits_nothing_and_never_calls_model():
    gateway = _FakeModelGateway()
    publisher = _FakeEventPublisher()
    tick = CollaborationTick(gateway, publisher, _FakeCounters())

    emitted = await tick.run(
        org_id="org1",
        tenant_id="t1",
        settings=_settings(collaboration_enabled=False),
        autonomy_level=4,
        leads=["alice", "bob"],
        day_spend_usd=0.0,
    )

    assert emitted == 0
    assert publisher.events == []
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_autonomy_below_l3_emits_nothing():
    gateway = _FakeModelGateway()
    publisher = _FakeEventPublisher()
    tick = CollaborationTick(gateway, publisher, _FakeCounters())

    emitted = await tick.run(
        org_id="org1",
        tenant_id="t1",
        settings=_settings(),
        autonomy_level=2,
        leads=["alice", "bob"],
        day_spend_usd=0.0,
    )

    assert emitted == 0
    assert publisher.events == []
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_collaboration_budget_exhausted_emits_nothing():
    """Fix 2 — the tick gates on ``collaboration_daily_budget_usd`` (its own
    tracked spend), not on the org's general ``daily_budget_usd``: an org
    with plenty of general budget left still goes quiet once its
    collaboration-specific budget is used up."""
    gateway = _FakeModelGateway()
    publisher = _FakeEventPublisher()
    counters = _FakeCounters(collab_spend=1.0)
    tick = CollaborationTick(gateway, publisher, counters)

    settings = _settings(daily_budget_usd=10_000.0, collaboration_daily_budget_usd=1.0)
    emitted = await tick.run(
        org_id="org1",
        tenant_id="t1",
        settings=settings,
        autonomy_level=4,
        leads=["alice", "bob"],
        day_spend_usd=0.0,  # general org spend is nowhere near its cap
    )

    assert emitted == 0
    assert publisher.events == []
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_zero_collaboration_budget_emits_nothing():
    """Fix 2's own example: operator sets ``collaboration_daily_budget_usd``
    to 0 -> the tick emits 0 messages even with budget and leads available."""
    gateway = _FakeModelGateway()
    publisher = _FakeEventPublisher()
    counters = _FakeCounters(collab_spend=0.0)
    tick = CollaborationTick(gateway, publisher, counters)

    settings = _settings(collaboration_daily_budget_usd=0.0)
    emitted = await tick.run(
        org_id="org1",
        tenant_id="t1",
        settings=settings,
        autonomy_level=4,
        leads=["alice", "bob"],
        day_spend_usd=0.0,
    )

    assert emitted == 0
    assert publisher.events == []
    assert gateway.calls == []
    assert counters.recorded == []


@pytest.mark.asyncio
async def test_emitting_records_collaboration_spend_separately():
    """Fix 2 — each emitted message is charged against the collaboration-
    specific counter (``record_collab_spend``), not the general spend
    counter, so the cap in the guard above actually has real data."""
    gateway = _FakeModelGateway()
    publisher = _FakeEventPublisher()
    counters = _FakeCounters(collab_spend=0.0)
    tick = CollaborationTick(gateway, publisher, counters)

    settings = _settings(collab_messages_per_tick=2, collaboration_daily_budget_usd=1.0)
    emitted = await tick.run(
        org_id="org1",
        tenant_id="t1",
        settings=settings,
        autonomy_level=4,
        leads=["alice", "bob"],
        day_spend_usd=0.0,
    )

    assert emitted == 2
    assert len(counters.recorded) == 2
    assert counters.collab_spend > 0.0


@pytest.mark.asyncio
async def test_no_leads_emits_nothing():
    gateway = _FakeModelGateway()
    publisher = _FakeEventPublisher()
    tick = CollaborationTick(gateway, publisher, _FakeCounters())

    emitted = await tick.run(
        org_id="org1",
        tenant_id="t1",
        settings=_settings(),
        autonomy_level=4,
        leads=[],
        day_spend_usd=0.0,
    )

    assert emitted == 0
    assert publisher.events == []
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_enabled_l4_emits_at_most_cap_messages_as_collaboration_event():
    gateway = _FakeModelGateway()
    publisher = _FakeEventPublisher()
    tick = CollaborationTick(gateway, publisher, _FakeCounters())

    settings = _settings(collab_messages_per_tick=3)
    leads = ["alice", "bob", "carol", "dave", "erin"]  # more leads than the cap

    emitted = await tick.run(
        org_id="org1",
        tenant_id="t1",
        settings=settings,
        autonomy_level=4,
        leads=leads,
        day_spend_usd=0.0,
    )

    assert emitted == 3
    assert len(publisher.events) == 3
    assert len(gateway.calls) == 3
    for event in publisher.events:
        assert event["event_type"] == EVENT_TYPE_COLLABORATION_MESSAGE
        # Correct org_id/tenant_id land in the correct real-signature slots
        # (not scrambled by the dead module's positional order).
        assert event["org_id"] == "org1"
        assert event["tenant_id"] == "t1"
        assert event["payload"]["lead"] in leads[:3]
        assert event["payload"]["message"] == "Shipping the integration by end of day."
    for _prompt, max_tokens in gateway.calls:
        assert max_tokens <= 40


@pytest.mark.asyncio
async def test_model_error_mid_tick_stops_fail_closed():
    # Fails on the 2nd call (index 1) -- the 1st message should already be
    # published, and nothing further should be attempted.
    gateway = _FakeModelGateway(raise_on_call=1)
    publisher = _FakeEventPublisher()
    tick = CollaborationTick(gateway, publisher, _FakeCounters())

    settings = _settings(collab_messages_per_tick=4)
    leads = ["alice", "bob", "carol", "dave"]

    emitted = await tick.run(
        org_id="org1",
        tenant_id="t1",
        settings=settings,
        autonomy_level=4,
        leads=leads,
        day_spend_usd=0.0,
    )

    assert emitted == 1
    assert len(publisher.events) == 1
    assert publisher.events[0]["event_type"] == EVENT_TYPE_COLLABORATION_MESSAGE
    assert publisher.events[0]["org_id"] == "org1"
    assert publisher.events[0]["tenant_id"] == "t1"
    assert publisher.events[0]["payload"]["lead"] == "alice"
    # The gateway was called twice: once succeeding, once raising -- and the
    # tick stopped there rather than trying "carol"/"dave".
    assert len(gateway.calls) == 2
