"""Explicit auction lifecycle transitions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AuctionPhase = Literal[
    "announced",
    "bidding",
    "sealed",
    "scored",
    "allocated",
    "executing",
    "settled",
    "rebid",
    "failed",
    "cancelled",
]


class AuctionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    auction_id: str
    state: AuctionPhase
    round_number: int = Field(ge=0)
    last_idempotency_key: str | None = None


_TRANSITIONS: dict[str, frozenset[str]] = {
    "announced": frozenset({"bidding", "cancelled"}),
    "bidding": frozenset({"sealed", "cancelled"}),
    "sealed": frozenset({"scored", "failed", "cancelled"}),
    "scored": frozenset({"allocated", "rebid", "failed", "cancelled"}),
    "allocated": frozenset({"executing", "rebid", "cancelled"}),
    "executing": frozenset({"settled", "rebid", "failed", "cancelled"}),
    "rebid": frozenset({"bidding", "failed", "cancelled"}),
    "settled": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


def transition(state: AuctionState, target: AuctionPhase, *, idempotency_key: str) -> AuctionState:
    if state.last_idempotency_key == idempotency_key:
        return state
    if target not in _TRANSITIONS[state.state]:
        raise ValueError(f"illegal auction transition: {state.state} -> {target}")
    return state.model_copy(
        update={
            "state": target,
            "round_number": state.round_number
            + (1 if target == "bidding" and state.state == "rebid" else 0),
            "last_idempotency_key": idempotency_key,
        }
    )


__all__ = ["AuctionPhase", "AuctionState", "transition"]
