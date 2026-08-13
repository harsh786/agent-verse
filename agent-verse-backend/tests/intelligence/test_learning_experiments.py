from app.intelligence.learning_experiments import ExperimentOutcome, LearningExperimentService
from app.memory.contracts import ExperimentSpec


def _spec(*, tenant: str = "tenant", identifier: str = "exp") -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id=identifier,
        tenant_id=tenant,
        agent_id="agent",
        kind="prompt",
        target_key="planner",
        control_version="v1",
        candidate_version="v2",
        assignment_seed="seed",
        traffic_percent=50,
        primary_metric="quality",
        guardrail_metrics=("cost", "latency"),
        min_samples_per_arm=1,
        confidence_threshold=0.95,
        status="running",
    )


def test_assignment_is_sticky_and_kill_switch_returns_control() -> None:
    service = LearningExperimentService()
    spec = service.register(_spec())
    assert service.assign(spec, fingerprint="goal") == service.assign(spec, fingerprint="goal")
    stopped = spec.model_copy(update={"kill_switch": True})
    assert service.assign(stopped, fingerprint="goal")[1] == "control"


def test_one_running_experiment_per_tenant_target() -> None:
    service = LearningExperimentService()
    service.register(_spec())
    try:
        service.register(_spec(identifier="other"))
    except ValueError as exc:
        assert "active experiment" in str(exc)
    else:
        raise AssertionError("concurrent target ownership must fail")


def test_promotion_is_tenant_scoped_and_guardrail_bounded() -> None:
    service = LearningExperimentService()
    spec = service.register(_spec())
    service.record("other", ExperimentOutcome("a", "control", 0.1, True))
    service.record("other", ExperimentOutcome("b", "candidate", 0.9, True))
    assert not service.promotion_ready(spec)
    service.record("tenant", ExperimentOutcome("a", "control", 0.5, True))
    service.record("tenant", ExperimentOutcome("b", "candidate", 0.9, False))
    assert not service.promotion_ready(spec)
