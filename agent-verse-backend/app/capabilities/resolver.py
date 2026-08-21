from __future__ import annotations

from app.capabilities.registry import CapabilityRegistry
from app.capabilities.schema import CapabilityKind, CapabilityProfile, RiskLevel


class CapabilityResolver:
    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry

    def find_tools_for_modalities(
        self,
        input_modalities: list[str],
        *,
        output_modalities: list[str] | None = None,
        max_risk: RiskLevel = RiskLevel.HIGH,
    ) -> list[CapabilityProfile]:
        tools = self._registry.filter(kind=CapabilityKind.TOOL, max_risk=max_risk)
        results = [t for t in tools if any(m in t.input_modalities for m in input_modalities)]
        if output_modalities:
            results = [
                t for t in results if any(m in t.output_modalities for m in output_modalities)
            ]
        return results
