"""Deterministic independent-deployment diversity enforcement."""

from __future__ import annotations

from app.coordination.moa.models import ModelCandidate


class DiversityPolicy:
    def __init__(
        self,
        minimum_providers: int,
        minimum_model_families: int,
        minimum_failure_domains: int,
    ) -> None:
        self._providers = minimum_providers
        self._families = minimum_model_families
        self._domains = minimum_failure_domains

    def select(
        self,
        candidates: tuple[ModelCandidate, ...],
        *,
        count: int,
        region: str,
        required_capabilities: frozenset[str],
        allow_degraded: bool = False,
    ) -> tuple[ModelCandidate, ...]:
        eligible: dict[str, ModelCandidate] = {}
        for candidate in sorted(candidates, key=lambda item: item.candidate_id):
            if (
                candidate.healthy
                and candidate.region == region
                and required_capabilities <= candidate.capabilities
            ):
                eligible.setdefault(candidate.deployment_id, candidate)
        selected = tuple(eligible.values())[:count]
        independent = (
            len({item.provider_id for item in selected}) >= self._providers
            and len({item.model_family for item in selected}) >= self._families
            and len({item.failure_domain for item in selected}) >= self._domains
        )
        if len(selected) < count or (not independent and not allow_degraded):
            raise ValueError("requested model diversity is unavailable")
        return selected


__all__ = ["DiversityPolicy"]
