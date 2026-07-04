"""Tests for Phase 11 experiment registry."""
import pytest


class TestExperimentRegistry:
    def _make(self):
        from app.intelligence.experiment_registry import ExperimentRegistry

        return ExperimentRegistry()

    def test_propose_returns_experiment(self):
        reg = self._make()
        exp = reg.propose(
            tenant_id="t1",
            agent_id="a1",
            name="test-variant",
            experiment_type="prompt_variant",
            config={"prompt": "new prompt"},
        )
        assert exp["id"] is not None
        assert exp["status"] == "proposed"

    def test_one_active_per_agent_enforced(self):
        reg = self._make()
        reg.propose(
            tenant_id="t1", agent_id="a1", name="exp1", experiment_type="prompt", config={}
        )
        with pytest.raises(ValueError, match="already active"):
            reg.propose(
                tenant_id="t1", agent_id="a1", name="exp2", experiment_type="prompt", config={}
            )

    def test_different_agents_can_have_simultaneous_experiments(self):
        reg = self._make()
        reg.propose(
            tenant_id="t1", agent_id="a1", name="exp-a1", experiment_type="prompt", config={}
        )
        reg.propose(
            tenant_id="t1", agent_id="a2", name="exp-a2", experiment_type="prompt", config={}
        )
        assert len(reg._experiments) == 2

    def test_insufficient_data_decision(self):
        reg = self._make()
        exp = reg.propose(
            tenant_id="t1", agent_id="a1", name="test", experiment_type="prompt", config={}
        )
        result = reg.evaluate(exp["id"])
        assert result["decision"] == "insufficient_data"

    def test_promote_on_sufficient_improvement(self):
        from app.intelligence.experiment_registry import MIN_SAMPLES_PER_ARM

        reg = self._make()
        exp = reg.propose(
            tenant_id="t1", agent_id="a1", name="test", experiment_type="prompt", config={}
        )

        # Record control scores
        for _ in range(MIN_SAMPLES_PER_ARM):
            reg.record_outcome(exp["id"], arm="control", score=0.7)

        # Record treatment scores (significantly better)
        for _ in range(MIN_SAMPLES_PER_ARM):
            reg.record_outcome(exp["id"], arm="treatment", score=0.85)

        result = reg.evaluate(exp["id"])
        assert result["decision"] == "promote"
        assert result["improvement_pct"] > 5

    def test_welford_variance_not_fabricated(self):
        """Variance must be real, not mean*0.3."""
        from app.intelligence.experiment_registry import ExperimentRegistry

        reg = ExperimentRegistry()
        exp = reg.propose(
            tenant_id="t1", agent_id="a1", name="test", experiment_type="prompt", config={}
        )

        # All control scores are identical → variance should be 0
        for _ in range(5):
            reg.record_outcome(exp["id"], arm="control", score=0.8)

        ctrl = exp.get("control_metrics", {})
        assert ctrl.get("variance", 0) == pytest.approx(0.0, abs=0.001), (
            "Variance should be 0 for identical scores, not fabricated"
        )

    def test_promote_clears_active_lock(self):
        reg = self._make()
        exp = reg.propose(
            tenant_id="t1", agent_id="a1", name="exp1", experiment_type="prompt", config={}
        )
        reg.promote(exp["id"])

        # Should now be able to propose a new experiment
        exp2 = reg.propose(
            tenant_id="t1", agent_id="a1", name="exp2", experiment_type="prompt", config={}
        )
        assert exp2["status"] == "proposed"
