"""N6 + N9 — Work Value Engine + Work Discovery Pipeline.

Work Value Engine (N6):
    Scores discovered work by ROI, urgency, strategic alignment.
    Used by N8 Autonomous Loop to PRIORITIZE discovered work.

Work Discovery Pipeline (N9):
    Detects work that SHOULD exist but doesn't:
    - KPI deviations above threshold
    - Overdue obligations
    - Competitor signals
    - Unresolved blockers older than SLA
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

# ── Work Value Engine (N6) ────────────────────────────────────────────────────


@dataclass
class WorkItem:
    """A discovered piece of work with scoring metadata."""

    id: str
    title: str
    org_id: str
    source: str  # 'kpi_deviation' | 'overdue' | 'blocker' | 'external'
    urgency: float = 0.5  # 0-1
    strategic_fit: float = 0.5  # 0-1
    estimated_cost: float = 0.0  # USD
    estimated_roi: float = 0.0  # USD expected return
    risk_level: str = "low"  # low | medium | high
    discovered_at: str = ""
    value_score: float = 0.0  # computed by WorkValueEngine

    def __post_init__(self) -> None:
        if not self.discovered_at:
            self.discovered_at = datetime.now(UTC).isoformat()


class WorkValueEngine:
    """N6 — Score discovered work items by business value.

    Scoring formula (configurable weights):
        value = (urgency * 0.4) + (strategic_fit * 0.35) + (roi_factor * 0.25)
        where roi_factor = min(1.0, estimated_roi / max(1.0, estimated_cost * 5))
    """

    from typing import ClassVar

    DEFAULT_WEIGHTS: ClassVar[dict[str, float]] = {
        "urgency": 0.4,
        "strategic_fit": 0.35,
        "roi": 0.25,
    }

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self._weights = weights or dict(self.DEFAULT_WEIGHTS)

    def score(self, item: WorkItem) -> float:
        """Return a 0-1 value score for a work item."""
        with _tracer.start_as_current_span("work_value_engine.score") as span:
            span.set_attribute("source", item.source)
            span.set_attribute("urgency", item.urgency)

            roi_factor = 0.5
            if item.estimated_cost > 0 and item.estimated_roi > 0:
                roi_factor = min(1.0, item.estimated_roi / max(1.0, item.estimated_cost * 5))

            score = (
                item.urgency * self._weights["urgency"]
                + item.strategic_fit * self._weights["strategic_fit"]
                + roi_factor * self._weights["roi"]
            )
            item.value_score = round(score, 4)
            span.set_attribute("value_score", item.value_score)
            return item.value_score

    def rank(self, items: list[WorkItem]) -> list[WorkItem]:
        """Score and rank items descending by value score."""
        for item in items:
            self.score(item)
        return sorted(items, key=lambda x: x.value_score, reverse=True)


# ── Work Discovery Pipeline (N9) ─────────────────────────────────────────────


class WorkDiscoveryPipeline:
    """N9 — Detect work that should exist but doesn't.

    Discovery sources (pluggable):
      1. KPI Deviation Monitor  — MRR below target, conversion drop > 10%
      2. Overdue Obligation Scan — tasks past deadline
      3. Blocker Resolution Check — tasks blocked > SLA
      4. External Signal Monitor — competitors, regulatory (placeholder)
    """

    def __init__(self, value_engine: WorkValueEngine | None = None) -> None:
        self._engine = value_engine or WorkValueEngine()

    async def discover(self, org_id: str, health: dict[str, Any]) -> list[WorkItem]:
        """Run all discovery sources and return ranked work items."""
        with _tracer.start_as_current_span("work_discovery.discover") as span:
            span.set_attribute("org_id", org_id)

            discovered: list[WorkItem] = []
            results = await asyncio.gather(
                self._kpi_deviation(org_id, health),
                self._overdue_obligations(org_id, health),
                self._blocker_resolution(org_id, health),
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, list):
                    discovered.extend(r)

            ranked = self._engine.rank(discovered)
            span.set_attribute("items_discovered", len(ranked))
            _log.info("work_discovery.complete", org_id=org_id, count=len(ranked))
            return ranked

    async def _kpi_deviation(self, org_id: str, health: dict[str, Any]) -> list[WorkItem]:
        """Detect KPI deviations that require investigation."""
        items: list[WorkItem] = []
        failed = health.get("task_counts", {}).get("failed", 0)
        blocked = health.get("task_counts", {}).get("blocked", 0)

        if failed > 0:
            items.append(
                WorkItem(
                    id=f"kpi-failed-{org_id}",
                    title=f"Investigate {failed} failed tasks",
                    org_id=org_id,
                    source="kpi_deviation",
                    urgency=min(1.0, 0.5 + (failed / 20)),
                    strategic_fit=0.7,
                    estimated_cost=50.0,
                    estimated_roi=500.0,
                    risk_level="high" if failed > 5 else "medium",
                )
            )

        if blocked > 3:
            items.append(
                WorkItem(
                    id=f"kpi-blocked-{org_id}",
                    title=f"Unblock {blocked} stalled tasks",
                    org_id=org_id,
                    source="kpi_deviation",
                    urgency=min(0.9, 0.4 + (blocked / 30)),
                    strategic_fit=0.6,
                    estimated_cost=20.0,
                    estimated_roi=200.0,
                    risk_level="medium",
                )
            )
        return items

    async def _overdue_obligations(self, org_id: str, health: dict[str, Any]) -> list[WorkItem]:
        """Detect overdue tasks and missions."""
        pending = health.get("pending_approvals", 0)
        items: list[WorkItem] = []
        if pending > 2:
            items.append(
                WorkItem(
                    id=f"overdue-approvals-{org_id}",
                    title=f"Process {pending} pending approvals before SLA",
                    org_id=org_id,
                    source="overdue",
                    urgency=min(1.0, 0.6 + (pending / 10)),
                    strategic_fit=0.8,
                    estimated_cost=10.0,
                    estimated_roi=300.0,
                    risk_level="medium",
                )
            )
        return items

    async def _blocker_resolution(self, org_id: str, health: dict[str, Any]) -> list[WorkItem]:
        """Detect items needing escalation."""
        attention = health.get("items_needing_attention", 0)
        if attention <= 0:
            return []
        return [
            WorkItem(
                id=f"blocker-esc-{org_id}",
                title=f"{attention} items need escalation or attention",
                org_id=org_id,
                source="blocker",
                urgency=0.7,
                strategic_fit=0.5,
                estimated_cost=30.0,
                estimated_roi=150.0,
                risk_level="medium",
            )
        ]


# ── N3: Domain Discovery ──────────────────────────────────────────────────────


class DomainDiscovery:
    """N3 — Detect unknown capability domains from mission analysis.

    When an org tries to execute a mission that requires capabilities
    not in its current capability registry, this engine surfaces the
    gap and suggests domain expansions or new department creation.
    """

    def __init__(self) -> None:
        self._known_domains: set[str] = set()

    def register_domains(self, domains: list[str]) -> None:
        self._known_domains.update(d.lower().strip() for d in domains)

    def discover_unknown_domains(self, required_capabilities: list[str]) -> list[dict[str, str]]:
        """Return capabilities that match no known domain."""
        with _tracer.start_as_current_span("domain_discovery.scan") as span:
            span.set_attribute("required_count", len(required_capabilities))

            unknown = []
            for cap in required_capabilities:
                cap_lower = cap.lower().strip()
                if not any(cap_lower in d or d in cap_lower for d in self._known_domains):
                    unknown.append(
                        {
                            "capability": cap,
                            "suggestion": f"Create new department or expand capabilities for '{cap}'",
                            "severity": "gap",
                        }
                    )

            span.set_attribute("unknown_count", len(unknown))
            if unknown:
                _log.warning(
                    "domain_discovery.gaps_found",
                    count=len(unknown),
                    gaps=[u["capability"] for u in unknown[:5]],
                )
            return unknown


# ── N5: Capability Graph ──────────────────────────────────────────────────────


@dataclass
class CapabilityNode:
    capability_id: str
    name: str
    domain: str
    proficiency: float = 0.5  # 0-1: how well the org can do this
    agent_count: int = 0  # agents that have this capability
    dependencies: list[str] = field(default_factory=list)


class CapabilityGraph:
    """N5 — Graph of organisational capabilities and their relationships.

    Answers:
    - What capabilities does this org have?
    - What gaps exist for a given mission?
    - Which capabilities are interdependent?
    """

    def __init__(self) -> None:
        self._nodes: dict[str, CapabilityNode] = {}
        self._edges: dict[str, list[str]] = {}  # capability_id → [depends_on]

    def add_capability(self, node: CapabilityNode) -> None:
        self._nodes[node.capability_id] = node
        if node.dependencies:
            self._edges[node.capability_id] = node.dependencies

    def gap_analysis(self, required: list[str]) -> dict[str, Any]:
        """Return which required capabilities are missing or under-proficient."""
        with _tracer.start_as_current_span("capability_graph.gap_analysis") as span:
            span.set_attribute("required_count", len(required))

            gaps: list[dict[str, Any]] = []
            present: list[dict[str, Any]] = []

            for cap_name in required:
                # Find by name (case-insensitive)
                found = next(
                    (n for n in self._nodes.values() if cap_name.lower() in n.name.lower()),
                    None,
                )
                if found is None:
                    gaps.append({"capability": cap_name, "status": "missing"})
                elif found.proficiency < 0.4:
                    gaps.append(
                        {
                            "capability": cap_name,
                            "status": "under_proficient",
                            "proficiency": found.proficiency,
                        }
                    )
                else:
                    present.append({"capability": cap_name, "proficiency": found.proficiency})

            span.set_attribute("gap_count", len(gaps))
            return {
                "total_required": len(required),
                "gaps": gaps,
                "present": present,
                "coverage_pct": round(len(present) / max(1, len(required)) * 100, 1),
            }

    def all_capabilities(self) -> list[dict[str, Any]]:
        return [
            {
                "id": n.capability_id,
                "name": n.name,
                "domain": n.domain,
                "proficiency": n.proficiency,
                "agent_count": n.agent_count,
            }
            for n in self._nodes.values()
        ]


# ── Module-level singletons ───────────────────────────────────────────────────

_work_value_engine = WorkValueEngine()
_work_discovery = WorkDiscoveryPipeline(_work_value_engine)
_domain_discovery = DomainDiscovery()
_capability_graph = CapabilityGraph()


def get_work_value_engine() -> WorkValueEngine:
    return _work_value_engine


def get_work_discovery() -> WorkDiscoveryPipeline:
    return _work_discovery


def get_domain_discovery() -> DomainDiscovery:
    return _domain_discovery


def get_capability_graph() -> CapabilityGraph:
    return _capability_graph
