from __future__ import annotations

import json

import pytest

from app.orchestration.runtime_profile import (
    AgentPatternConfig,
    EvalConfig,
    GoalProperties,
    GoalRuntimeProfile,
    MemoryCacheConfig,
    ModelPlanConfig,
    RAGStrategyConfig,
    RiskLevel,
    SecurityConfig,
)
from app.sandbox_runtime.executor import SandboxExecutor
from app.sandbox_runtime.filesystem_policy import FilesystemMode, FilesystemPolicy
from app.sandbox_runtime.network_policy import NetworkMode, NetworkPolicy
from app.sandbox_runtime.profile import SandboxRuntimeProfile, SandboxType
from app.sandbox_runtime.sandbox_trace import SandboxTrace
from app.sandbox_runtime.simulation_runner import SimulationRunner


def _make_profile(risk=RiskLevel.LOW, sandbox=False):
    return GoalRuntimeProfile(
        goal_id="g1",
        tenant_id="t1",
        properties=GoalProperties(
            raw_goal="test",
            risk=risk,
            requires_code=(risk != RiskLevel.LOW),
        ),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(sandbox_required=sandbox),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )


def test_sandbox_profile_defaults():
    p = SandboxRuntimeProfile()
    assert p.sandbox_type == SandboxType.NONE
    assert p.requires_dry_run is False
    assert p.rollback_required is False


def test_sandbox_profile_for_code():
    p = SandboxRuntimeProfile(
        sandbox_type=SandboxType.PYTHON,
        network_policy="none",
        filesystem_policy="ephemeral",
        timeout_seconds=30,
        requires_dry_run=True,
        rollback_required=True,
    )
    assert p.sandbox_type == SandboxType.PYTHON
    assert p.requires_dry_run is True


def test_sandbox_profile_serializable():
    p = SandboxRuntimeProfile(sandbox_type=SandboxType.BROWSER, requires_dry_run=True)
    json.dumps(p.to_dict())


def test_executor_selects_sandbox_for_code():
    executor = SandboxExecutor()
    profile = _make_profile(risk=RiskLevel.MEDIUM, sandbox=True)
    sp = executor.select_sandbox(profile)
    assert sp.sandbox_type != SandboxType.NONE


def test_executor_no_sandbox_for_safe_read():
    executor = SandboxExecutor()
    profile = _make_profile(risk=RiskLevel.LOW, sandbox=False)
    sp = executor.select_sandbox(profile)
    assert sp.sandbox_type == SandboxType.NONE


def test_network_policy_none_blocks_all():
    policy = NetworkPolicy(mode=NetworkMode.NONE)
    assert policy.is_allowed("https://api.example.com") is False


def test_network_policy_allowlist():
    policy = NetworkPolicy(mode=NetworkMode.ALLOWLIST, allowed_hosts=["api.github.com"])
    assert policy.is_allowed("https://api.github.com") is True
    assert policy.is_allowed("https://evil.com") is False


def test_filesystem_policy_read_only():
    policy = FilesystemPolicy(mode=FilesystemMode.READ_ONLY)
    assert policy.can_write("/tmp/file.txt") is False
    assert policy.can_read("/tmp/file.txt") is True


def test_filesystem_policy_ephemeral():
    policy = FilesystemPolicy(mode=FilesystemMode.EPHEMERAL, workspace="/tmp/sandbox")
    assert policy.can_write("/tmp/sandbox/output.txt") is True
    assert policy.can_write("/etc/passwd") is False


def test_simulation_runner_dry_run():
    runner = SimulationRunner()
    result = runner.dry_run("delete_user", {"user_id": "u123"})
    assert result.simulated is True
    assert result.would_affect is not None


def test_sandbox_trace_records():
    trace = SandboxTrace(goal_id="g1")
    trace.record("python", "calc.py", success=True, latency_ms=450.0)
    assert len(trace.executions) == 1
    assert trace.executions[0]["sandbox_type"] == "python"
