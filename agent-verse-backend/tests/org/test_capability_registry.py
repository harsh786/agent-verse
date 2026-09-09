"""Tests for CapabilityRegistry — app/org/capability_registry.py"""
from __future__ import annotations

import pytest

from app.org.capability_registry import CapabilityRegistry, CapabilitySpec


def test_registry_contains_web_search():
    reg = CapabilityRegistry()
    spec = reg.get("web_search")
    assert spec is not None
    assert isinstance(spec, CapabilitySpec)


def test_registry_contains_code_generation():
    reg = CapabilityRegistry()
    spec = reg.get("code_generation")
    assert spec is not None
    assert len(spec.tools) > 0


def test_registry_get_unknown_returns_none():
    reg = CapabilityRegistry()
    assert reg.get("nonexistent_capability_xyz") is None


def test_registry_list_returns_multiple():
    reg = CapabilityRegistry()
    all_caps = reg.list_all()
    assert len(all_caps) >= 10


def test_registry_legal_requires_min_quality():
    reg = CapabilityRegistry()
    spec = reg.get("legal_review")
    assert spec is not None
    assert spec.min_quality >= 0.90


def test_registry_register_custom_capability():
    reg = CapabilityRegistry()
    reg.register("custom_cap", CapabilitySpec(tools=["custom_tool"], models=["gpt-4o"]))
    assert reg.get("custom_cap") is not None


def test_registry_gap_detection():
    reg = CapabilityRegistry()
    report = reg.detect_gaps(["web_search", "nonexistent_cap_xyz"])
    assert "nonexistent_cap_xyz" in report.missing_capabilities
    assert report.coverage_pct < 1.0


def test_registry_get_capabilities_for_role():
    reg = CapabilityRegistry()
    caps = reg.get_for_role("market_analyst")
    assert len(caps) >= 1
    assert any(c in caps for c in ["web_search", "data_analysis"])
