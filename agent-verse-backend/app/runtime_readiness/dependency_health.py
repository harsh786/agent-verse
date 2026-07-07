from __future__ import annotations
import enum
from dataclasses import dataclass


class DepStatus(str, enum.Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass
class DependencyHealth:
    postgres: DepStatus = DepStatus.UNKNOWN
    redis: DepStatus = DepStatus.UNKNOWN
    embedder: DepStatus = DepStatus.UNKNOWN
    llm_provider: DepStatus = DepStatus.UNKNOWN
    celery: DepStatus = DepStatus.UNKNOWN
    web_search: DepStatus = DepStatus.UNKNOWN
    kg_store: DepStatus = DepStatus.UNKNOWN

    @classmethod
    def all_healthy(cls) -> "DependencyHealth":
        return cls(
            postgres=DepStatus.HEALTHY,
            redis=DepStatus.HEALTHY,
            embedder=DepStatus.HEALTHY,
            llm_provider=DepStatus.HEALTHY,
            celery=DepStatus.HEALTHY,
            web_search=DepStatus.HEALTHY,
            kg_store=DepStatus.HEALTHY,
        )
