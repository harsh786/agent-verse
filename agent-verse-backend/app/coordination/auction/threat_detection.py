"""Deterministic auction identity and collusion threat checks."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BidIdentity:
    bidder_id: str
    credential_id: str
    deployment_id: str
    signature: str


def detect_threats(identities: tuple[BidIdentity, ...]) -> tuple[str, ...]:
    reasons: list[str] = []
    credentials = Counter(item.credential_id for item in identities)
    deployments = Counter(item.deployment_id for item in identities)
    bidders = Counter(item.bidder_id for item in identities)
    signatures = Counter(item.signature for item in identities)
    if any(count > 1 for count in bidders.values()):
        reasons.append("duplicate_bidder")
    if any(count > 1 for count in credentials.values()):
        reasons.append("duplicate_credential")
    if any(count > 1 for count in deployments.values()):
        reasons.append("duplicate_deployment")
    if any(count > 1 for count in signatures.values()):
        reasons.append("replayed_signature")
    return tuple(sorted(reasons))


__all__ = ["BidIdentity", "detect_threats"]
