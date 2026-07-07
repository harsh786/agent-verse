"""Tests for runtime flags and GoalRuntimeProfile contracts."""
from __future__ import annotations

import os
import pytest
from app.core.runtime_flags import RuntimeFlags, get_runtime_flags


def test_flags_default_values():
    flags = RuntimeFlags()
    assert flags.dynamic_orchestration is False
    assert flags.agentic_rag is False
    assert flags.plan_verification is False
    assert flags.data_classification is False
    assert flags.capability_registry is False
    assert flags.policy_compiler is False


def test_flags_from_env(monkeypatch):
    monkeypatch.setenv("DYNAMIC_ORCHESTRATION", "true")
    monkeypatch.setenv("AGENTIC_RAG", "true")
    flags = RuntimeFlags.from_env()
    assert flags.dynamic_orchestration is True
    assert flags.agentic_rag is True


def test_get_runtime_flags_singleton():
    get_runtime_flags.cache_clear()
    f1 = get_runtime_flags()
    f2 = get_runtime_flags()
    assert f1 is f2
