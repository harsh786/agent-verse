"""Auction contracts using fixed-point score inputs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScoreWeights(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quality: int = Field(ge=0)
    cost: int = Field(ge=0)
    latency: int = Field(ge=0)
    confidence: int = Field(ge=0)
    fairness: int = Field(ge=0)
    load: int = Field(ge=0)

    @model_validator(mode="after")
    def total_is_basis_points(self) -> ScoreWeights:
        if sum(self.model_dump().values()) != 10_000:
            raise ValueError("auction weights must total 10000 basis points")
        return self


class AuctionAnnouncement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    auction_id: str
    tenant_id: str
    work_item_id: str
    deadline: datetime
    eligible_bidder_ids: frozenset[str]
    required_capabilities: frozenset[str]
    maximum_cost: Decimal = Field(gt=0)
    weights: ScoreWeights
    scoring_policy_version: str


class BidPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quality: int = Field(ge=0, le=10_000)
    cost: Decimal = Field(ge=0)
    latency_ms: int = Field(ge=0)
    confidence: int = Field(ge=0, le=10_000)
    fairness: int = Field(ge=-1_000, le=1_000)
    load: int = Field(ge=0, le=10_000)
    capabilities: frozenset[str]
    commitment: str


class SealedBid(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    auction_id: str
    bidder_id: str
    version: int = Field(gt=0)
    submitted_at: datetime
    ciphertext: str
    nonce: str
    signature: str


class RevealedBid(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bidder_id: str
    version: int
    submitted_at: datetime
    payload: BidPayload


class RankedBid(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bidder_id: str
    total_score: int
    quality_score: int
    cost: Decimal
    submitted_at: datetime
    explanation: tuple[tuple[str, int], ...]


__all__ = [
    "AuctionAnnouncement",
    "BidPayload",
    "RankedBid",
    "RevealedBid",
    "ScoreWeights",
    "SealedBid",
]
