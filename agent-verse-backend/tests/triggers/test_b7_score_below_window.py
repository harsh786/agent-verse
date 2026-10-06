"""B7 live open item 3: goal_score_below on LLM-judged scores must not misfire.

``accuracy`` and ``coherence`` are LLM judgements: the same correct "ACK" answer
scored accuracy 1.0 in one live run and 0.0 in the next, so a trigger on a judged
dimension (or on the overall average, which includes both) fired on one noisy
judgement. A goal_score_below trigger now decides on a rolling window of the
watched goals' scores (``app.triggers.score_window``):

* ``score_window`` 0 (default) = 1 for deterministic dimensions (unchanged),
  3 for accuracy / coherence / overall; ``score_aggregation`` "mean" (default)
  or "all" (N consecutive breaches);
* nothing fires before N scores; the window is cleared after a firing;
* the series is shared through Redis and a relayed goal is not counted twice.
"""

from __future__ import annotations

from typing import Any

import fakeredis
import pytest

from app.intelligence.eval_runner import EvalRunner
from app.triggers import score_window
from app.triggers.consumers.chain import ChainTriggerConsumer, build_chain_event
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.validation import validate_spec


def _spec(**kw: Any) -> TriggerSpec:
    kw.setdefault("score_threshold", 0.5)
    return TriggerSpec(trigger_type=TriggerType.GOAL_SCORE_BELOW, **kw)


# ── the rule ─────────────────────────────────────────────────────────────────


def test_judged_dimensions_are_real_evaluation_dimensions() -> None:
    assert score_window.JUDGED_DIMENSIONS.issubset(EvalRunner.DIMENSIONS)


@pytest.mark.parametrize(
    ("dimension", "window"),
    [
        ("accuracy", 3),
        ("coherence", 3),
        ("", 3),  # overall average includes the judged dimensions
        ("overall", 3),
        ("task_completion", 1),
        ("efficiency", 1),
        ("safety", 1),
        ("sla", 1),
        ("tool_relevance", 1),
    ],
)
def test_default_window_depends_on_whether_the_dimension_is_judged(
    dimension: str, window: int
) -> None:
    assert score_window.effective_window(_spec(score_dimension=dimension)) == window


def test_explicit_window_overrides_the_default() -> None:
    assert score_window.effective_window(_spec(score_dimension="accuracy", score_window=1)) == 1
    assert score_window.effective_window(_spec(score_dimension="sla", score_window=5)) == 5


@pytest.mark.parametrize(
    "kw",
    [
        {"score_window": -1},
        {"score_window": score_window.MAX_SCORE_WINDOW + 1},
        {"score_window": True},
        {"score_window": "3"},
        {"score_aggregation": "median"},
    ],
)
def test_invalid_window_options_are_refused(kw: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"score_window|score_aggregation"):
        validate_spec(_spec(**kw))


@pytest.mark.parametrize(
    "kw", [{}, {"score_window": 0}, {"score_window": 10, "score_aggregation": "all"}]
)
def test_valid_window_options(kw: dict[str, Any]) -> None:
    validate_spec(_spec(**kw))


def test_window_options_are_not_validated_for_other_types() -> None:
    validate_spec(TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED, score_window=-1))


@pytest.mark.parametrize(
    ("scores", "how", "fires"),
    [
        ([0.0, 1.0, 1.0], "mean", False),  # one bad judgement is diluted
        ([0.0, 0.0, 1.0], "mean", True),  # mean 0.33 < 0.5
        ([0.4, 0.4, 0.4], "all", True),
        ([0.4, 0.6, 0.4], "all", False),  # not consecutive
        ([0.0, 0.0], "mean", False),  # fewer than N scores: never
    ],
)
def test_breached(scores: list[float], how: str, fires: bool) -> None:
    assert score_window.breached(scores, 0.5, 3, how)[0] is fires


# ── through the chain consumer ───────────────────────────────────────────────


class _Store:
    def __init__(self, spec: TriggerSpec, trigger_id: str = "trg-1") -> None:
        self.spec = spec
        self.trigger_id = trigger_id

    async def find_by_type_async(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
        return [{"schedule_id": self.trigger_id, "spec": self.spec}]


class _Disp:
    def __init__(self, skip_reason: str | None = None) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.skip_reason = skip_reason

    async def resolve_tenant_plan(self, tenant_id: str) -> str:
        return "free"

    async def dispatch(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(args)
        return type("R", (), {"skip_reason": self.skip_reason, "goal_id": "new"})()


def _event(goal_id: str, accuracy: float, overall: float = 0.9) -> dict[str, Any]:
    raw = build_chain_event(
        channel="goal.score_below", tenant_id="t-w", goal_id=goal_id,
        score=overall, scores={"accuracy": accuracy, "task_completion": 1.0},
    )
    return {"channel": "goal.score_below", "data": raw}


async def _feed(consumer: ChainTriggerConsumer, events: list[dict[str, Any]]) -> None:
    for event in events:
        await consumer._handle(event)


async def test_one_noisy_judgement_does_not_fire_a_judged_trigger() -> None:
    """The live case: accuracy flips 1.0 -> 0.0 -> 1.0 for the same answer."""
    disp = _Disp()
    consumer = ChainTriggerConsumer(
        trigger_store=_Store(_spec(score_dimension="accuracy")), dispatcher=disp
    )
    await _feed(consumer, [_event("g1", 1.0), _event("g2", 0.0), _event("g3", 1.0)])
    assert disp.calls == []


async def test_a_consistently_weak_agent_still_fires_once_per_window() -> None:
    disp = _Disp()
    consumer = ChainTriggerConsumer(
        trigger_store=_Store(_spec(score_dimension="accuracy")), dispatcher=disp
    )
    await _feed(consumer, [_event("g1", 0.2), _event("g2", 0.1)])
    assert disp.calls == []  # fewer than 3 scores
    await _feed(consumer, [_event("g3", 0.3)])
    assert len(disp.calls) == 1
    payload = disp.calls[0][1]
    assert payload["goal_id"] == "g3"
    assert payload["score_window"] == {
        "size": 3, "aggregation": "mean", "value": 0.2, "scores": [0.3, 0.1, 0.2]
    }
    # Cleared after the firing: the next firing needs 3 fresh goals.
    await _feed(consumer, [_event("g4", 0.1), _event("g5", 0.1)])
    assert len(disp.calls) == 1
    await _feed(consumer, [_event("g6", 0.1)])
    assert len(disp.calls) == 2


async def test_all_aggregation_needs_consecutive_breaches() -> None:
    disp = _Disp()
    spec = _spec(score_dimension="accuracy", score_aggregation="all")
    consumer = ChainTriggerConsumer(trigger_store=_Store(spec), dispatcher=disp)
    await _feed(consumer, [_event("g1", 0.0), _event("g2", 0.0), _event("g3", 0.9)])
    assert disp.calls == []  # mean 0.3 would fire; "all" does not
    await _feed(consumer, [_event("g4", 0.1), _event("g5", 0.2)])
    assert disp.calls == []  # window [0.2, 0.1, 0.9]
    await _feed(consumer, [_event("g6", 0.3)])
    assert len(disp.calls) == 1  # [0.3, 0.2, 0.1]


async def test_overall_defaults_to_a_window_too() -> None:
    disp = _Disp()
    consumer = ChainTriggerConsumer(trigger_store=_Store(_spec()), dispatcher=disp)
    await _feed(consumer, [_event("g1", 1.0, overall=0.3)])
    assert disp.calls == []
    await _feed(consumer, [_event("g2", 1.0, overall=0.3), _event("g3", 1.0, overall=0.3)])
    assert len(disp.calls) == 1


async def test_deterministic_dimension_still_fires_on_one_goal() -> None:
    disp = _Disp()
    spec = _spec(score_dimension="task_completion")
    consumer = ChainTriggerConsumer(trigger_store=_Store(spec), dispatcher=disp)
    raw = build_chain_event(
        channel="goal.score_below", tenant_id="t-w", goal_id="g1", score=0.9,
        scores={"task_completion": 0.0},
    )
    await consumer._handle({"channel": "goal.score_below", "data": raw})
    assert len(disp.calls) == 1
    assert "score_window" not in disp.calls[0][1]


async def test_score_window_1_opts_a_judged_trigger_into_single_goal_firing() -> None:
    disp = _Disp()
    spec = _spec(score_dimension="accuracy", score_window=1)
    consumer = ChainTriggerConsumer(trigger_store=_Store(spec), dispatcher=disp)
    await _feed(consumer, [_event("g1", 0.0)])
    assert len(disp.calls) == 1


async def test_a_relayed_goal_is_not_counted_twice() -> None:
    disp = _Disp()
    consumer = ChainTriggerConsumer(
        trigger_store=_Store(_spec(score_dimension="accuracy")), dispatcher=disp
    )
    await _feed(consumer, [_event("g1", 0.0), _event("g1", 0.0), _event("g1", 0.0)])
    assert disp.calls == []


async def test_a_suppressed_fire_keeps_the_window() -> None:
    """Rate limit / loop guard refused it: the window stays full, the next goal retries."""
    disp = _Disp(skip_reason="rate_limit")
    consumer = ChainTriggerConsumer(
        trigger_store=_Store(_spec(score_dimension="accuracy")), dispatcher=disp
    )
    await _feed(consumer, [_event("g1", 0.0), _event("g2", 0.0), _event("g3", 0.0)])
    assert len(disp.calls) == 1
    await _feed(consumer, [_event("g4", 0.0)])
    assert len(disp.calls) == 2


async def test_window_is_shared_across_replicas_through_redis() -> None:
    """Two consumers (two API replicas) on one Redis build one series per trigger."""
    redis = fakeredis.FakeAsyncRedis()
    spec = _spec(score_dimension="accuracy")
    disp_a, disp_b = _Disp(), _Disp()
    a = ChainTriggerConsumer(trigger_store=_Store(spec), dispatcher=disp_a, redis=redis)
    b = ChainTriggerConsumer(trigger_store=_Store(spec), dispatcher=disp_b, redis=redis)
    await a._handle(_event("g1", 0.1))
    await b._handle(_event("g2", 0.1))
    await b._handle(_event("g2", 0.1))  # relayed copy on the other replica
    assert disp_a.calls == disp_b.calls == []
    await a._handle(_event("g3", 0.1))
    assert len(disp_a.calls) == 1 and disp_b.calls == []
    key = score_window.series_key("t-w", "trg-1")
    assert await redis.llen(key) == 0  # cleared after the firing
    assert await redis.ttl(key) == -2


async def test_each_trigger_has_its_own_window() -> None:
    redis = fakeredis.FakeAsyncRedis()
    disp = _Disp()
    one = ChainTriggerConsumer(
        trigger_store=_Store(_spec(score_dimension="accuracy"), "trg-a"),
        dispatcher=disp, redis=redis,
    )
    other = ChainTriggerConsumer(
        trigger_store=_Store(_spec(score_dimension="accuracy"), "trg-b"),
        dispatcher=disp, redis=redis,
    )
    await one._handle(_event("g1", 0.0))
    await one._handle(_event("g2", 0.0))
    await other._handle(_event("g3", 0.0))
    assert disp.calls == []
    assert await redis.llen(score_window.series_key("t-w", "trg-a")) == 2
    assert await redis.ttl(score_window.series_key("t-w", "trg-a")) > 0


async def test_redis_outage_falls_back_to_a_local_window() -> None:
    class _Down:
        async def lrange(self, *a: Any) -> Any:
            raise ConnectionError("redis down")

        async def delete(self, *a: Any) -> Any:
            raise ConnectionError("redis down")

    series = score_window.ScoreSeries(_Down())
    assert await series.observe("k", "g1", 0.1, 3) == [0.1]
    assert await series.observe("k", "g2", 0.2, 3) == [0.2, 0.1]
    await series.reset("k")  # never raises
    assert await series.observe("k", "g3", 0.3, 3) == [0.3]


def test_window_options_survive_persistence() -> None:
    """The options ride the schedule's config JSONB like every other spec field."""
    from app.triggers.store import apply_config_to_spec, spec_config

    spec = _spec(score_dimension="accuracy", score_window=5, score_aggregation="all")
    restored = TriggerSpec(trigger_type=TriggerType.GOAL_SCORE_BELOW)
    apply_config_to_spec(restored, spec_config(spec))
    assert (restored.score_window, restored.score_aggregation) == (5, "all")
