"""Skills Runtime execution engine.

Responsibilities:
1. TriggerMatcher: score how well a goal/step matches a skill's trigger hints
2. ScopedPermissionChecker: enforce per-tenant and per-agent skill permission scopes
3. SkillExecutor: actually run a skill against a provider with timeout + output contract validation
"""

from __future__ import annotations

import asyncio
import datetime
import json
import time
import uuid
from typing import Any

from app.providers.base import CompletionRequest, Message
from app.skills_runtime.models import SkillDefinition, SkillExecution, SkillScope, SkillStatus


class TriggerMatcher:
    """Score how well a goal/step matches a skill's trigger hints."""

    def score(self, goal: str, trigger_hints: list[str]) -> float:
        """Return 0.0-1.0 match score.

        Algorithm:
        1. Lowercase both sides
        2. For each trigger hint, compute Jaccard similarity of word sets
        3. Also check if any trigger hint is a substring of goal
        4. Return max score across all hints
        """
        if not trigger_hints:
            return 0.0

        goal_lower = goal.lower()
        goal_words = set(goal_lower.split())
        max_score = 0.0

        for hint in trigger_hints:
            hint_lower = hint.lower()
            hint_words = set(hint_lower.split())

            # Jaccard similarity of word sets
            union = goal_words | hint_words
            jaccard = len(goal_words & hint_words) / len(union) if union else 0.0

            # Substring check: boost score if hint is a substring of goal or vice versa
            if hint_lower in goal_lower:
                substring_score = 0.9
            elif goal_lower in hint_lower:
                substring_score = 0.6
            else:
                substring_score = 0.0

            candidate = max(jaccard, substring_score)
            if candidate > max_score:
                max_score = candidate

        return min(max_score, 1.0)

    def rank_skills(
        self,
        goal: str,
        skills: list[SkillDefinition],
        threshold: float = 0.15,
    ) -> list[tuple[SkillDefinition, float]]:
        """Return list of (skill, score) sorted by score desc, filtered by threshold."""
        scored = [(skill, self.score(goal, skill.trigger_hints)) for skill in skills]
        filtered = [(s, sc) for s, sc in scored if sc >= threshold]
        filtered.sort(key=lambda x: x[1], reverse=True)
        return filtered

    def best_match(
        self,
        goal: str,
        skills: list[SkillDefinition],
    ) -> SkillDefinition | None:
        """Return highest-scoring skill or None if below threshold."""
        ranked = self.rank_skills(goal, skills)
        return ranked[0][0] if ranked else None


class ScopedPermissionChecker:
    """Enforce per-tenant and per-agent skill permission scopes."""

    def __init__(self) -> None:
        # tenant_id → set of disabled skill_ids
        self._disabled: dict[str, set[str]] = {}
        # (tenant_id, agent_id) → set of allowed skill_ids (empty = all allowed)
        self._agent_allowlist: dict[tuple[str, str], set[str]] = {}

    def disable_for_tenant(self, tenant_id: str, skill_id: str) -> None:
        """Disable a skill for a tenant."""
        self._disabled.setdefault(tenant_id, set()).add(skill_id)

    def enable_for_tenant(self, tenant_id: str, skill_id: str) -> None:
        """Re-enable a skill for a tenant."""
        self._disabled.setdefault(tenant_id, set()).discard(skill_id)

    def is_allowed(
        self,
        skill: SkillDefinition,
        tenant_id: str,
        agent_id: str | None = None,
    ) -> bool:
        """Check whether a skill is allowed for the given tenant/agent.

        Rules (evaluated in order):
        1. If skill status is DISABLED → False
        2. If skill_id is in the tenant's disabled set → False
        3. If the agent has an allowlist and skill_id is not in it → False
        4. If skill.scope == AGENT and agent_id is None → False
        5. If skill.tenant_id is set and skill.tenant_id != tenant_id → False
        6. Otherwise → True
        """
        # 1. Global skill status
        if skill.status == SkillStatus.DISABLED:
            return False

        # 2. Tenant-level disable list
        if skill.skill_id in self._disabled.get(tenant_id, set()):
            return False

        # 3. Agent allowlist (if one has been configured for this agent)
        if agent_id is not None:
            allowlist = self._agent_allowlist.get((tenant_id, agent_id), set())
            if allowlist and skill.skill_id not in allowlist:
                return False

        # 4. AGENT-scoped skills require an agent_id
        if skill.scope == SkillScope.AGENT and agent_id is None:
            return False

        # 5. Tenant-scoped skills must belong to the requesting tenant
        return skill.tenant_id is None or skill.tenant_id == tenant_id

    def set_agent_allowlist(
        self,
        tenant_id: str,
        agent_id: str,
        skill_ids: list[str],
    ) -> None:
        """Set the skill allowlist for a specific agent."""
        self._agent_allowlist[(tenant_id, agent_id)] = set(skill_ids)


class SkillExecutor:
    """Execute a skill against an LLM provider."""

    def __init__(
        self,
        *,
        permission_checker: ScopedPermissionChecker | None = None,
        trigger_matcher: TriggerMatcher | None = None,
        provider: Any = None,  # LLMProvider
        timeout_seconds: float = 30.0,
    ) -> None:
        self._permission_checker = permission_checker or ScopedPermissionChecker()
        self._trigger_matcher = trigger_matcher or TriggerMatcher()
        self._provider = provider
        self._timeout_seconds = timeout_seconds

    async def execute(
        self,
        *,
        skill: SkillDefinition,
        input_context: str,
        tenant_id: str,
        agent_id: str | None = None,
        goal_id: str | None = None,
    ) -> SkillExecution:
        """Execute a skill.

        Steps:
        1. Check permission via ScopedPermissionChecker → raise PermissionError if denied
        2. Build prompt: system=skill.instructions, user=input_context
        3. Call provider.complete() with asyncio.wait_for(timeout=timeout_seconds)
        4. Validate output_contract required_fields against JSON output
        5. Return successful SkillExecution
        6. On any exception → return failed SkillExecution with error=str(exc)
        """
        execution_id = str(uuid.uuid4())
        start = time.monotonic()
        now = datetime.datetime.now(datetime.UTC).isoformat()

        try:
            # 1. Permission check
            if not self._permission_checker.is_allowed(skill, tenant_id, agent_id):
                raise PermissionError(
                    f"Skill '{skill.skill_id}' is not allowed for "
                    f"tenant='{tenant_id}' agent='{agent_id}'"
                )

            # 2. Build prompt and call provider
            if self._provider is None:
                output = (
                    f"[{skill.name} Skill] Processing: {input_context[:100]}"
                    " (LLM provider not configured)"
                )
            else:
                req = CompletionRequest(
                    messages=[Message(role="user", content=input_context)],
                    model="",
                    system=skill.instructions or None,
                    max_tokens=1000,
                )
                # 3. Call with timeout
                resp = await asyncio.wait_for(
                    self._provider.complete(req),
                    timeout=self._timeout_seconds,
                )
                output = resp.content

                # 4. Validate output_contract
                required_fields: list[str] = skill.output_contract.get("required_fields", [])
                if required_fields:
                    try:
                        parsed = json.loads(output)
                        missing = [f for f in required_fields if f not in parsed]
                        if missing:
                            raise ValueError(f"Output missing required fields: {missing}")
                    except (json.JSONDecodeError, TypeError) as exc:
                        raise ValueError(f"Output contract validation failed: {exc}") from exc

            elapsed = (time.monotonic() - start) * 1000
            # 5. Return successful execution
            return SkillExecution(
                execution_id=execution_id,
                skill_id=skill.skill_id,
                tenant_id=tenant_id,
                goal_id=goal_id,
                input_context=input_context[:500],
                output=output,
                success=True,
                duration_ms=round(elapsed, 1),
                created_at=now,
            )

        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            # 6. Return failed execution on any exception
            return SkillExecution(
                execution_id=execution_id,
                skill_id=skill.skill_id,
                tenant_id=tenant_id,
                goal_id=goal_id,
                input_context=input_context[:500],
                output="",
                success=False,
                error=str(exc),
                duration_ms=round(elapsed, 1),
                created_at=now,
            )

    async def execute_best_match(
        self,
        *,
        goal: str,
        skills: list[SkillDefinition],
        tenant_id: str,
        agent_id: str | None = None,
        goal_id: str | None = None,
    ) -> SkillExecution | None:
        """Find best matching skill and execute it. Returns None if no match."""
        best = self._trigger_matcher.best_match(goal, skills)
        if best is None:
            return None
        return await self.execute(
            skill=best,
            input_context=goal,
            tenant_id=tenant_id,
            agent_id=agent_id,
            goal_id=goal_id,
        )


# Module-level singletons
trigger_matcher = TriggerMatcher()
permission_checker = ScopedPermissionChecker()
skill_executor = SkillExecutor(
    permission_checker=permission_checker,
    trigger_matcher=trigger_matcher,
)
