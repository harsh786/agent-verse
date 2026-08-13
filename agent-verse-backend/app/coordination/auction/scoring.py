"""Byte-stable fixed-point auction ranking."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from app.coordination.auction.models import AuctionAnnouncement, RankedBid, RevealedBid


def _cost_score(cost: Decimal, ceiling: Decimal) -> int:
    value = ((ceiling - min(cost, ceiling)) / ceiling * Decimal(10_000)).quantize(
        Decimal("1"), rounding=ROUND_HALF_EVEN
    )
    return int(value)


def _latency_score(latency_ms: int) -> int:
    return max(0, 10_000 - min(latency_ms, 10_000))


def score_bids(
    announcement: AuctionAnnouncement, bids: tuple[RevealedBid, ...]
) -> tuple[RankedBid, ...]:
    weights = announcement.weights
    ranked: list[RankedBid] = []
    for bid in bids:
        payload = bid.payload
        components = {
            "quality": payload.quality,
            "cost": _cost_score(payload.cost, announcement.maximum_cost),
            "latency": _latency_score(payload.latency_ms),
            "confidence": payload.confidence,
            "fairness": max(0, min(10_000, 5_000 + payload.fairness)),
            "load": 10_000 - payload.load,
        }
        total = sum(components[name] * int(getattr(weights, name)) for name in components) // 10_000
        ranked.append(
            RankedBid(
                bidder_id=bid.bidder_id,
                total_score=total,
                quality_score=payload.quality,
                cost=payload.cost,
                submitted_at=bid.submitted_at,
                explanation=tuple(sorted(components.items())),
            )
        )
    return tuple(
        sorted(
            ranked,
            key=lambda item: (
                -item.total_score,
                -item.quality_score,
                item.cost,
                item.submitted_at,
                item.bidder_id,
            ),
        )
    )


__all__ = ["score_bids"]
