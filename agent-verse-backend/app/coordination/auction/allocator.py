"""Idempotent fenced auction allocation and settlement."""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StaleWinnerError(RuntimeError):
    pass


class Allocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    auction_id: str
    work_item_id: str
    winner_agent_id: str
    fencing_token: int = Field(gt=0)
    lease_expires_at: datetime
    fairness_adjustment: Decimal
    explanation: dict[str, Any]
    state: Literal["allocated", "settled"] = "allocated"
    outcome_reference: str | None = None


class AuctionAllocator:
    def __init__(self) -> None:
        self._allocations: dict[tuple[str, str], Allocation] = {}
        self._commands: dict[tuple[str, str], Allocation] = {}
        self._lock = asyncio.Lock()

    async def allocate(
        self,
        *,
        tenant_id: str,
        auction_id: str,
        work_item_id: str,
        winner_agent_id: str,
        fairness_adjustment: Decimal,
        explanation: dict[str, Any],
        lease_expires_at: datetime,
        idempotency_key: str,
    ) -> Allocation:
        command = (tenant_id, idempotency_key)
        async with self._lock:
            if command in self._commands:
                return self._commands[command]
            key = (tenant_id, auction_id)
            prior = self._allocations.get(key)
            if prior is not None:
                raise ValueError("auction is already allocated")
            allocation = Allocation(
                tenant_id=tenant_id,
                auction_id=auction_id,
                work_item_id=work_item_id,
                winner_agent_id=winner_agent_id,
                fencing_token=1,
                lease_expires_at=lease_expires_at,
                fairness_adjustment=fairness_adjustment,
                explanation=explanation,
            )
            self._allocations[key] = allocation
            self._commands[command] = allocation
            return allocation

    async def settle(
        self,
        tenant_id: str,
        auction_id: str,
        *,
        winner_agent_id: str,
        fencing_token: int,
        outcome_reference: str,
        idempotency_key: str,
    ) -> Allocation:
        command = (tenant_id, idempotency_key)
        async with self._lock:
            if command in self._commands:
                return self._commands[command]
            key = (tenant_id, auction_id)
            current = self._allocations.get(key)
            if (
                current is None
                or current.winner_agent_id != winner_agent_id
                or current.fencing_token != fencing_token
            ):
                raise StaleWinnerError("stale auction winner")
            if current.state == "settled":
                raise ValueError("allocation already settled")
            settled = current.model_copy(
                update={"state": "settled", "outcome_reference": outcome_reference}
            )
            self._allocations[key] = settled
            self._commands[command] = settled
            return settled


__all__ = ["Allocation", "AuctionAllocator", "StaleWinnerError"]
