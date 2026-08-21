"""Safe attributed aggregation input without private or executable reasoning."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from app.coordination.moa.models import MoAProposal

_INJECTION = re.compile(
    r"(?:ignore|override).{0,30}(?:instruction|policy)|reveal.{0,20}(?:secret|credential)",
    re.IGNORECASE,
)


class AggregationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt: str
    included_proposal_ids: tuple[str, ...]
    excluded: tuple[tuple[str, str], ...]
    evidence_references: tuple[str, ...]


def build_aggregation_input(
    proposals: tuple[MoAProposal, ...], *, maximum_characters: int
) -> AggregationInput:
    if maximum_characters <= 0:
        raise ValueError("aggregation context bound must be positive")
    included: list[MoAProposal] = []
    excluded: list[tuple[str, str]] = []
    for proposal in proposals:
        if not proposal.valid:
            excluded.append((proposal.proposal_id, proposal.rejection_reason or "invalid"))
            continue
        if _INJECTION.search(proposal.safe_excerpt):
            raise ValueError("unsafe proposal content")
        included.append(proposal)
    parts = [
        (
            f"proposal={item.proposal_id}; deployment={item.deployment_id}; "
            f"evidence={','.join(item.evidence_references)}; data={item.safe_excerpt}"
        )
        for item in included
    ]
    prompt = "\n".join(parts)
    if len(prompt) > maximum_characters:
        raise ValueError("aggregation context exceeds bound")
    return AggregationInput(
        prompt=prompt,
        included_proposal_ids=tuple(item.proposal_id for item in included),
        excluded=tuple(excluded),
        evidence_references=tuple(
            reference for item in included for reference in item.evidence_references
        ),
    )


__all__ = ["AggregationInput", "build_aggregation_input"]
