"""Regression: trigger conditions must actually be evaluated (fail closed).

cel-python is not installed, so both ``CELEvaluator.evaluate`` and
``TriggerDispatcher._evaluate_condition`` used to return True for every
expression, and the dispatcher gated on ``spec.condition`` while the API stores
the CONDITION expression in ``spec.condition_expression`` — so every event fired
every CONDITION trigger.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.triggers.condition.evaluator import CELEvaluator
from app.triggers.condition.safe_eval import ConditionError, evaluate_condition
from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.models import TriggerSpec, TriggerType

PAYLOAD = {
    "severity": "critical",
    "count": 7,
    "env": "prod",
    "ok": False,
    "labels": {"team": "sre", "nested": {"level": 3}},
}


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("payload.severity == 'critical'", True),
        ("payload.severity == 'low'", False),
        ("severity != 'low'", True),
        ("payload.count > 5 && payload.count <= 7", True),
        ("payload.count >= 8 || payload.env == 'staging'", False),
        ("payload.labels.team == 'sre'", True),
        ("payload.labels.nested.level < 4", True),
        ("payload['labels']['team'] == 'sre'", True),
        ("payload.env in ['prod', 'staging']", True),
        ("payload.env not in ['prod']", False),
        ("!(payload.count == 7)", False),
        ("not payload.ok and (payload.count == 7 or false)", True),
        ("payload.ok == false", True),
        ("payload.count != -1", True),
        ("'crit' in payload.severity", True),
    ],
)
def test_safe_evaluator_semantics(expr: str, expected: bool) -> None:
    assert evaluate_condition(expr, PAYLOAD) is expected
    assert CELEvaluator().evaluate(expr, PAYLOAD) is expected


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('true')",
        "payload.__class__",
        "().__class__.__bases__",
        "payload.severity.upper() == 'CRITICAL'",
        "open('/etc/passwd')",
        "lambda: 1",
        "[x for x in payload]",
        "payload.missing == 1",
        "payload.severity.__len__",
        "payload.count < 'a'",
        "payload.count +",  # syntax error
        "payload.count + 1 == 8",  # arithmetic not supported
        "payload[payload.env]",  # non-literal subscript
    ],
)
def test_safe_evaluator_rejects_unsafe_or_invalid(expr: str) -> None:
    with pytest.raises(ConditionError):
        evaluate_condition(expr, PAYLOAD)


def test_string_literals_are_not_rewritten() -> None:
    # '&&' / '!' inside a string literal must stay literal.
    assert evaluate_condition("payload.s == 'a && !b'", {"s": "a && !b"}) is True


def _spec(**kw: object) -> TriggerSpec:
    spec = TriggerSpec(trigger_type=TriggerType.CONDITION, goal_template="Go", **kw)  # type: ignore[arg-type]
    spec.trigger_id = "trg-cond"  # type: ignore[attr-defined]
    return spec


@pytest.mark.asyncio
async def test_dispatcher_gates_on_condition_expression_false() -> None:
    goal_service = AsyncMock()
    goal_service.create_goal.return_value = {"goal_id": "g-1"}
    d = TriggerDispatcher(goal_service=goal_service)
    persisted: list[object] = []
    d._persist_event = AsyncMock(side_effect=persisted.append)  # type: ignore[method-assign]
    spec = _spec(condition_expression="payload.severity == 'critical'")
    tenant = SimpleNamespace(tenant_id="t1", plan="free")

    result = await d.dispatch(spec, {"severity": "low"}, tenant)

    assert result.skip_reason == "condition_false"
    goal_service.create_goal.assert_not_awaited()
    # the skip is audited
    assert [getattr(e, "skip_reason", None) for e in persisted] == ["condition_false"]


@pytest.mark.asyncio
async def test_dispatcher_fires_when_condition_expression_true() -> None:
    goal_service = AsyncMock()
    goal_service.create_goal.return_value = {"goal_id": "g-1"}
    d = TriggerDispatcher(goal_service=goal_service)
    spec = _spec(condition_expression="payload.severity == 'critical'")
    tenant = SimpleNamespace(tenant_id="t1", plan="free")

    result = await d.dispatch(spec, {"severity": "critical"}, tenant)

    assert result.skip_reason is None
    assert result.goal_id == "g-1"


@pytest.mark.asyncio
async def test_dispatcher_condition_error_fails_closed_and_is_audited() -> None:
    goal_service = AsyncMock()
    d = TriggerDispatcher(goal_service=goal_service)
    persisted: list[object] = []
    d._persist_event = AsyncMock(side_effect=persisted.append)  # type: ignore[method-assign]
    spec = _spec(condition_expression="__import__('os').getcwd()")
    tenant = SimpleNamespace(tenant_id="t1", plan="free")

    result = await d.dispatch(spec, {"severity": "critical"}, tenant)

    assert result.skip_reason == "condition_error"
    goal_service.create_goal.assert_not_awaited()
    assert [getattr(e, "skip_reason", None) for e in persisted] == ["condition_error"]


@pytest.mark.asyncio
async def test_dispatcher_legacy_condition_field_still_gates() -> None:
    goal_service = AsyncMock()
    d = TriggerDispatcher(goal_service=goal_service)
    spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK, condition="payload.x == 1")
    spec.trigger_id = "trg-legacy"  # type: ignore[attr-defined]
    tenant = SimpleNamespace(tenant_id="t1", plan="free")

    result = await d.dispatch(spec, {"x": 2}, tenant)

    assert result.skip_reason == "condition_false"
    goal_service.create_goal.assert_not_awaited()


def test_consumer_condition_really_evaluates() -> None:
    from app.triggers.consumers.condition import ConditionTriggerConsumer

    c = ConditionTriggerConsumer()
    spec = _spec(condition_expression="payload.severity == 'critical'")
    assert c._should_fire("condition", spec, "t", {"severity": "critical"}, {}) is True
    assert c._should_fire("condition", spec, "t", {"severity": "low"}, {}) is False


def test_consumer_compound_subcondition_error_is_false() -> None:
    from app.triggers.consumers.condition import ConditionTriggerConsumer

    c = ConditionTriggerConsumer()
    compound = _spec(compound_logic="OR", compound_trigger_ids=["a", "b"])
    index = {
        "a": _spec(condition_expression="open('x')"),
        "b": _spec(condition_expression="payload.x == 1"),
    }
    assert c._should_fire("compound", compound, "t", {"x": 2}, index) is False
    assert c._should_fire("compound", compound, "t", {"x": 1}, index) is True
