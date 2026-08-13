from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.coordination_moa import router
from app.coordination.moa.models import MoALayer, MoAProposal
from app.coordination.moa.repository import InMemoryMoARepository
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware


def _app() -> FastAPI:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return TenantContext(
            tenant_id=f"tenant-{key}", plan=PlanTier.PROFESSIONAL, api_key_id=key
        )

    repository = InMemoryMoARepository()
    layer = MoALayer(
        layer_id="layer",
        tenant_id="tenant-a",
        session_id="session",
        strategy_execution_id="execution",
        layer_index=0,
        aggregator_deployment_id="aggregator",
        quorum=1,
        deployment_ids=("deployment",),
        idempotency_key="layer",
    )
    proposal = MoAProposal(
        proposal_id="proposal",
        tenant_id="tenant-a",
        session_id="session",
        strategy_execution_id="execution",
        layer_index=0,
        participant_id="agent",
        provider_id="provider",
        model_family="family",
        deployment_id="deployment",
        region="in",
        failure_domain="domain",
        proposal_reference="artifact://proposal",
        safe_excerpt="safe excerpt",
        valid=True,
        attempt=1,
        idempotency_key="proposal",
    )
    asyncio.run(repository.create_layer(layer))
    asyncio.run(repository.save_proposal(proposal))
    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(router)
    app.state.moa_repository = repository
    return app


def test_moa_api_explains_quorum_without_cross_tenant_disclosure() -> None:
    client = TestClient(_app())
    response = client.get(
        "/api/v1/coordination/sessions/session/moa/layers",
        headers={"X-API-Key": "a"},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["quorum_met"] is True
    assert response.json()["items"][0]["proposals"][0]["safe_excerpt"] == "safe excerpt"
    denied = client.get(
        "/api/v1/coordination/sessions/session/moa/layers/0",
        headers={"X-API-Key": "b"},
    )
    assert denied.status_code == 404
