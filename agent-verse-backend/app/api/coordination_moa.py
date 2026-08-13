"""Safe tenant-scoped Mixture-of-Agents layer explanations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

router = APIRouter(prefix="/api/v1/coordination/sessions", tags=["coordination-moa"])


def _tenant(request: Request) -> str:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
    return str(tenant.tenant_id)


def _repository(request: Request) -> Any:
    repository = getattr(request.app.state, "moa_repository", None)
    if repository is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "MoA runtime unavailable")
    return repository


def _layer_public(layer: Any, proposals: tuple[Any, ...]) -> dict[str, Any]:
    valid_deployments = sorted({item.deployment_id for item in proposals if item.valid})
    return {
        **layer.model_dump(mode="json"),
        "proposals": [item.model_dump(mode="json") for item in proposals],
        "valid_unique_deployments": valid_deployments,
        "quorum_met": len(valid_deployments) >= layer.quorum,
        "included_proposal_ids": [item.proposal_id for item in proposals if item.valid],
        "excluded": [
            {"proposal_id": item.proposal_id, "reason": item.rejection_reason or "invalid"}
            for item in proposals
            if not item.valid
        ],
    }


@router.get("/{session_id}/moa/layers", operation_id="list_moa_layers")
async def list_layers(
    request: Request,
    session_id: str,
    after_layer: int = Query(default=-1, ge=-1),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    tenant_id = _tenant(request)
    repository = _repository(request)
    layers = tuple(
        item
        for item in await repository.layers(tenant_id, session_id)
        if item.layer_index > after_layer
    )[:limit]
    items = []
    for layer in layers:
        proposals = await repository.proposals(
            tenant_id, layer.strategy_execution_id, layer.layer_index
        )
        items.append(_layer_public(layer, proposals))
    return {
        "items": items,
        "next_layer": layers[-1].layer_index if layers else after_layer,
        "has_more": len(layers) == limit,
    }


@router.get("/{session_id}/moa/layers/{layer_index}", operation_id="get_moa_layer")
async def get_layer(
    request: Request, session_id: str, layer_index: int
) -> dict[str, Any]:
    tenant_id = _tenant(request)
    repository = _repository(request)
    layer = next(
        (
            item
            for item in await repository.layers(tenant_id, session_id)
            if item.layer_index == layer_index
        ),
        None,
    )
    if layer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MoA layer not found")
    proposals = await repository.proposals(
        tenant_id, layer.strategy_execution_id, layer.layer_index
    )
    return _layer_public(layer, proposals)


__all__ = ["router"]
