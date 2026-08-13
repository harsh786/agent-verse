"""Evidence-bound generative reflection."""

from __future__ import annotations

import hashlib

from app.coordination.generative.models import Observation, Reflection


class ReflectionService:
    def __init__(self, *, importance_threshold: int, minimum_evidence: int) -> None:
        self._threshold = importance_threshold
        self._minimum = minimum_evidence

    def reflect(
        self, observations: tuple[Observation, ...], *, conclusion: str
    ) -> Reflection | None:
        eligible = tuple(
            item for item in observations if not item.quarantined and item.evidence_references
        )
        if len(eligible) < self._minimum:
            return None
        if sum(item.importance for item in eligible) < self._threshold:
            return None
        tenant_ids = {item.tenant_id for item in eligible}
        persona_ids = {item.persona_id for item in eligible}
        if len(tenant_ids) != 1 or len(persona_ids) != 1:
            raise PermissionError("reflection evidence crosses an isolation boundary")
        source_ids = tuple(sorted(item.observation_id for item in eligible))
        digest = hashlib.sha256(":".join(source_ids).encode()).hexdigest()
        return Reflection(
            reflection_id=digest,
            tenant_id=next(iter(tenant_ids)),
            persona_id=next(iter(persona_ids)),
            safe_conclusion=conclusion,
            source_observation_ids=source_ids,
            evidence_references=tuple(
                sorted({ref for item in eligible for ref in item.evidence_references})
            ),
        )


__all__ = ["ReflectionService"]
