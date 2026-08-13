from __future__ import annotations

import pytest

from app.coordination.moa.models import MoALayer, MoAProposal
from app.coordination.moa.repository import InMemoryMoARepository


@pytest.mark.asyncio
async def test_moa_layers_and_proposals_are_idempotent_and_tenant_scoped() -> None:
    repository = InMemoryMoARepository()
    layer = MoALayer(
        layer_id="layer-1",
        tenant_id="tenant-a",
        session_id="session",
        strategy_execution_id="execution",
        layer_index=0,
        aggregator_deployment_id="aggregate",
        quorum=2,
        idempotency_key="layer-key",
    )
    assert await repository.create_layer(layer) == layer
    assert await repository.create_layer(layer) == layer
    proposal = MoAProposal(
        proposal_id="proposal-1",
        tenant_id="tenant-a",
        session_id="session",
        strategy_execution_id="execution",
        layer_index=0,
        participant_id="agent-a",
        provider_id="provider-a",
        model_family="family-a",
        deployment_id="deployment-a",
        region="in",
        failure_domain="domain-a",
        proposal_reference="artifact://proposal-1",
        safe_excerpt="answer",
        valid=True,
        attempt=1,
        idempotency_key="proposal-key",
    )
    assert await repository.save_proposal(proposal) == proposal
    assert await repository.save_proposal(proposal) == proposal
    assert await repository.layers("tenant-b", "session") == ()
    assert await repository.proposals("tenant-a", "execution", 0) == (proposal,)
