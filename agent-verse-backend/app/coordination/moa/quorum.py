"""Unique-deployment quorum calculation."""

from pydantic import BaseModel, ConfigDict

from app.coordination.moa.models import MoAProposal


class QuorumResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    met: bool
    required: int
    valid_unique_deployments: tuple[str, ...]


def evaluate_quorum(
    proposals: tuple[MoAProposal, ...], *, required: int
) -> QuorumResult:
    if required <= 0:
        raise ValueError("quorum must be positive")
    deployments = tuple(
        sorted({item.deployment_id for item in proposals if item.valid})
    )
    return QuorumResult(
        met=len(deployments) >= required,
        required=required,
        valid_unique_deployments=deployments,
    )


__all__ = ["QuorumResult", "evaluate_quorum"]
