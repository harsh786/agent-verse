"""Runtime evidence for strategy certification, recorded when strategies actually run.

Certification used to be derived from *empty* evidence (``derive_state(capability, ())``)
and ``StrategyEvidenceStore`` was never written. This recorder appends one evidence row
per strategy a finished goal actually ran (read from ``execution_context
["strategy_execution"]`` — the constructed runtime, not the request), tenant-scoped:

* a goal on the admitted strategy-runtime-v2 path is ``canary`` evidence (production
  traffic on a canary tenant) — the only runtime category that feeds certification;
* any other run is recorded as ``production_run`` — counted, never certifying.

Storage is bounded: in memory, at most ``max_per_strategy`` rows per (tenant, strategy)
and ``max_keys`` keys (oldest evicted); in Postgres, rows expire after ``ttl`` (the
certification max age) and reads are capped. With a DB session factory the rows go to
``strategy_certification_evidence`` under the tenant's RLS context, so evidence is shared
across replicas; the in-memory copy is the fallback when no DB is wired.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.observability.logging import get_logger
from app.orchestration.strategy_certification import (
    REQUIRED_CERTIFICATION_CATEGORIES,
    RuntimeEvidence,
    StrategyEvidenceStore,
)
from app.orchestration.strategy_registry import StrategyCapability, StrategyRegistry

_logger = get_logger(__name__)

CANARY = "canary"
PRODUCTION_RUN = "production_run"
PASSED = "passed"
FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StrategyRunEvidence:
    strategy_id: str
    adapter_version: str
    state_schema_version: int
    evidence_type: str
    result: str
    observed_at: datetime
    artifact_reference: str

    def as_runtime_evidence(self) -> RuntimeEvidence | None:
        if self.evidence_type not in REQUIRED_CERTIFICATION_CATEGORIES:
            return None
        return RuntimeEvidence(
            category=self.evidence_type,  # type: ignore[arg-type]
            adapter_version=self.adapter_version,
            passed=self.result == PASSED,
            recorded_at=self.observed_at,
        )


DbFactory = Callable[[], Any]


def _from_row(row: dict[str, Any]) -> StrategyRunEvidence:
    return StrategyRunEvidence(
        strategy_id=str(row["strategy_id"]),
        adapter_version=str(row["adapter_version"]),
        state_schema_version=int(row["state_schema_version"]),
        evidence_type=str(row["evidence_type"]),
        result=str(row["result"]),
        observed_at=row["observed_at"],
        artifact_reference=str(row["artifact_reference"]),
    )


class StrategyEvidenceRecorder:
    def __init__(
        self,
        registry: StrategyRegistry,
        *,
        db_factory_getter: Callable[[], DbFactory | None] | None = None,
        max_per_strategy: int = 100,
        max_keys: int = 10_000,
        read_limit: int = 200,
        ttl: timedelta = timedelta(days=30),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if max_per_strategy <= 0 or max_keys <= 0 or read_limit <= 0:
            raise ValueError("evidence bounds must be positive")
        self._registry = registry
        # A getter (not the factory) so the lifespan's DB wiring is picked up later.
        self._db_factory_getter = db_factory_getter
        self._max_per_strategy = max_per_strategy
        self._max_keys = max_keys
        self._read_limit = read_limit
        self._ttl = ttl
        self._clock = clock
        self._memory: OrderedDict[tuple[str, str], deque[StrategyRunEvidence]] = OrderedDict()

    def _db_factory(self) -> DbFactory | None:
        if self._db_factory_getter is None:
            return None
        resolved = self._db_factory_getter()
        return resolved if callable(resolved) else None

    def _remember(self, tenant_id: str, item: StrategyRunEvidence) -> None:
        key = (tenant_id, item.strategy_id)
        rows = self._memory.get(key)
        if rows is None:
            rows = deque(maxlen=self._max_per_strategy)
            self._memory[key] = rows
            while len(self._memory) > self._max_keys:
                self._memory.popitem(last=False)
        else:
            self._memory.move_to_end(key)
        rows.appendleft(item)

    async def record_run(
        self,
        *,
        tenant_id: str,
        goal_id: str,
        strategy_ids: Iterable[str],
        succeeded: bool,
        runtime_path: str,
    ) -> list[StrategyRunEvidence]:
        """Append evidence for each strategy the goal ran; returns what was recorded."""
        now = self._clock()
        evidence_type = CANARY if runtime_path == "v2" else PRODUCTION_RUN
        recorded: list[StrategyRunEvidence] = []
        for strategy_id in dict.fromkeys(strategy_ids):
            try:
                capability = self._registry.resolve(strategy_id).capability
            except LookupError:
                continue
            item = StrategyRunEvidence(
                strategy_id=capability.strategy_id,
                adapter_version=capability.adapter_version,
                state_schema_version=capability.state_schema_version,
                evidence_type=evidence_type,
                result=PASSED if succeeded else FAILED,
                observed_at=now,
                artifact_reference=f"goal:{goal_id}",
            )
            self._remember(tenant_id, item)
            recorded.append(item)
            factory = self._db_factory()
            if factory is None:
                continue
            try:
                await StrategyEvidenceStore(factory).append(
                    tenant_id=tenant_id,
                    strategy_id=item.strategy_id,
                    adapter_version=item.adapter_version,
                    state_schema_version=item.state_schema_version,
                    evidence_type=item.evidence_type,
                    result=item.result,
                    artifact_reference=item.artifact_reference,
                    observed_at=now,
                    expires_at=now + self._ttl,
                    details={"goal_id": goal_id, "runtime_path": runtime_path},
                )
            except Exception as exc:
                _logger.warning(
                    "strategy_evidence_persist_failed",
                    strategy_id=item.strategy_id,
                    error_type=type(exc).__name__,
                    error=str(exc)[:200],
                )
        return recorded

    async def list_evidence(
        self, tenant_id: str, capability: StrategyCapability
    ) -> list[StrategyRunEvidence]:
        """Current (unexpired, same adapter/schema version) evidence, newest first."""
        now = self._clock()
        factory = self._db_factory()
        if factory is not None:
            try:
                rows = await StrategyEvidenceStore(factory).list_current(
                    tenant_id=tenant_id,
                    strategy_id=capability.strategy_id,
                    adapter_version=capability.adapter_version,
                    state_schema_version=capability.state_schema_version,
                    now=now,
                )
                return [_from_row(row) for row in rows[: self._read_limit]]
            except Exception as exc:
                _logger.warning(
                    "strategy_evidence_read_failed",
                    strategy_id=capability.strategy_id,
                    error_type=type(exc).__name__,
                )
        rows_in_memory = self._memory.get((tenant_id, capability.strategy_id), ())
        return [
            item
            for item in rows_in_memory
            if item.adapter_version == capability.adapter_version
            and item.state_schema_version == capability.state_schema_version
            and now - item.observed_at < self._ttl
        ][: self._read_limit]

    async def evidence_by_strategy(
        self, tenant_id: str, capabilities: Iterable[StrategyCapability]
    ) -> dict[str, list[StrategyRunEvidence]]:
        """``list_evidence`` for many strategies with a single DB round trip."""
        wanted = {c.strategy_id: c for c in capabilities}
        factory = self._db_factory()
        if factory is None:
            return {sid: await self.list_evidence(tenant_id, cap) for sid, cap in wanted.items()}
        grouped: dict[str, list[StrategyRunEvidence]] = {sid: [] for sid in wanted}
        try:
            rows = await StrategyEvidenceStore(factory).list_current_for_tenant(
                tenant_id=tenant_id, now=self._clock()
            )
        except Exception as exc:
            _logger.warning("strategy_evidence_read_failed", error_type=type(exc).__name__)
            return grouped
        for row in rows:
            capability = wanted.get(str(row["strategy_id"]))
            if (
                capability is None
                or str(row["adapter_version"]) != capability.adapter_version
                or int(row["state_schema_version"]) != capability.state_schema_version
            ):
                continue
            bucket = grouped[capability.strategy_id]
            if len(bucket) < self._read_limit:
                bucket.append(_from_row(row))
        return grouped

    async def certification_evidence(
        self, tenant_id: str, capability: StrategyCapability
    ) -> tuple[RuntimeEvidence, ...]:
        evidence = (
            item.as_runtime_evidence() for item in await self.list_evidence(tenant_id, capability)
        )
        return tuple(item for item in evidence if item is not None)

    @staticmethod
    def summarize(evidence: Iterable[StrategyRunEvidence]) -> dict[str, Any]:
        items = list(evidence)
        return {
            "runs": len(items),
            "succeeded": sum(1 for item in items if item.result == PASSED),
            "failed": sum(1 for item in items if item.result == FAILED),
            "canary_runs": sum(1 for item in items if item.evidence_type == CANARY),
            "last_run_at": items[0].observed_at.isoformat() if items else None,
        }


async def record_goal_strategy_evidence(
    recorder: StrategyEvidenceRecorder | None,
    *,
    tenant_id: str,
    goal_id: str,
    execution_context: Any,
    succeeded: bool | None,
    dry_run: bool = False,
) -> list[StrategyRunEvidence]:
    """Record evidence for the strategies a finished goal actually ran.

    Shared by the API (GoalService) and the Celery worker: only the in-process
    path used to record, so queued production goals left certification empty.
    ``succeeded`` is None for a non-terminal outcome (nothing is recorded), and
    dry runs never count.
    """
    if recorder is None or dry_run or succeeded is None:
        return []
    ctx = execution_context if isinstance(execution_context, dict) else {}
    execution = ctx.get("strategy_execution")
    if not isinstance(execution, dict):
        return []
    patterns = [str(item) for item in execution.get("patterns") or ()]
    if not patterns:
        return []
    return await recorder.record_run(
        tenant_id=tenant_id,
        goal_id=goal_id,
        strategy_ids=patterns,
        succeeded=succeeded,
        runtime_path=str(ctx.get("strategy_runtime_path") or "legacy"),
    )


__all__ = [
    "CANARY",
    "PRODUCTION_RUN",
    "StrategyEvidenceRecorder",
    "StrategyRunEvidence",
    "record_goal_strategy_evidence",
]
