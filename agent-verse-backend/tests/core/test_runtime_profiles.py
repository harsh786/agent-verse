"""Tests for runtime_profiles registry."""
from __future__ import annotations
import pytest
from app.core.runtime_profiles import RuntimeProfilesRegistry, get_runtime_profiles_registry


def test_registry_register_and_get():
    registry = RuntimeProfilesRegistry()
    from unittest.mock import MagicMock
    profile = MagicMock()
    profile.tenant_id = "t1"
    profile.goal_id = "g1"
    registry.register(profile)
    assert registry.get("t1", "g1") is profile

def test_registry_list_for_tenant():
    registry = RuntimeProfilesRegistry()
    from unittest.mock import MagicMock
    for i in range(3):
        p = MagicMock(); p.tenant_id = "t1"; p.goal_id = f"g{i}"
        registry.register(p)
    p2 = MagicMock(); p2.tenant_id = "t2"; p2.goal_id = "g0"
    registry.register(p2)
    assert len(registry.list_for_tenant("t1")) == 3
    assert len(registry.list_for_tenant("t2")) == 1

def test_registry_remove():
    registry = RuntimeProfilesRegistry()
    from unittest.mock import MagicMock
    p = MagicMock(); p.tenant_id = "t1"; p.goal_id = "g1"
    registry.register(p)
    registry.remove("t1", "g1")
    assert registry.get("t1", "g1") is None

def test_get_runtime_profiles_registry_singleton():
    r1 = get_runtime_profiles_registry()
    r2 = get_runtime_profiles_registry()
    assert r1 is r2
