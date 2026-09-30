"""Tenant-scoped auction read model and opaque sealed-bid command."""

from typing import Any, cast

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.coordination.pattern_runs.service import public_record

router = APIRouter(prefix="/api/v1/coordination/sessions", tags=["coordination-auction"])


class SubmitSealedBidRequest(BaseModel):
    bidder_id: str = Field(min_length=1)
    bid_version: int = Field(gt=0)
    ciphertext: str = Field(min_length=1, max_length=64_000)
    nonce: str = Field(min_length=16, max_length=256)
    signature: str = Field(min_length=32, max_length=512)


def _tenant(request: Request) -> str:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
    return str(tenant.tenant_id)


@router.get("/{session_id}/auction", operation_id="get_auction_state")
async def get_auction_state(request: Request, session_id: str) -> dict[str, Any]:
    tenant_id = _tenant(request)
    records = await request.app.state.auction_repository.list_session(tenant_id, session_id)
    count = await request.app.state.auction_bid_inbox.count(tenant_id, session_id)
    runs = [public_record("market_auction", item) for item in records]
    return {
        # One row per allocation a sealed-bid run made (winner, score, fairness).
        "items": [run["view"]["allocation"] for run in runs if run["view"].get("allocation")],
        "runs": runs,
        "sealed_bid_count": count,
    }


@router.post("/{session_id}/auction/bids", operation_id="submit_sealed_auction_bid")
async def submit_bid(
    request: Request,
    session_id: str,
    body: SubmitSealedBidRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
) -> dict[str, Any]:
    try:
        receipt = await request.app.state.auction_bid_inbox.submit(
            tenant_id=_tenant(request),
            session_id=session_id,
            bidder_id=body.bidder_id,
            bid_version=body.bid_version,
            ciphertext=body.ciphertext,
            nonce=body.nonce,
            signature=body.signature,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return cast(dict[str, Any], receipt.model_dump(mode="json"))


__all__ = ["SubmitSealedBidRequest", "router"]
