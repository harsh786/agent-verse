from app.coordination.moa.models import MoAProposal
from app.coordination.moa.quorum import evaluate_quorum


def _proposal(identifier: str, deployment: str, *, valid: bool = True) -> MoAProposal:
    return MoAProposal(
        proposal_id=identifier,
        tenant_id="tenant",
        session_id="session",
        strategy_execution_id="execution",
        layer_index=0,
        participant_id=identifier,
        provider_id=identifier,
        model_family=identifier,
        deployment_id=deployment,
        region="in",
        failure_domain=identifier,
        proposal_reference=f"artifact://{identifier}",
        safe_excerpt="safe",
        valid=valid,
        attempt=1,
        idempotency_key=identifier,
    )


def test_quorum_counts_valid_unique_deployments_not_responses() -> None:
    result = evaluate_quorum(
        (
            _proposal("a", "same"),
            _proposal("alias", "same"),
            _proposal("bad", "other", valid=False),
        ),
        required=2,
    )
    assert not result.met and result.valid_unique_deployments == ("same",)
    met = evaluate_quorum((_proposal("a", "one"), _proposal("b", "two")), required=2)
    assert met.met
