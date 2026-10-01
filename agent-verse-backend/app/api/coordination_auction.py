"""Tenant-scoped auction read model, registry-backed sealed-bid auctions and bids.

``POST .../auctions`` opens a sealed-bid auction with a per-auction key pair (the
private key and bidder signing secrets are kept only as vault ciphertext);
bidders seal bids to the published public key and sign them with their secret;
``POST .../auctions/{id}/close`` closes the window, unseals, scores and allocates.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.coordination import _authorization
from app.coordination.auction.registry import AuctionRegistryError
from app.coordination.pattern_runs.service import public_record

router = APIRouter(prefix="/api/v1/coordination/sessions", tags=["coordination-auction"])


class SubmitSealedBidRequest(BaseModel):
    bidder_id: str = Field(min_length=1)
    bid_version: int = Field(gt=0)
    ciphertext: str = Field(min_length=1, max_length=64_000)
    nonce: str = Field(min_length=16, max_length=256)
    signature: str = Field(min_length=32, max_length=512)
    # The registry auction the bid is sealed for; omitted -> the session's only
    # open auction (legacy registry-less intake when none is open).
    auction_id: str | None = Field(default=None, min_length=1, max_length=64)


class OpenAuctionRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=4_000)
    eligible_bidder_ids: list[str] = Field(min_length=1, max_length=50)
    maximum_cost: Decimal = Field(gt=0, le=Decimal("1000000"))
    bid_window_seconds: int = Field(default=3_600, ge=10, le=7 * 86_400)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)


def _registry_service(request: Request) -> Any:
    service = getattr(request.app.state, "auction_registry_service", None)
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Auction registry unavailable")
    return service


def _require_operator(request: Request) -> Any:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
    if "coordination:create" not in _authorization(tenant).permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "coordination:create permission required")
    return tenant


async def _require_session(request: Request, tenant: Any, session_id: str) -> None:
    service = getattr(request.app.state, "coordination_service", None)
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Coordination unavailable")
    try:
        await service.get_session(tenant, session_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found") from exc


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
    tenant_id = _tenant(request)
    registry = getattr(request.app.state, "auction_registry_service", None)
    try:
        auction = (
            await registry.resolve_open_auction(tenant_id, session_id, body.auction_id)
            if registry is not None
            else None
        )
        if auction is not None:
            # Registry auction: window, eligibility and signature are checked now;
            # the envelope stays sealed until close.
            receipt = await registry.accept_bid(
                auction,
                bidder_id=body.bidder_id,
                bid_version=body.bid_version,
                ciphertext=body.ciphertext,
                nonce=body.nonce,
                signature=body.signature,
                idempotency_key=idempotency_key,
            )
        elif body.auction_id is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Auction not found")
        else:
            receipt = await request.app.state.auction_bid_inbox.submit(
                tenant_id=tenant_id,
                session_id=session_id,
                bidder_id=body.bidder_id,
                bid_version=body.bid_version,
                ciphertext=body.ciphertext,
                nonce=body.nonce,
                signature=body.signature,
                idempotency_key=idempotency_key,
            )
    except AuctionRegistryError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    response = cast(dict[str, Any], receipt.model_dump(mode="json"))
    response["auction_id"] = auction.auction_id if auction is not None else None
    return response


@router.post("/{session_id}/auctions", operation_id="open_sealed_bid_auction", status_code=201)
async def open_auction(
    request: Request,
    session_id: str,
    body: OpenAuctionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> dict[str, Any]:
    """Open an auction; returns the public key and each bidder's signing secret."""
    tenant = _require_operator(request)
    await _require_session(request, tenant, session_id)
    bidders = tuple(dict.fromkeys(b.strip()[:100] for b in body.eligible_bidder_ids if b.strip()))
    if not bidders:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "eligible bidders required")
    record, secrets_by_bidder = await _registry_service(request).open_auction(
        tenant_id=str(tenant.tenant_id),
        session_id=session_id,
        objective=body.objective,
        bidders=bidders,
        maximum_cost=body.maximum_cost,
        window=timedelta(seconds=body.bid_window_seconds),
        required_capabilities=frozenset(body.required_capabilities),
        idempotency_key=idempotency_key,
    )
    # Distribute each secret to its bidder only; the server keeps them sealed.
    return {**record.public(), "bidder_signing_secrets": secrets_by_bidder}


@router.get("/{session_id}/auctions/{auction_id}", operation_id="get_sealed_bid_auction")
async def get_auction(request: Request, session_id: str, auction_id: str) -> dict[str, Any]:
    try:
        record = await _registry_service(request).get(_tenant(request), session_id, auction_id)
    except AuctionRegistryError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return cast(dict[str, Any], record.public())


@router.post("/{session_id}/auctions/{auction_id}/close", operation_id="close_sealed_bid_auction")
async def close_auction(request: Request, session_id: str, auction_id: str) -> dict[str, Any]:
    """Close the bid window, unseal every bidder's latest bid, score and allocate."""
    tenant = _require_operator(request)
    try:
        record = await _registry_service(request).close(
            str(tenant.tenant_id), session_id, auction_id
        )
    except AuctionRegistryError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return cast(dict[str, Any], record.public())


__all__ = ["OpenAuctionRequest", "SubmitSealedBidRequest", "router"]
