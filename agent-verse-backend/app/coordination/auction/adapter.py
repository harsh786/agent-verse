"""Deterministic market-auction strategy adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.coordination.auction.allocator import AuctionAllocator
from app.orchestration.strategy_adapters import ExecutionTier


@dataclass(frozen=True, slots=True)
class MarketAuctionAdapter:
    strategy_id: str = "market_auction"
    execution_tier: ExecutionTier = ExecutionTier.DISTRIBUTED

    def create_runtime(self, **kwargs: Any) -> AuctionAllocator:
        if kwargs:
            raise TypeError("market auction runtime takes no constructor arguments")
        return AuctionAllocator()


__all__ = ["MarketAuctionAdapter"]
