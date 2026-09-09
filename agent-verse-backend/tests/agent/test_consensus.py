"""Tests for Phase 3 Track D (consensus verifier) and Track E (calibration)."""
from __future__ import annotations

import pytest

from app.agent.consensus import ConsensusVerifier, requires_consensus


class TestRequiresConsensus:
    def test_legal_domain_requires_consensus(self) -> None:
        assert requires_consensus("review contract", "legal", []) is True

    def test_general_domain_no_consensus(self) -> None:
        assert requires_consensus("find tickets", "software", []) is False

    def test_write_high_tool_risk_requires_consensus(self) -> None:
        assert requires_consensus("deploy app", None, ["write_high"]) is True

    def test_destructive_tool_risk_requires_consensus(self) -> None:
        assert requires_consensus("clear data", None, ["destructive"]) is True

    def test_no_tool_risks_and_no_domain(self) -> None:
        assert requires_consensus("list issues", None, []) is False

    def test_regulated_flag_requires_consensus(self) -> None:
        assert requires_consensus(regulated=True) is True

    def test_step_with_destructive_tool_requires_consensus(self) -> None:
        from app.agent.state import StepResult
        s = StepResult(description="delete it")
        s.tool_calls = [{"tool_name": "delete_issue", "server_name": "jira", "success": True}]
        assert requires_consensus(steps=[s]) is True

    def test_step_with_read_only_tool_no_consensus(self) -> None:
        from app.agent.state import StepResult
        s = StepResult(description="list")
        s.tool_calls = [{"tool_name": "search_issues", "server_name": "jira", "success": True}]
        assert requires_consensus(steps=[s], regulated=False) is False


class TestConsensusVerifier:
    @pytest.mark.asyncio
    async def test_single_verifier_works(self) -> None:
        from app.providers.fake import FakeProvider

        primary = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
        cv = ConsensusVerifier(primary_verifier=primary)
        result = await cv.verify("test goal", "step 1: done")
        assert result.success is True
        assert len(result.votes) == 1

    @pytest.mark.asyncio
    async def test_majority_two_vs_one(self) -> None:
        from app.providers.fake import FakeProvider

        primary = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
        cross = FakeProvider(responses=['{"success": true, "reason": "confirmed"}'])
        judge = FakeProvider(responses=['{"success": false, "reason": "missed step"}'])
        cv = ConsensusVerifier(
            primary_verifier=primary,
            cross_model_verifier=cross,
            judge_verifier=judge,
        )
        result = await cv.verify("test goal", "step 1: done")
        assert result.success is True   # 2 vs 1
        assert result.unanimous is False
        assert result.requires_hitl is True

    @pytest.mark.asyncio
    async def test_unanimous_no_hitl(self) -> None:
        from app.providers.fake import FakeProvider

        primary = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
        cross = FakeProvider(responses=['{"success": true, "reason": "confirmed"}'])
        cv = ConsensusVerifier(primary_verifier=primary, cross_model_verifier=cross)
        result = await cv.verify("test goal", "step 1: done")
        assert result.unanimous is True
        assert result.requires_hitl is False

    @pytest.mark.asyncio
    async def test_fail_closed_on_provider_error(self) -> None:
        """A verifier that throws an exception counts as a failure vote."""

        class BoomProvider:
            async def complete(self, req: object) -> object:
                raise RuntimeError("down")

        primary = BoomProvider()  # type: ignore[arg-type]
        cv = ConsensusVerifier(primary_verifier=primary)
        result = await cv.verify("test goal", "step 1: done")
        assert result.success is False  # fail-closed
        assert len(result.votes) == 1
        assert result.votes[0].success is False

    @pytest.mark.asyncio
    async def test_plan_style_constructor(self) -> None:
        """ConsensusVerifier accepts plan-style primary= keyword."""
        from app.providers.fake import FakeProvider

        primary = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
        cv = ConsensusVerifier(primary=primary)
        result = await cv.verify("goal", "summary")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_agreement_field_populated(self) -> None:
        from app.providers.fake import FakeProvider

        primary = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
        cross = FakeProvider(responses=['{"success": false, "reason": "fail"}'])
        cv = ConsensusVerifier(primary_verifier=primary, cross_model_verifier=cross)
        result = await cv.verify("goal", "summary")
        # 1 success out of 2 → agreement = 0.5
        assert abs(result.agreement - 0.5) < 0.01


class TestCalibrationStore:
    @pytest.mark.asyncio
    async def test_record_and_false_confirm_rate(self) -> None:
        from app.intelligence.verifier_calibration import VerifierCalibrationStore

        store = VerifierCalibrationStore()

        # 9 correct predictions (verifier said True, actual was True)
        for i in range(9):
            rid = await store.record_verdict(
                goal_id=f"g{i}",
                tenant_id="t1",
                verifier_verdict=True,
                goal_text="test",
            )
            await store.record_actual_outcome(rid, actual_success=True)

        # 1 false positive: verifier said True, actual was False
        rid_fp = await store.record_verdict(
            goal_id="g-fp",
            tenant_id="t1",
            verifier_verdict=True,
            goal_text="test",
        )
        await store.record_actual_outcome(rid_fp, actual_success=False)

        rate = store.false_confirm_rate("t1")
        assert rate["false_positives"] == 1
        assert rate["total"] == 10
        assert abs(rate["rate"] - 0.1) < 0.001
        assert rate["on_target"] is False  # 10% > 2% target

    @pytest.mark.asyncio
    async def test_empty_returns_zero(self) -> None:
        from app.intelligence.verifier_calibration import VerifierCalibrationStore

        store = VerifierCalibrationStore()
        rate = store.false_confirm_rate("t1")
        assert rate["rate"] == 0.0
        assert rate["total"] == 0
        assert rate["on_target"] is True

    @pytest.mark.asyncio
    async def test_false_negatives_counted(self) -> None:
        from app.intelligence.verifier_calibration import VerifierCalibrationStore

        store = VerifierCalibrationStore()
        # verifier said False (missed success), actual was True → false negative
        rid = await store.record_verdict(
            goal_id="g-fn",
            tenant_id="t1",
            verifier_verdict=False,
            goal_text="test",
        )
        await store.record_actual_outcome(rid, actual_success=True)

        rate = store.false_confirm_rate("t1")
        assert rate["false_negatives"] == 1
        assert rate["false_positives"] == 0
        assert rate["rate"] == 0.0  # false-confirm rate only counts false positives

    @pytest.mark.asyncio
    async def test_tenant_isolation(self) -> None:
        from app.intelligence.verifier_calibration import VerifierCalibrationStore

        store = VerifierCalibrationStore()
        # Record for tenant t1
        rid1 = await store.record_verdict(
            goal_id="g1", tenant_id="t1", verifier_verdict=True, goal_text=""
        )
        await store.record_actual_outcome(rid1, actual_success=False)

        # Record for tenant t2
        rid2 = await store.record_verdict(
            goal_id="g2", tenant_id="t2", verifier_verdict=True, goal_text=""
        )
        await store.record_actual_outcome(rid2, actual_success=True)

        rate_t1 = store.false_confirm_rate("t1")
        rate_t2 = store.false_confirm_rate("t2")
        assert rate_t1["false_positives"] == 1
        assert rate_t2["false_positives"] == 0

    @pytest.mark.asyncio
    async def test_unresolved_records_excluded_from_rate(self) -> None:
        from app.intelligence.verifier_calibration import VerifierCalibrationStore

        store = VerifierCalibrationStore()
        # Record without calling record_actual_outcome — should not count
        await store.record_verdict(
            goal_id="g-pending", tenant_id="t1", verifier_verdict=True, goal_text=""
        )
        rate = store.false_confirm_rate("t1")
        assert rate["total"] == 0  # no resolved records
