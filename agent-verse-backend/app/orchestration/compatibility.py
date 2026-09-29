"""Deterministic composition of one primary strategy and auxiliary capabilities."""

from __future__ import annotations

from dataclasses import dataclass

from app.orchestration.runtime_profile import StrategyRejection, StrategySelection
from app.orchestration.strategy_adapters import ExecutionTier
from app.orchestration.strategy_registry import StrategyRegistry, StrategyState

# Registry ids (see strategy_registry.build_default_registry). The admission gates in
# ``compose`` key off the capability's execution tier, so a newly registered sandbox /
# distributed strategy is gated automatically; these tables document the current set and a
# test pins them to the registry so an id can never drift (``program_of_thoughts`` /
# ``magentic_one`` / ``swarm`` once did, silently skipping their gates).
GENERATED_CODE_STRATEGIES = frozenset({"codeact", "program_of_thought"})
DISTRIBUTED_STRATEGIES = frozenset(
    {
        "autogpt",
        "babyagi",
        "camel",
        "consensus",
        "consensus_verification",
        "debate",
        "decentralized_swarm",
        "generative_agents",
        "goal_tree",
        "group_chat",
        "magentic",
        "market_auction",
        "mixture_of_agents",
        "supervisor",
        "voyager",
    }
)


@dataclass(frozen=True, slots=True)
class CompatibilityDecision:
    primary: StrategySelection
    execution_tier: ExecutionTier
    accepted: tuple[StrategySelection, ...]
    rejected: tuple[StrategyRejection, ...]
    primary_rejection: str | None = None


class CompatibilityEvaluator:
    def __init__(self, registry: StrategyRegistry) -> None:
        self._registry = registry

    def compose(
        self,
        *,
        primary_id: str,
        candidate_auxiliary_ids: tuple[str, ...],
        ready_ids: frozenset[str],
        sandbox_ready: bool = False,
        coordination_ready: bool = False,
    ) -> CompatibilityDecision:
        primary_resolution = self._registry.resolve(primary_id)
        primary_capability = primary_resolution.capability
        primary = StrategySelection(
            primary_resolution.canonical_id,
            primary_capability.adapter_version,
        )
        primary_rejection: str | None = None
        if primary.strategy_id not in ready_ids:
            primary_rejection = "not_ready"
        tier = primary_capability.execution_tier
        if tier is ExecutionTier.SANDBOX and not sandbox_ready:
            primary_rejection = "sandbox_not_ready"
        if tier is ExecutionTier.DISTRIBUTED and not coordination_ready:
            primary_rejection = "coordination_not_ready"

        accepted: list[StrategySelection] = []
        rejected: list[StrategyRejection] = []
        seen: set[str] = set()
        for candidate_id in candidate_auxiliary_ids:
            try:
                resolution = self._registry.resolve(candidate_id)
            except LookupError:
                rejected.append(StrategyRejection(candidate_id, "unknown_strategy"))
                continue
            canonical_id = resolution.canonical_id
            capability = resolution.capability
            if canonical_id in seen or canonical_id == primary.strategy_id:
                rejected.append(StrategyRejection(candidate_id, "duplicate_strategy"))
            elif capability.state is StrategyState.DISABLED:
                rejected.append(StrategyRejection(candidate_id, "disabled"))
            elif canonical_id not in ready_ids:
                rejected.append(StrategyRejection(candidate_id, "not_ready"))
            elif capability.execution_tier not in {
                ExecutionTier.CROSS_CUTTING,
                primary_capability.execution_tier,
            }:
                rejected.append(StrategyRejection(candidate_id, "incompatible_primary_tier"))
            elif canonical_id in primary_capability.excluded_strategies:
                rejected.append(StrategyRejection(candidate_id, "explicitly_excluded"))
            else:
                accepted.append(StrategySelection(canonical_id, capability.adapter_version))
                seen.add(canonical_id)

        return CompatibilityDecision(
            primary=primary,
            execution_tier=primary_capability.execution_tier,
            accepted=tuple(accepted),
            rejected=tuple(rejected),
            primary_rejection=primary_rejection,
        )
