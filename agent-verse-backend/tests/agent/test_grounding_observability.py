"""Structured observability for grounding/hallucination debugging.

Closes an audit gap: `app/agent/grounding.py` previously only had ad hoc
`logger.debug`/`logger.info` calls — no Prometheus metric and no runtime
decision trace event existed for grounding failures specifically. This file
proves:
  * `agentverse_grounding_check_total` (app.observability.metrics) increments
    on every check, labelled by outcome ("grounded"/"ungrounded") and risk
    ("high_risk"/"normal") — and specifically that a failing check increments
    the "ungrounded" bucket while a passing check does not.
  * `GroundingResult.trace_event` is populated with a
    `grounding_check_failed` runtime decision trace event
    (app.observability.runtime_decision_trace) on failure, and is None on
    success.
"""

from __future__ import annotations

from app.agent.grounding import GroundingChecker, check_grounding
from app.observability import metrics
from app.observability.runtime_decision_trace import RuntimeSSEEmitter, SSEEventType
from app.providers.base import CompletionResponse


def _grounding_sample(status: str, risk: str) -> float:
    value = metrics.REGISTRY.get_sample_value(
        "agentverse_grounding_check_total", {"status": status, "risk": risk}
    )
    return value or 0.0


class TestGroundingMetric:
    def test_ungrounded_check_increments_ungrounded_label(self) -> None:
        before = _grounding_sample("ungrounded", "normal")

        result = check_grounding("Found JIRA-999", [])  # no evidence at all -> ungrounded

        assert result.grounded is False
        after = _grounding_sample("ungrounded", "normal")
        assert after == before + 1

    def test_grounded_check_increments_grounded_label_not_ungrounded(self) -> None:
        before_grounded = _grounding_sample("grounded", "normal")
        before_ungrounded = _grounding_sample("ungrounded", "normal")

        result = check_grounding(
            "Found JIRA-123", ["JIRA-123 title: Fix login, status: open"]
        )

        assert result.grounded is True
        assert _grounding_sample("grounded", "normal") == before_grounded + 1
        assert _grounding_sample("ungrounded", "normal") == before_ungrounded

    def test_high_risk_failure_uses_high_risk_label(self) -> None:
        before = _grounding_sample("ungrounded", "high_risk")

        result = check_grounding("Found JIRA-999 and JIRA-888", [], strict=True)

        assert result.grounded is False
        after = _grounding_sample("ungrounded", "high_risk")
        assert after == before + 1

    def test_no_concrete_claims_does_not_increment_ungrounded(self) -> None:
        """A trivial check (nothing to verify) is not a grounding *failure* and
        must not pollute the failure-rate metric."""
        before = _grounding_sample("ungrounded", "normal")

        result = check_grounding("The task finished.", [])

        assert result.grounded is True
        assert _grounding_sample("ungrounded", "normal") == before

    def test_metric_rendering_does_not_leak_claim_content(self) -> None:
        check_grounding("Contact secret-user@example.com about JIRA-42", [])
        body, _ = metrics.render_metrics()
        assert b"secret-user@example.com" not in body
        assert b"JIRA-42" not in body


class TestGroundingTraceEvent:
    def test_trace_event_populated_on_failure(self) -> None:
        result = check_grounding("Found JIRA-999", [], goal_id="goal-abc")

        assert result.grounded is False
        assert result.trace_event is not None
        assert result.trace_event["type"] == SSEEventType.GROUNDING_CHECK_FAILED
        assert result.trace_event["goal_id"] == "goal-abc"
        # "JIRA-999" extracts as both a jira_id claim and a bare "999" number claim.
        assert result.trace_event["ungrounded_count"] == 2
        assert result.trace_event["checked_claims"] == 2
        assert "JIRA-999" in result.trace_event["ungrounded_samples"]

    def test_trace_event_is_none_on_success(self) -> None:
        result = check_grounding(
            "Found JIRA-123", ["JIRA-123 title: Fix login, status: open"]
        )
        assert result.grounded is True
        assert result.trace_event is None

    def test_trace_event_none_when_no_claims_to_check(self) -> None:
        result = check_grounding("The task finished.", [])
        assert result.grounded is True
        assert result.trace_event is None

    def test_trace_event_reflects_high_risk_flag(self) -> None:
        result = check_grounding("Found JIRA-999 and JIRA-888", [], strict=True)
        assert result.trace_event is not None
        assert result.trace_event["high_risk"] is True

    def test_trace_event_defaults_goal_id_to_empty_string(self) -> None:
        result = check_grounding("Found JIRA-999", [])
        assert result.trace_event is not None
        assert result.trace_event["goal_id"] == ""


class TestRuntimeSSEEmitterGroundingCheckFailed:
    def test_emitter_builds_expected_event_shape(self) -> None:
        emitter = RuntimeSSEEmitter()
        event = emitter.grounding_check_failed(
            goal_id="g1",
            ungrounded_count=2,
            checked_claims=5,
            high_risk=True,
            ungrounded_samples=["JIRA-1", "JIRA-2"],
        )
        assert event["type"] == SSEEventType.GROUNDING_CHECK_FAILED
        assert event["goal_id"] == "g1"
        assert event["ungrounded_count"] == 2
        assert event["checked_claims"] == 5
        assert event["high_risk"] is True
        assert event["ungrounded_samples"] == ["JIRA-1", "JIRA-2"]

    def test_emitter_defaults_samples_to_empty_list(self) -> None:
        emitter = RuntimeSSEEmitter()
        event = emitter.grounding_check_failed(
            goal_id="g1", ungrounded_count=0, checked_claims=0
        )
        assert event["ungrounded_samples"] == []


class TestGroundingCheckerAsyncWiring:
    async def test_check_wires_goal_id_into_trace_event_on_failure(self) -> None:
        checker = GroundingChecker()
        result = await checker.check(
            step_output="Found JIRA-999",
            tool_outputs=[],
            high_risk=True,
            goal_id="goal-xyz",
        )
        assert result.grounded is False
        assert result.trace_event is not None
        assert result.trace_event["goal_id"] == "goal-xyz"
        assert result.trace_event["high_risk"] is True

    async def test_check_no_trace_event_on_success(self) -> None:
        checker = GroundingChecker()
        result = await checker.check(
            step_output="Found JIRA-123",
            tool_outputs=["JIRA-123 title: Fix login, status: open"],
            goal_id="goal-xyz",
        )
        assert result.grounded is True
        assert result.trace_event is None


class _FakeLLMProvider:
    """Minimal stand-in for an LLMProvider — only `complete()` is exercised."""

    def __init__(self, content: str) -> None:
        self._content = content

    async def complete(self, request: object) -> CompletionResponse:
        return CompletionResponse(content=self._content, model="fake-model")


class TestGroundingCheckerLLMPassObservability:
    """The optional second (LLM) pass produces its own grounding outcome and
    must be instrumented the same way as the deterministic pass."""

    async def test_llm_pass_confirms_ungrounded_records_trace_event(self) -> None:
        checker = GroundingChecker(
            llm_provider=_FakeLLMProvider('{"grounded": [], "ungrounded": ["JIRA-999"]}')
        )
        before = _grounding_sample("ungrounded", "normal")

        result = await checker.check(
            step_output="Found JIRA-999",
            tool_outputs=["unrelated evidence text"],
            goal_id="goal-llm",
        )

        assert result.grounded is False
        assert result.trace_event is not None
        assert result.trace_event["goal_id"] == "goal-llm"
        # Two increments: the deterministic pass (fails) and the LLM pass
        # (confirms still ungrounded) each record their own outcome.
        assert _grounding_sample("ungrounded", "normal") == before + 2

    async def test_llm_pass_clears_residual_claims_records_grounded(self) -> None:
        checker = GroundingChecker(
            llm_provider=_FakeLLMProvider('{"grounded": ["JIRA-999"], "ungrounded": []}')
        )
        before = _grounding_sample("grounded", "normal")

        result = await checker.check(
            step_output="Found JIRA-999",
            tool_outputs=["unrelated evidence text"],
            goal_id="goal-llm",
        )

        assert result.grounded is True
        assert result.trace_event is None
        assert _grounding_sample("grounded", "normal") == before + 1
