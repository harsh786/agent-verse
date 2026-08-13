"""Worst-case cost and deadline admission for immutable MoA layer plans."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.coordination.moa.models import MoALayer, MoAPlan, ModelCandidate


class LayerPlanner:
    def plan(
        self,
        *,
        candidates: tuple[ModelCandidate, ...],
        layers: int,
        fan_out: int,
        quorum: int,
        aggregator_id: str,
        maximum_cost_usd: float,
        deadline: datetime,
        replacement_waves: int,
    ) -> MoAPlan:
        if layers <= 0 or fan_out <= 0 or not 1 <= quorum <= fan_out:
            raise ValueError("invalid layer, fan-out, or quorum")
        if replacement_waves not in {0, 1}:
            raise ValueError("only one replacement wave is supported")
        by_id = {item.deployment_id: item for item in candidates if item.healthy}
        aggregator = by_id.get(aggregator_id)
        if aggregator is None:
            raise ValueError("aggregator unavailable")
        proposers = tuple(
            sorted(
                (item for item in by_id.values() if item.deployment_id != aggregator_id),
                key=lambda item: item.deployment_id,
            )
        )[:fan_out]
        if len(proposers) < fan_out:
            raise ValueError("aggregator must be separate from all proposers")
        proposal_cost = sum(item.estimated_cost_usd for item in proposers)
        layer_cost = proposal_cost * (1 + replacement_waves) + aggregator.estimated_cost_usd
        worst_cost = layer_cost * layers
        proposal_latency = max(item.estimated_latency_ms for item in proposers)
        layer_latency = proposal_latency * (1 + replacement_waves) + aggregator.estimated_latency_ms
        worst_latency = layer_latency * layers
        remaining_ms = (deadline - datetime.now(UTC)).total_seconds() * 1_000
        if worst_cost > maximum_cost_usd:
            raise ValueError("MoA worst-case cost is not admitted")
        if remaining_ms < worst_latency:
            raise ValueError("MoA worst-case latency exceeds deadline")
        planned = tuple(
            MoALayer(
                layer_id=uuid.uuid5(
                    uuid.NAMESPACE_URL, f"moa-layer:{index}:{aggregator_id}"
                ).hex,
                tenant_id="pending",
                session_id="pending",
                strategy_execution_id="pending",
                layer_index=index,
                aggregator_deployment_id=aggregator_id,
                quorum=quorum,
                deployment_ids=tuple(item.deployment_id for item in proposers),
                idempotency_key=f"layer-{index}",
            )
            for index in range(layers)
        )
        return MoAPlan(
            layers=planned,
            worst_case_cost_usd=worst_cost,
            worst_case_latency_ms=worst_latency,
            replacement_waves=replacement_waves,
        )


__all__ = ["LayerPlanner"]
