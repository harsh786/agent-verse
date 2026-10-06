"""B7-5: goal_score_below filters are what the trigger says.

* A trigger saved without a threshold kept the dataclass default 0.0, and no
  score is below 0.0 — it could never fire (the UI showed 0.7 but sent nothing).
  A threshold outside (0, 1] is now refused when the trigger is saved.
* ``score_dimension`` ("accuracy", …) was stored and ignored: the consumer always
  compared the overall average. The event now carries the per-dimension scores
  and the consumer compares the chosen one; a dimension the scorecard does not
  have does not fire.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.triggers.consumers.chain import ChainTriggerConsumer, build_chain_event
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.validation import validate_spec


@pytest.mark.parametrize("threshold", [0.0, -0.1, 1.5])
def test_score_below_threshold_must_be_in_range(threshold: float) -> None:
    with pytest.raises(ValueError, match="score_threshold"):
        validate_spec(
            TriggerSpec(trigger_type=TriggerType.GOAL_SCORE_BELOW, score_threshold=threshold)
        )


def test_score_below_with_a_threshold_is_valid() -> None:
    validate_spec(TriggerSpec(trigger_type=TriggerType.GOAL_SCORE_BELOW, score_threshold=0.7))


class _Store:
    def __init__(self, spec: TriggerSpec) -> None:
        self.spec = spec

    async def find_by_type_async(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
        return [{"spec": self.spec}]


class _Disp:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def resolve_tenant_plan(self, tenant_id: str) -> str:
        return "free"

    async def dispatch(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(args)


async def _fire(spec: TriggerSpec, **event: Any) -> int:
    disp = _Disp()
    consumer = ChainTriggerConsumer(trigger_store=_Store(spec), dispatcher=disp)
    raw = build_chain_event(channel="goal.score_below", tenant_id="t", goal_id="g", **event)
    await consumer._handle({"channel": "goal.score_below", "data": raw})
    return len(disp.calls)


async def test_dimension_threshold_compares_that_dimension() -> None:
    spec = TriggerSpec(
        trigger_type=TriggerType.GOAL_SCORE_BELOW, score_threshold=0.5, score_dimension="accuracy"
    )
    # Overall is high, accuracy is low: fires on accuracy.
    assert await _fire(spec, score=0.9, scores={"accuracy": 0.2, "latency": 1.0}) == 1
    # Overall is low, accuracy is high: does not fire.
    assert await _fire(spec, score=0.3, scores={"accuracy": 0.8, "latency": 0.0}) == 0


async def test_missing_dimension_does_not_fire() -> None:
    spec = TriggerSpec(
        trigger_type=TriggerType.GOAL_SCORE_BELOW, score_threshold=0.5, score_dimension="safety"
    )
    assert await _fire(spec, score=0.1, scores={"accuracy": 0.1}) == 0


async def test_overall_dimension_uses_the_average() -> None:
    spec = TriggerSpec(
        trigger_type=TriggerType.GOAL_SCORE_BELOW, score_threshold=0.5, score_dimension="overall"
    )
    assert await _fire(spec, score=0.4, scores={"accuracy": 0.9}) == 1


async def test_event_without_a_score_does_not_fire() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.GOAL_SCORE_BELOW, score_threshold=0.9)
    assert await _fire(spec) == 0


def test_chain_event_carries_dimension_scores() -> None:
    ev = json.loads(
        build_chain_event(
            channel="goal.score_below", tenant_id="t", goal_id="g", score=0.5,
            scores={"accuracy": 0.25},
        )
    )
    assert ev["scores"] == {"accuracy": 0.25}


def test_worker_score_below_event_carries_dimension_scores(monkeypatch: Any) -> None:
    from types import SimpleNamespace

    import fakeredis

    from app.core.config import get_settings
    from app.scaling import tasks

    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: r)
    card = SimpleNamespace(scores={"accuracy": 0.2, "task_completion": 0.6})
    card.average_score = lambda: 0.4
    state = SimpleNamespace(context={"eval_scorecard": card})
    assert tasks._publish_worker_score_below(
        state, tenant_id="t", goal_id="g", agent_id="", plan="free",
        trigger_chain_depth=0, source_trigger_id="",
    )
    [(_, fields)] = r.xrange(get_settings().trigger_bus_stream_goal)
    assert json.loads(fields["data"])["scores"] == {"accuracy": 0.2, "task_completion": 0.6}
