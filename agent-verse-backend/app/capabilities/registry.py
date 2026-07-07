from __future__ import annotations
from app.capabilities.schema import CapabilityKind, CapabilityProfile, RiskLevel


class CapabilityRegistry:
    def __init__(self, entries: list[CapabilityProfile]) -> None:
        self._by_id: dict[str, CapabilityProfile] = {e.capability_id: e for e in entries}

    def get(self, capability_id: str) -> CapabilityProfile | None:
        return self._by_id.get(capability_id)

    def list_by_kind(self, kind: CapabilityKind) -> list[CapabilityProfile]:
        return [c for c in self._by_id.values() if c.kind == kind]

    def filter(
        self,
        *,
        kind: CapabilityKind | None = None,
        max_risk: RiskLevel | None = None,
        required_modality: str | None = None,
    ) -> list[CapabilityProfile]:
        order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        results = list(self._by_id.values())
        if kind:
            results = [c for c in results if c.kind == kind]
        if max_risk:
            max_idx = order.index(max_risk)
            results = [c for c in results if order.index(c.risk_level) <= max_idx]
        if required_modality:
            results = [c for c in results if required_modality in c.input_modalities]
        return results

    def list_all(self) -> list[CapabilityProfile]:
        return list(self._by_id.values())


def build_default_capability_registry() -> CapabilityRegistry:
    T = CapabilityKind.TOOL
    M = CapabilityKind.MODEL
    R = CapabilityKind.RETRIEVER
    E = CapabilityKind.EMBEDDER
    G = CapabilityKind.GUARDRAIL
    entries = [
        CapabilityProfile("tool:web_search", T, "platform", ["text"], ["text", "json"],
            RiskLevel.LOW, "low", "interactive", 0.95, [], "Web search"),
        CapabilityProfile("tool:code_interpreter", T, "platform", ["text", "code"], ["text", "json"],
            RiskLevel.MEDIUM, "medium", "interactive", 0.90, ["code_execution"], "Python sandbox"),
        CapabilityProfile("tool:file_ops", T, "platform", ["text"], ["text", "json"],
            RiskLevel.MEDIUM, "low", "interactive", 0.98, [], "File operations"),
        CapabilityProfile("tool:shell", T, "platform", ["text"], ["text"],
            RiskLevel.HIGH, "low", "interactive", 0.88, ["shell_access"], "Shell commands"),
        CapabilityProfile("tool:http", T, "platform", ["text", "json"], ["text", "json"],
            RiskLevel.MEDIUM, "low", "realtime", 0.92, [], "HTTP requests"),
        CapabilityProfile("tool:document_parser", T, "platform", ["text", "pdf", "docx"], ["text"],
            RiskLevel.LOW, "low", "interactive", 0.97, [], "Parse documents"),
        CapabilityProfile("tool:artifact", T, "platform", ["text"], ["text", "json"],
            RiskLevel.LOW, "free", "realtime", 0.99, [], "Store artifacts"),
        CapabilityProfile("model:text_generation", M, "platform", ["text"], ["text"],
            RiskLevel.LOW, "medium", "interactive", 0.99, [], "Text generation"),
        CapabilityProfile("model:vision", M, "platform", ["text", "image"], ["text"],
            RiskLevel.LOW, "high", "interactive", 0.97, [], "Vision model"),
        CapabilityProfile("model:code", M, "platform", ["text", "code"], ["text", "code"],
            RiskLevel.LOW, "medium", "interactive", 0.98, [], "Code model"),
        CapabilityProfile("model:embedding", M, "platform", ["text"], ["vector"],
            RiskLevel.LOW, "low", "realtime", 0.999, [], "Embedding model"),
        CapabilityProfile("retriever:vector", R, "tenant", ["text", "vector"], ["text"],
            RiskLevel.LOW, "free", "realtime", 0.97, [], "Vector search"),
        CapabilityProfile("retriever:web", R, "platform", ["text"], ["text"],
            RiskLevel.LOW, "low", "interactive", 0.90, [], "Web retrieval"),
        CapabilityProfile("retriever:graph", R, "tenant", ["text"], ["text", "json"],
            RiskLevel.LOW, "low", "interactive", 0.88, [], "Graph traversal"),
        CapabilityProfile("embedder:text", E, "platform", ["text"], ["vector"],
            RiskLevel.LOW, "low", "realtime", 0.999, [], "Text embedding"),
        CapabilityProfile("embedder:code", E, "platform", ["code", "text"], ["vector"],
            RiskLevel.LOW, "low", "realtime", 0.99, [], "Code embedding"),
        CapabilityProfile("embedder:multimodal", E, "platform", ["text", "image"], ["vector"],
            RiskLevel.LOW, "medium", "interactive", 0.95, [], "Multimodal embedding"),
        CapabilityProfile("guardrail:injection", G, "platform", ["text"], ["bool"],
            RiskLevel.LOW, "free", "realtime", 0.99, [], "Injection scanner"),
        CapabilityProfile("guardrail:pii", G, "platform", ["text"], ["bool", "text"],
            RiskLevel.LOW, "free", "realtime", 0.97, [], "PII detection"),
        CapabilityProfile("guardrail:toxicity", G, "platform", ["text"], ["bool"],
            RiskLevel.LOW, "free", "realtime", 0.99, [], "Toxicity scorer"),
    ]
    return CapabilityRegistry(entries)
