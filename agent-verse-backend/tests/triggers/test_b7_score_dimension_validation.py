"""B7-L4: a goal_score_below trigger names a dimension the scorecard has.

``score_dimension`` was stored as given: a typo ("acuracy") or a dimension the
evaluator never produces was accepted with 201 and the trigger then silently
never fired (the consumer finds no such score). It is now refused when the
trigger is saved; blank / "overall" mean the overall average.
"""

from __future__ import annotations

import pytest

from app.intelligence.eval_runner import EvalRunner
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.validation import validate_spec


def _spec(dimension: str) -> TriggerSpec:
    return TriggerSpec(
        trigger_type=TriggerType.GOAL_SCORE_BELOW, score_threshold=0.7, score_dimension=dimension
    )


@pytest.mark.parametrize("dimension", ["acuracy", "speed", "Accuracy ", "overall_score"])
def test_unknown_score_dimension_is_refused(dimension: str) -> None:
    with pytest.raises(ValueError, match="score_dimension"):
        validate_spec(_spec(dimension))


@pytest.mark.parametrize("dimension", ["", "overall", *EvalRunner.DIMENSIONS])
def test_known_score_dimension_is_accepted(dimension: str) -> None:
    validate_spec(_spec(dimension))


def test_other_trigger_types_ignore_score_dimension() -> None:
    validate_spec(
        TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED, score_dimension="anything")
    )
