"""Real, idempotent default handlers for the ImprovementActionExecutor (D-3).

The ``ImprovementActionExecutor`` dispatches governed self-improvement actions
onto a ``handlers`` map keyed by action type.  Historically it was constructed
with ``handlers={}`` (see ``app/main.py``), so even a dispatched action did
nothing.  This module supplies a *real* default handler set: each handler
applies a concrete, testable, idempotent effect against the relevant store
(prompt variants, model-routing overrides, tool blacklist, reflexion lessons,
regression cases, RAG strategy).

The stores here are simple in-memory, tenant-scoped structures so the loop is
runnable and verifiable in isolation (no DB required).  In production the
factory would be handed the DB/Redis-backed equivalents — see the wiring TODO
at the bottom of this module.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from app.intelligence.prompt_optimizer import PromptOptimizer, PromptVariant

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _deterministic_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Tenant-scoped in-memory effect stores (idempotent writes)
# ---------------------------------------------------------------------------


class ModelRoutingPolicyStore:
    """Per-tenant model-routing overrides keyed by task type. Idempotent set."""

    def __init__(self) -> None:
        self._overrides: dict[tuple[str, str], str] = {}

    def set_override(self, tenant_id: str, task_type: str, model: str) -> str | None:
        """Set the override; returns the previous model (None if unset)."""
        key = (tenant_id, task_type)
        previous = self._overrides.get(key)
        self._overrides[key] = model
        return previous

    def get_override(self, tenant_id: str, task_type: str) -> str | None:
        return self._overrides.get((tenant_id, task_type))


class ToolBlacklistStore:
    """Per-tenant set of blacklisted tool-name patterns. Idempotent add."""

    def __init__(self) -> None:
        self._patterns: dict[str, set[str]] = {}

    def add(self, tenant_id: str, pattern: str) -> bool:
        """Add ``pattern``; returns True if newly added, False if already present."""
        bucket = self._patterns.setdefault(tenant_id, set())
        if pattern in bucket:
            return False
        bucket.add(pattern)
        return True

    def is_blacklisted(self, tenant_id: str, pattern: str) -> bool:
        return pattern in self._patterns.get(tenant_id, set())

    def patterns_for(self, tenant_id: str) -> frozenset[str]:
        return frozenset(self._patterns.get(tenant_id, set()))


class RegressionCaseStore:
    """Per-tenant regression cases keyed by case id. Idempotent add."""

    def __init__(self) -> None:
        self._cases: dict[str, dict[str, dict[str, Any]]] = {}

    def add(self, tenant_id: str, case_id: str, data: dict[str, Any]) -> bool:
        bucket = self._cases.setdefault(tenant_id, {})
        if case_id in bucket:
            return False
        bucket[case_id] = data
        return True

    def cases_for(self, tenant_id: str) -> dict[str, dict[str, Any]]:
        return dict(self._cases.get(tenant_id, {}))


class LessonStore:
    """Per-tenant reflexion lessons keyed by lesson id. Idempotent add.

    A clearly-scoped in-memory stand-in for the DB-backed ``ReflexionService``
    so the handler produces an observable effect without a repository.
    """

    def __init__(self) -> None:
        self._lessons: dict[str, dict[str, dict[str, Any]]] = {}

    def add(self, tenant_id: str, lesson_id: str, data: dict[str, Any]) -> bool:
        bucket = self._lessons.setdefault(tenant_id, {})
        if lesson_id in bucket:
            return False
        bucket[lesson_id] = data
        return True

    def lessons_for(self, tenant_id: str) -> dict[str, dict[str, Any]]:
        return dict(self._lessons.get(tenant_id, {}))


class RagStrategyStore:
    """Per-tenant RAG strategy selection. Idempotent set."""

    def __init__(self) -> None:
        self._strategy: dict[str, str] = {}

    def set_strategy(self, tenant_id: str, strategy: str) -> str | None:
        previous = self._strategy.get(tenant_id)
        self._strategy[tenant_id] = strategy
        return previous

    def get_strategy(self, tenant_id: str) -> str | None:
        return self._strategy.get(tenant_id)


# ---------------------------------------------------------------------------
# Default handler factory
# ---------------------------------------------------------------------------


def build_default_improvement_handlers(
    *,
    prompt_optimizer: PromptOptimizer | None = None,
    model_routing_store: ModelRoutingPolicyStore | None = None,
    tool_blacklist_store: ToolBlacklistStore | None = None,
    regression_store: RegressionCaseStore | None = None,
    lesson_store: LessonStore | None = None,
    rag_strategy_store: RagStrategyStore | None = None,
) -> dict[str, Handler]:
    """Build the real default handler map for ``ImprovementActionExecutor``.

    Each handler is idempotent and returns a non-empty durable result dict
    (carrying ``evidence_ref`` / ``rollback_ref`` so the executor records a
    terminal, auditable outcome).  Any store not supplied is created fresh, so
    the returned handlers are self-contained and testable in isolation.

    Covered action types (those the ``SelfImprovementEngine`` actually
    dispatches), plus a few aliases from the contract literal:

      * ``update_prompt_variant`` / ``update_prompt`` → register a prompt variant
      * ``update_model_routing`` / ``change_model``   → set a model-routing override
      * ``blacklist_tool_pattern``                    → add a tool-name pattern
      * ``store_reflexion_lesson``                    → record a reflexion lesson
      * ``create_regression_case``                    → record a regression case
      * ``update_rag_strategy`` / ``change_rag``      → set a RAG strategy

    Action types outside this set (``adjust_limit``, ``publish_skill``,
    ``rollback``) are intentionally absent — the executor fails loudly on a
    missing handler, which is the desired fail-safe until they are implemented.
    """
    optimizer = prompt_optimizer or PromptOptimizer()
    routing = model_routing_store or ModelRoutingPolicyStore()
    blacklist = tool_blacklist_store or ToolBlacklistStore()
    regressions = regression_store or RegressionCaseStore()
    lessons = lesson_store or LessonStore()
    rag = rag_strategy_store or RagStrategyStore()

    async def update_prompt_variant(payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(payload["tenant_id"])
        prompt_key = str(payload.get("prompt_key") or "system_prompt")
        name = str(payload.get("variant_name") or payload.get("name") or "auto-candidate")
        text = str(payload.get("prompt_text") or payload.get("new_value") or "")
        if not text:
            raise ValueError("update_prompt_variant requires non-empty prompt_text")
        variant_id = _deterministic_id("prompt", tenant_id, prompt_key, name)
        newly_created = optimizer.get_variant(variant_id, tenant_id=tenant_id) is None
        if newly_created:
            optimizer.add_variant(
                PromptVariant(
                    variant_id=variant_id,
                    name=name,
                    prompt_text=text,
                    prompt_key=prompt_key,
                ),
                tenant_id=tenant_id,
            )
        return {
            "effect": "prompt_variant_registered",
            "variant_id": variant_id,
            "prompt_key": prompt_key,
            "newly_created": newly_created,
            "evidence_ref": f"prompt_variant://{tenant_id}/{variant_id}",
            "rollback_ref": f"prompt_variant://{tenant_id}/{variant_id}",
        }

    async def update_model_routing(payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(payload["tenant_id"])
        task_type = str(payload.get("task_type") or "execution")
        model = str(payload["model"])
        previous = routing.set_override(tenant_id, task_type, model)
        return {
            "effect": "model_routing_updated",
            "task_type": task_type,
            "model": model,
            "previous_model": previous,
            "evidence_ref": f"model_routing://{tenant_id}/{task_type}",
            "rollback_ref": f"model_routing://{tenant_id}/{task_type}",
        }

    async def blacklist_tool_pattern(payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(payload["tenant_id"])
        pattern = str(payload["pattern"])
        newly_added = blacklist.add(tenant_id, pattern)
        return {
            "effect": "tool_pattern_blacklisted",
            "pattern": pattern,
            "newly_added": newly_added,
            "evidence_ref": f"tool_blacklist://{tenant_id}/{pattern}",
            "rollback_ref": f"tool_blacklist://{tenant_id}/{pattern}",
        }

    async def store_reflexion_lesson(payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(payload["tenant_id"])
        lesson = str(payload["lesson"])
        goal_id = str(payload.get("goal_id") or "")
        lesson_id = _deterministic_id("lesson", tenant_id, goal_id, lesson)
        newly_stored = lessons.add(
            tenant_id, lesson_id, {"lesson": lesson, "goal_id": goal_id}
        )
        return {
            "effect": "reflexion_lesson_stored",
            "lesson_id": lesson_id,
            "newly_stored": newly_stored,
            "evidence_ref": f"lesson://{tenant_id}/{lesson_id}",
            "rollback_ref": f"lesson://{tenant_id}/{lesson_id}",
        }

    async def create_regression_case(payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(payload["tenant_id"])
        goal_id = str(payload.get("goal_id") or "")
        case_id = str(payload.get("case_id") or _deterministic_id("regression", tenant_id, goal_id))
        newly_created = regressions.add(
            tenant_id,
            case_id,
            {"goal_id": goal_id, "reason": payload.get("reason", "")},
        )
        return {
            "effect": "regression_case_created",
            "case_id": case_id,
            "newly_created": newly_created,
            "evidence_ref": f"regression://{tenant_id}/{case_id}",
            "rollback_ref": f"regression://{tenant_id}/{case_id}",
        }

    async def update_rag_strategy(payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(payload["tenant_id"])
        strategy = str(payload["strategy"])
        previous = rag.set_strategy(tenant_id, strategy)
        return {
            "effect": "rag_strategy_updated",
            "strategy": strategy,
            "previous_strategy": previous,
            "evidence_ref": f"rag_strategy://{tenant_id}",
            "rollback_ref": f"rag_strategy://{tenant_id}",
        }

    return {
        "update_prompt_variant": update_prompt_variant,
        "update_prompt": update_prompt_variant,
        "update_model_routing": update_model_routing,
        "change_model": update_model_routing,
        "blacklist_tool_pattern": blacklist_tool_pattern,
        "store_reflexion_lesson": store_reflexion_lesson,
        "create_regression_case": create_regression_case,
        "update_rag_strategy": update_rag_strategy,
        "change_rag": update_rag_strategy,
    }


__all__ = [
    "Handler",
    "LessonStore",
    "ModelRoutingPolicyStore",
    "RagStrategyStore",
    "RegressionCaseStore",
    "ToolBlacklistStore",
    "build_default_improvement_handlers",
]

# ── TODO (follow-up wiring; intentionally NOT done here — out of scope for D-3) ─
# The runtime is still suggest-and-log + gated off.  To un-gate the live loop:
#
# 1. app/main.py  (~line 959 and ~line 1723): replace the two
#    ``ImprovementActionExecutor(handlers={})`` constructions with
#    ``ImprovementActionExecutor(handlers=build_default_improvement_handlers(
#        prompt_optimizer=app.state.prompt_optimizer, ...))`` — passing the
#    DB/Redis-backed stores instead of the in-memory defaults built here.
# 2. app/agent/graph.py ``_trigger_self_optimization`` (~line 753-766): today it
#    only LOGS suggestions.  Wire it to call
#    ``SelfOptimizerV2.plan_improvement_actions(...)`` (from eval scorecard
#    signals) and dispatch each returned record through
#    ``app.state.improvement_action_executor.execute(record, policy_allowed=...)``.
# 3. app/core/runtime_flags.py: gate step 2 behind the existing
#    ``enable_self_improvement`` (and/or ``dynamic_orchestration``) flag, which
#    currently defaults to False.
