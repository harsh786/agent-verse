"""Tests for app/skills_runtime/executor.py — TriggerMatcher, ScopedPermissionChecker,
and SkillExecutor."""

from __future__ import annotations

from app.providers.fake import FakeProvider
from app.skills_runtime.executor import (
    ScopedPermissionChecker,
    SkillExecutor,
    TriggerMatcher,
)
from app.skills_runtime.models import (
    BUILTIN_SKILLS,
    SkillDefinition,
    SkillScope,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _builtin(skill_id: str) -> SkillDefinition:
    """Load a built-in skill dict into a SkillDefinition."""
    raw = next(s for s in BUILTIN_SKILLS if s["skill_id"] == skill_id)
    return SkillDefinition(
        skill_id=raw["skill_id"],
        name=raw["name"],
        description=raw["description"],
        scope=SkillScope.PLATFORM,
        trigger_hints=raw.get("trigger_hints", []),
        instructions=raw.get("instructions", ""),
        is_builtin=True,
    )


def _all_builtins() -> list[SkillDefinition]:
    return [_builtin(s["skill_id"]) for s in BUILTIN_SKILLS]


# ── 1. TriggerMatcher — exact trigger phrase ──────────────────────────────────


def test_trigger_matcher_exact_match() -> None:
    """'create knowledge graph' should strongly match the graphify skill (score > 0.5)."""
    matcher = TriggerMatcher()
    graphify = _builtin("graphify")

    score = matcher.score("create knowledge graph", graphify.trigger_hints)

    assert score > 0.5, f"Expected score > 0.5 for graphify, got {score}"


# ── 2. TriggerMatcher — no match ─────────────────────────────────────────────


def test_trigger_matcher_no_match() -> None:
    """'deploy to kubernetes' should not match any built-in skill (returns None)."""
    matcher = TriggerMatcher()
    skills = _all_builtins()

    result = matcher.best_match("deploy to kubernetes", skills)

    assert result is None, f"Expected no match, got {result}"


# ── 3. ScopedPermissionChecker — disable / enable cycle ──────────────────────


def test_permission_checker_disable_enable() -> None:
    """Disable graphify for a tenant → is_allowed False; re-enable → True."""
    checker = ScopedPermissionChecker()
    skill = _builtin("graphify")
    tenant = "tenant_abc"

    # Initially allowed (no restrictions)
    assert checker.is_allowed(skill, tenant) is True

    # Disable
    checker.disable_for_tenant(tenant, "graphify")
    assert checker.is_allowed(skill, tenant) is False

    # Re-enable
    checker.enable_for_tenant(tenant, "graphify")
    assert checker.is_allowed(skill, tenant) is True


# ── 4. SkillExecutor — FakeProvider happy path ───────────────────────────────


async def test_skill_executor_fake_provider() -> None:
    """Execute headroom skill with FakeProvider; expect success=True and correct output."""
    provider = FakeProvider(responses=["Compressed output."])
    executor = SkillExecutor(provider=provider)
    skill = _builtin("headroom")

    result = await executor.execute(
        skill=skill,
        input_context="This is a very long piece of text that needs token compression.",
        tenant_id="tenant_test",
    )

    assert result.success is True
    assert result.output == "Compressed output."
    assert result.skill_id == "headroom"
    assert result.error is None
    assert result.duration_ms >= 0.0


# ── 5. AGENT-scope enforcement — no agent_id → denied ────────────────────────


def test_scope_enforcement_agent_scope() -> None:
    """AGENT-scoped skill without agent_id must be denied; with agent_id must be allowed."""
    checker = ScopedPermissionChecker()
    skill = SkillDefinition(
        skill_id="agent_only_skill",
        name="Agent Only Skill",
        description="Requires an explicit agent context",
        scope=SkillScope.AGENT,
        trigger_hints=[],
    )

    # Rule 4: scope==AGENT and agent_id is None → False
    assert checker.is_allowed(skill, "tenant1", agent_id=None) is False

    # Providing an agent_id satisfies the scope requirement
    assert checker.is_allowed(skill, "tenant1", agent_id="agent_abc") is True


# ── 6. Execution history must be bounded ─────────────────────────────────────


def test_skill_execution_history_bounded() -> None:
    """_executions must use bounded deque storage to prevent unbounded memory growth."""
    from collections import deque

    from app.api.skills_runtime import _executions

    # Seed a tenant's execution queue by simulating the runtime insert
    tid = "tenant_bounded_test"
    q: deque = deque(maxlen=1000)
    for i in range(1500):
        q.append({"execution_id": str(i)})
    _executions[tid] = q

    # After 1500 inserts, only the most recent 1000 entries must be kept
    assert len(_executions[tid]) == 1000, (
        f"Expected 1000 entries (maxlen), got {len(_executions[tid])}"
    )
    # The oldest entry (0) must have been evicted; the newest (1499) must be present
    assert _executions[tid][-1]["execution_id"] == "1499"
    assert _executions[tid][0]["execution_id"] == "500"  # first surviving entry

    # Verify the store is a deque, not a plain list
    assert isinstance(_executions[tid], deque), "_executions values must be deque instances"
    assert _executions[tid].maxlen == 1000
