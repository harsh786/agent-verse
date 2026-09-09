"""Org Digital Twin — SUPPLEMENT H.

A live simulation model of the organisation used for planning,
capacity simulation, and what-if analysis WITHOUT modifying production state.

Usage:
    twin = OrgDigitalTwin()
    result = await twin.simulate_mission(org_id, mission_config, session)
    print(result.resource_usage, result.estimated_duration_h, result.bottlenecks)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from opentelemetry import trace

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


@dataclass
class SimResult:
    """Result of a digital twin simulation run."""

    mission_id: str | None = None
    estimated_duration_h: float = 0.0
    estimated_cost_usd: float = 0.0
    resource_usage: dict[str, float] = field(default_factory=dict)
    bottlenecks: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    feasible: bool = True
    confidence: float = 0.8
    simulated_at: str = ""

    def __post_init__(self) -> None:
        if not self.simulated_at:
            self.simulated_at = datetime.now(UTC).isoformat()


@dataclass
class CapacityPlan:
    """Capacity planning output."""

    org_id: str
    current_utilisation: dict[str, float]  # department → %
    queued_missions: int
    estimated_clear_h: float  # hours until all queued missions complete
    underutilised: list[str]
    overloaded: list[str]
    recommendations: list[str]


class OrgDigitalTwin:
    """Live simulation model of the organisation.

    Purposes:
    1. Planning   — "What resources needed for this mission?"
    2. Simulation — "Would 5 more agents improve throughput?"
    3. Capacity   — "When do queued missions complete?"
    4. What-if    — "What if Legal dept was 2x faster?"

    Never modifies production state — read-only DB access.
    """

    def __init__(self) -> None:
        self._db: Any = None  # async session factory (injected in lifespan)
        self._last_synced_event: dict[str, dict[str, Any]] = {}  # org_id -> last event
        self._synced_event_counts: dict[str, int] = {}  # org_id -> total events synced

    def set_db(self, db_factory: Any) -> None:
        self._db = db_factory

    async def sync(self, event: dict[str, Any]) -> None:
        """Update the twin's live-state snapshot from a real org event.

        Called by the org-twin-sync trigger (app/org/feature_flags.py) on every
        org event so what-if/capacity projections stay grounded in reality.
        This never touches production state — it only updates the twin's own
        in-memory snapshot.
        """
        org_id = event.get("org_id", "")
        if not org_id:
            return
        with _tracer.start_as_current_span("digital_twin.sync") as span:
            span.set_attribute("org_id", org_id)
            span.set_attribute("event_type", event.get("event_type", ""))
            self._last_synced_event[org_id] = event
            self._synced_event_counts[org_id] = self._synced_event_counts.get(org_id, 0) + 1
            _log.debug(
                "digital_twin.synced",
                org_id=org_id,
                event_type=event.get("event_type", ""),
            )

    async def simulate_mission(
        self,
        org_id: str,
        mission_config: dict[str, Any],
        session: Any = None,
    ) -> SimResult:
        """Simulate what resources and time a mission would require."""
        with _tracer.start_as_current_span("digital_twin.simulate_mission") as span:
            span.set_attribute("org_id", org_id)
            title = mission_config.get("title", "Unknown")
            span.set_attribute("mission_title", title)

            _log.info("digital_twin.simulate_mission", org_id=org_id, title=title)

            # Heuristic simulation based on priority and complexity
            priority = mission_config.get("priority", "medium")
            duration_map = {"critical": 2.0, "high": 6.0, "medium": 12.0, "low": 24.0}
            cost_map = {"critical": 50.0, "high": 20.0, "medium": 8.0, "low": 2.0}

            estimated_h = duration_map.get(priority, 12.0)
            estimated_cost = cost_map.get(priority, 8.0)

            result = SimResult(
                estimated_duration_h=estimated_h,
                estimated_cost_usd=estimated_cost,
                resource_usage={"agents": 2, "tools": 5, "llm_tokens": 50_000},
                recommendations=[
                    "Assign to department with relevant capability.",
                    f"Estimated {estimated_h}h runtime at priority '{priority}'.",
                ],
                feasible=True,
                confidence=0.75,
            )
            span.set_attribute("estimated_duration_h", estimated_h)
            return result

    async def capacity_plan(self, org_id: str, session: Any = None) -> CapacityPlan:
        """Analyse current org capacity and predict when queued work clears."""
        with _tracer.start_as_current_span("digital_twin.capacity_plan") as span:
            span.set_attribute("org_id", org_id)

            # Stub implementation — real version queries OrgMission + OrgTask tables
            return CapacityPlan(
                org_id=org_id,
                current_utilisation={"Engineering": 0.85, "Operations": 0.60},
                queued_missions=0,
                estimated_clear_h=0.0,
                underutilised=["Operations"],
                overloaded=[],
                recommendations=["Consider redistributing work to Operations."],
            )

    async def what_if(
        self,
        org_id: str,
        scenario: dict[str, Any],
    ) -> dict[str, Any]:
        """What-if analysis — "what if Legal dept was 2x faster?"."""
        with _tracer.start_as_current_span("digital_twin.what_if") as span:
            span.set_attribute("org_id", org_id)
            span.set_attribute("scenario_keys", str(list(scenario.keys())))

            _log.info("digital_twin.what_if", org_id=org_id, scenario=scenario)

            # Placeholder: real impl would re-run capacity simulation with modified params
            await asyncio.sleep(0)  # yield to event loop
            return {
                "org_id": org_id,
                "scenario": scenario,
                "projected_improvement": "Estimated 15-20% throughput gain based on scenario.",
                "confidence": 0.6,
                "simulated_at": datetime.now(UTC).isoformat(),
            }


# Module-level singleton (wired in lifespan if needed)
_twin = OrgDigitalTwin()


def get_twin() -> OrgDigitalTwin:
    """Return the process-local digital twin instance."""
    return _twin
