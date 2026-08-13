"""Bounded Constitutional AI critique/revision under deterministic policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.coordination.patterns.common import invoke
from app.orchestration.strategy_adapters import ExecutionTier
from app.policy_runtime.constraint_model import RuntimeConstraints
from app.policy_runtime.runtime_enforcer import RuntimeEnforcer


class ConstitutionalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    original_safe_summary: str = Field(max_length=8_000)
    critique_safe_summary: str = Field(max_length=4_000)
    revised_safe_summary: str = Field(max_length=8_000)
    principle_ids: tuple[str, ...]
    model_calls: int = Field(ge=0, le=2)


@dataclass(frozen=True, slots=True)
class ConstitutionalAIAdapter:
    strategy_id: str = "constitutional_ai"
    execution_tier: ExecutionTier = ExecutionTier.LOCAL

    def create_runtime(self, **kwargs: Any) -> ConstitutionalAIRuntime:
        return ConstitutionalAIRuntime(**kwargs)


class ConstitutionalAIRuntime:
    def __init__(self, *, enforcer: RuntimeEnforcer | None = None) -> None:
        self._enforcer = enforcer or RuntimeEnforcer()

    async def revise(
        self,
        *,
        original_safe_summary: str,
        requested_capability: str,
        constraints: RuntimeConstraints,
        principle_ids: tuple[str, ...],
        critique: Any,
        revision: Any,
    ) -> ConstitutionalResult:
        if not self._enforcer.is_capability_allowed(requested_capability, constraints):
            raise PermissionError("deterministic policy denied requested capability")
        if not principle_ids:
            raise ValueError("at least one constitutional principle is required")
        critique_summary = str(await invoke(critique, original_safe_summary, principle_ids))[:4_000]
        revised = str(
            await invoke(revision, original_safe_summary, critique_summary, principle_ids)
        )[:8_000]
        return ConstitutionalResult(
            original_safe_summary=original_safe_summary[:8_000],
            critique_safe_summary=critique_summary,
            revised_safe_summary=revised,
            principle_ids=principle_ids,
            model_calls=2,
        )


__all__ = [
    "ConstitutionalAIAdapter",
    "ConstitutionalAIRuntime",
    "ConstitutionalResult",
]
