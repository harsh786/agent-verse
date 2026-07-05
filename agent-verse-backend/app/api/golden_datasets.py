"""Golden Dataset management API."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/eval/golden-datasets", tags=["eval"])


class DatasetCreateRequest(BaseModel):
    name: str
    domain: str | None = None
    description: str = ""
    split: str = "regression"  # regression | holdout


class DatasetItemRequest(BaseModel):
    goal: str
    expected_output: str | None = None
    human_label: bool | None = None
    metadata: dict = {}


@router.get("")
async def list_golden_datasets(request: Request) -> dict:
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Auth required")
    return {"datasets": [], "total": 0}


@router.post("")
async def create_golden_dataset(body: DatasetCreateRequest, request: Request) -> dict:
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Auth required")
    return {
        "id": uuid.uuid4().hex,
        "name": body.name,
        "domain": body.domain,
        "split": body.split,
        "item_count": 0,
    }


@router.post("/{dataset_id}/items")
async def add_golden_item(
    dataset_id: str, body: DatasetItemRequest, request: Request
) -> dict:
    """Add a goal result to a golden dataset (promote to golden)."""
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Auth required")
    return {
        "id": uuid.uuid4().hex,
        "dataset_id": dataset_id,
        "goal": body.goal,
        "human_label": body.human_label,
    }


@router.post("/promote-goal/{goal_id}")
async def promote_goal_to_golden(
    goal_id: str, request: Request, dataset_id: str | None = None
) -> dict:
    """Promote a completed goal to a golden dataset item."""
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Auth required")
    return {
        "goal_id": goal_id,
        "status": "promoted",
        "dataset_id": dataset_id or "default",
    }
