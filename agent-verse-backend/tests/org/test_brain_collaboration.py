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
    classify_message_kind,
)
from app.org.brain_settings import AutonomySettings

_VALID_KINDS = {"update", "proposal", "question", "handoff", "result", "risk", "block"}


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
    """Returns a canned short line (plus canned latency/tokens/cost); can be
    told to raise on a given call index (0-based) to exercise the
    fail-closed path."""

    def __init__(
        self,
        *,
        raise_on_call: int | None = None,
        message: str = "Shipping the integration by end of day.",
        latency_ms: int = 5,
        tokens: int = 12,
        cost_usd: float = 0.001,
    ) -> None:
        self.calls: list[tuple[str, int]] = []
        self._raise_on_call = raise_on_call
        self._message = message
        self._latency_ms = latency_ms
        self._tokens = tokens
        self._cost_usd = cost_usd

    async def complete_short(
        self, prompt: str, *, max_tokens: int
    ) -> tuple[str, int, int, float]:
        idx = len(self.calls)
        self.calls.append((prompt, max_tokens))
        if self._raise_on_call is not None and idx == self._raise_on_call:
            raise RuntimeError("model provider unavailable")
        return self._message, self._latency_ms, self._tokens, self._cost_usd


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
        payload = event["payload"]
        # Backward-compat: existing consumers still see lead/message.
        assert payload["lead"] in leads[:3]
        assert payload["message"] == "Shipping the integration by end of day."
        # Typed enrichment for the Situation Room UX.
        assert payload["from_agent"] == payload["lead"]
        assert payload["to"] == "team"
        assert payload["kind"] in _VALID_KINDS
        assert payload["latency_ms"] >= 0
        assert payload["tokens"] >= 0
        assert payload["cost_usd"] >= 0
        assert payload["mission_id"] is None
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


@pytest.mark.parametrize(
    ("text", "expected_kind"),
    [
        ("What is the status of the migration?", "question"),
        ("I propose we cut over to the new schema tonight.", "proposal"),
        ("There's a risk the rollback window slips.", "risk"),
        ("We are blocked on the vendor's API key.", "block"),
        ("Migration completed, all rows verified.", "result"),
        ("Handoff of the on-call rotation to Bob now.", "handoff"),
        ("Continuing to monitor the deploy.", "update"),
    ],
)
def test_classify_message_kind_maps_representative_strings(text, expected_kind):
    assert classify_message_kind(text) == expected_kind


class _FakeLLMProvider:
    """Minimal fake satisfying the ``complete_short`` -> ``complete`` call
    path in ``LLMProviderCollaborationGateway``, without a real provider or
    network call."""

    def __init__(self, response) -> None:
        self._response = response
        self._default_model = "fast-fake-model"
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return self._response


@pytest.mark.asyncio
async def test_complete_short_returns_four_tuple_with_real_usage():
    """The gateway's ``complete_short`` returns
    ``(text, latency_ms, tokens, cost_usd)``, deriving tokens/cost from the
    provider response's real usage fields when present."""
    from app.org.brain_collaboration import LLMProviderCollaborationGateway
    from app.providers.base import CompletionResponse, TokenUsage

    response = CompletionResponse(
        content="Shipping the integration by end of day.",
        model="claude-haiku",
        input_tokens=30,
        output_tokens=10,
        usage=TokenUsage(prompt_tokens=30, completion_tokens=10, total_tokens=40),
    )
    provider = _FakeLLMProvider(response)
    gateway = LLMProviderCollaborationGateway(provider, gateway=None)

    text, latency_ms, tokens, cost_usd = await gateway.complete_short(
        "prompt", max_tokens=40
    )

    assert text == "Shipping the integration by end of day."
    assert latency_ms >= 0
    assert tokens == 40
    assert cost_usd > 0.0


@pytest.mark.asyncio
async def test_complete_short_falls_back_when_no_real_usage_reported():
    """When the provider response carries no usage info at all, ``complete_short``
    falls back to a rough length-based token estimate and the fixed
    per-message cost estimate rather than reporting a bogus real cost."""
    from app.org.brain_collaboration import (
        _EST_COST_USD_PER_MESSAGE,
        LLMProviderCollaborationGateway,
    )
    from app.providers.base import CompletionResponse

    response = CompletionResponse(
        content="Shipping the integration by end of day.",
        model="claude-haiku",
    )
    provider = _FakeLLMProvider(response)
    gateway = LLMProviderCollaborationGateway(provider, gateway=None)

    text, latency_ms, tokens, cost_usd = await gateway.complete_short(
        "prompt", max_tokens=40
    )

    assert text == "Shipping the integration by end of day."
    assert latency_ms >= 0
    assert tokens >= 0
    assert cost_usd == _EST_COST_USD_PER_MESSAGE


@pytest.mark.asyncio
async def test_run_publishes_enriched_payload_with_backward_compat_fields():
    gateway = _FakeModelGateway(latency_ms=42, tokens=17, cost_usd=0.0025)
    publisher = _FakeEventPublisher()
    tick = CollaborationTick(gateway, publisher, _FakeCounters())

    emitted = await tick.run(
        org_id="org1",
        tenant_id="t1",
        settings=_settings(),
        autonomy_level=4,
        leads=["alice"],
        day_spend_usd=0.0,
    )

    assert emitted == 1
    payload = publisher.events[0]["payload"]
    # Backward-compat.
    assert payload["lead"] == "alice"
    assert payload["message"] == "Shipping the integration by end of day."
    # Enrichment.
    assert payload["from_agent"] == "alice"
    assert payload["to"] == "team"
    assert payload["kind"] in _VALID_KINDS
    assert payload["latency_ms"] == 42
    assert payload["tokens"] == 17
    assert payload["cost_usd"] == 0.0025
    assert payload["mission_id"] is None


@pytest.mark.asyncio
async def test_real_gateway_cost_feeds_record_collab_spend():
    """When the fake gateway reports a real (non-default) cost, that exact
    real cost -- not the fixed ``_EST_COST_USD_PER_MESSAGE`` estimate -- is
    what gets recorded via ``counters.record_collab_spend``."""
    real_cost = 0.0042
    gateway = _FakeModelGateway(cost_usd=real_cost)
    publisher = _FakeEventPublisher()
    counters = _FakeCounters()
    tick = CollaborationTick(gateway, publisher, counters)

    emitted = await tick.run(
        org_id="org1",
        tenant_id="t1",
        settings=_settings(),
        autonomy_level=4,
        leads=["alice"],
        day_spend_usd=0.0,
    )

    assert emitted == 1
    assert counters.recorded == [real_cost]
    assert publisher.events[0]["payload"]["cost_usd"] == real_cost
