"""CapabilityRegistry — orchestration selects by declared capability."""
from __future__ import annotations
import pytest
from app.capabilities.registry import CapabilityRegistry, build_default_capability_registry
from app.capabilities.resolver import CapabilityResolver
from app.capabilities.schema import CapabilityKind, RiskLevel


@pytest.fixture
def registry() -> CapabilityRegistry:
    return build_default_capability_registry()


@pytest.fixture
def resolver(registry: CapabilityRegistry) -> CapabilityResolver:
    return CapabilityResolver(registry)


def test_registry_has_expected_tools(registry: CapabilityRegistry) -> None:
    tools = registry.list_by_kind(CapabilityKind.TOOL)
    assert len(tools) >= 5
    ids = {t.capability_id for t in tools}
    assert "tool:web_search" in ids
    assert "tool:shell" in ids


def test_get_by_id(registry: CapabilityRegistry) -> None:
    cap = registry.get("tool:web_search")
    assert cap is not None
    assert cap.kind == CapabilityKind.TOOL


def test_get_missing_returns_none(registry: CapabilityRegistry) -> None:
    assert registry.get("nonexistent:xyz") is None


def test_filter_by_kind(registry: CapabilityRegistry) -> None:
    models = registry.filter(kind=CapabilityKind.MODEL)
    assert all(m.kind == CapabilityKind.MODEL for m in models)
    assert len(models) >= 3


def test_filter_max_risk_excludes_high(registry: CapabilityRegistry) -> None:
    results = registry.filter(max_risk=RiskLevel.MEDIUM)
    for cap in results:
        assert cap.risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM)
    # shell is HIGH — must be excluded
    ids = {c.capability_id for c in results}
    assert "tool:shell" not in ids


def test_filter_by_modality(registry: CapabilityRegistry) -> None:
    results = registry.filter(required_modality="image")
    assert all("image" in c.input_modalities for c in results)
    ids = {c.capability_id for c in results}
    assert "model:vision" in ids


def test_resolver_finds_text_tools(resolver: CapabilityResolver) -> None:
    tools = resolver.find_tools_for_modalities(["text"])
    assert len(tools) >= 3


def test_resolver_max_risk_low_excludes_shell(resolver: CapabilityResolver) -> None:
    tools = resolver.find_tools_for_modalities(["text"], max_risk=RiskLevel.LOW)
    ids = {t.capability_id for t in tools}
    assert "tool:shell" not in ids
    # code_interpreter is MEDIUM — also excluded
    assert "tool:code_interpreter" not in ids
