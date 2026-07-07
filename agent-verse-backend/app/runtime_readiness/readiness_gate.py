from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING
from app.runtime_readiness.dependency_health import DependencyHealth, DepStatus

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile

_BLOCKING_DEPS = {"postgres", "llm_provider"}
_DEGRADABLE_DEPS = {"redis", "embedder", "celery", "web_search", "kg_store"}


@dataclass
class ReadinessResult:
    ready: bool
    degraded: bool = False
    blocking_deps: list[str] = field(default_factory=list)
    degradation_warnings: list[str] = field(default_factory=list)
    unavailable_optional: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "degraded": self.degraded,
            "blocking_deps": self.blocking_deps,
            "degradation_warnings": self.degradation_warnings,
            "unavailable_optional": self.unavailable_optional,
        }


class ReadinessGate:
    def __init__(self, health: DependencyHealth) -> None:
        self._health = health

    def check(self, profile: "GoalRuntimeProfile") -> ReadinessResult:
        blocking: list[str] = []
        warnings: list[str] = []
        optional_down: list[str] = []
        health_map = {
            "postgres": self._health.postgres,
            "redis": self._health.redis,
            "embedder": self._health.embedder,
            "llm_provider": self._health.llm_provider,
            "celery": self._health.celery,
            "web_search": self._health.web_search,
            "kg_store": self._health.kg_store,
        }
        for dep, status in health_map.items():
            if status == DepStatus.UNAVAILABLE:
                if dep in _BLOCKING_DEPS:
                    blocking.append(dep)
                else:
                    optional_down.append(dep)
                    warnings.append(f"{dep} unavailable — functionality will be degraded")
            elif status == DepStatus.DEGRADED:
                warnings.append(f"{dep} is degraded — performance may be affected")
        return ReadinessResult(
            ready=len(blocking) == 0,
            degraded=len(optional_down) > 0 or len(warnings) > 0,
            blocking_deps=blocking,
            degradation_warnings=warnings,
            unavailable_optional=optional_down,
        )
