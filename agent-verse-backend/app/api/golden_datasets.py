"""Golden Dataset management API — not implemented (honest 501).

These endpoints used to answer create / add-item / promote with freshly minted
ids and ``"status": "promoted"`` while persisting nothing, and ``promote-goal``
accepted any goal id, including another tenant's. The ``golden_datasets`` /
``golden_dataset_items`` tables (migration 0076) exist, but no eval runner reads
them, so storing rows there would still be a feature that does nothing.

Runnable golden tasks already exist and are what callers should use:

* ``POST /ai-ops/datasets`` + ``POST /ai-ops/datasets/{id}/run`` — datasets of
  golden tasks executed as real goals and scored by the AI-Ops runner.
* ``/enterprise`` eval suites — tenant-scoped suites of golden tasks.

The routes and request models stay so clients get a clear 501 instead of a 404.
"""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/eval/golden-datasets", tags=["eval"])

_NOT_IMPLEMENTED = (
    "Golden datasets are not implemented: nothing would be stored or evaluated. "
    "Use POST /ai-ops/datasets (golden tasks run by the AI-Ops eval runner) or "
    "the /enterprise eval suites instead."
)


class DatasetCreateRequest(BaseModel):
    name: str
    domain: str | None = None
    description: str = ""
    split: str = "regression"  # regression | holdout


class DatasetItemRequest(BaseModel):
    goal: str
    expected_output: str | None = None
    human_label: bool | None = None
    metadata: dict[str, Any] = {}


def _not_implemented(request: Request) -> NoReturn:
    if getattr(request.state, "tenant", None) is None:
        raise HTTPException(status_code=401, detail="Auth required")
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.get("")
async def list_golden_datasets(request: Request) -> dict[str, Any]:
    _not_implemented(request)


@router.post("")
async def create_golden_dataset(body: DatasetCreateRequest, request: Request) -> dict[str, Any]:
    _not_implemented(request)


@router.post("/{dataset_id}/items")
async def add_golden_item(
    dataset_id: str, body: DatasetItemRequest, request: Request
) -> dict[str, Any]:
    """Add a goal result to a golden dataset (not implemented)."""
    _not_implemented(request)


@router.post("/promote-goal/{goal_id}")
async def promote_goal_to_golden(
    goal_id: str, request: Request, dataset_id: str | None = None
) -> dict[str, Any]:
    """Promote a completed goal to a golden dataset item (not implemented)."""
    _not_implemented(request)
