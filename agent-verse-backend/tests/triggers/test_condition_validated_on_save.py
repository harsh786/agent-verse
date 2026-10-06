"""B1-12: a condition the evaluator cannot run is refused when the trigger is saved.

Live (TIME-CONDITION, 2026-10-06): a cron with condition_cel
``payload.goal_text.contains("ACK")`` was created 201, then every fire was
skipped as ``condition_error`` ("Call is not allowed in a condition"): the
deployment evaluates conditions with the dependency-free safe evaluator, which
has no function calls. The trigger looked active and never ran.
"""

from __future__ import annotations

import pytest

from app.triggers.condition.evaluator import CELEvaluator
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.validation import creatable_error, validate_spec


def _cron(**kw: str) -> TriggerSpec:
    return TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *", **kw)


@pytest.mark.parametrize(
    "expr",
    [
        'payload.goal_text.contains("ACK")',
        "size(payload.items) > 2",
        "payload.amount * 2 > 10",
        "payload[payload.key] == 1",
        "lambda x: x",
        "payload.amount >",
        "__import__('os')",
    ],
)
@pytest.mark.parametrize("field", ["condition_expression", "condition"])
def test_an_unrunnable_condition_is_refused(expr: str, field: str) -> None:
    spec = _cron(**{field: expr})
    with pytest.raises(ValueError, match="condition"):
        validate_spec(spec, plan="enterprise")
    assert creatable_error(spec, plan="enterprise") is not None


@pytest.mark.parametrize(
    "expr",
    [
        'payload.priority == "P1"',
        "payload.amount > 1000 && payload.currency == 'INR'",
        "severity in ['critical', 'high'] || !payload.muted",
        'payload["order"]["status"] != "cancelled"',
        "true",
    ],
)
def test_supported_conditions_are_accepted(expr: str) -> None:
    validate_spec(_cron(condition_expression=expr), plan="enterprise")
    validate_spec(_cron(condition=expr), plan="enterprise")


def test_the_check_matches_what_evaluation_accepts() -> None:
    evaluator = CELEvaluator()
    evaluator.check('payload.priority == "P1"')
    with pytest.raises(ValueError):
        evaluator.check('payload.goal_text.contains("x")')
