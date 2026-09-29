"""Bounded, replica-shared store for Agent Runtime 2.0 plans and run traces.

GoalService creates one :class:`AgentExecutionPlan` and one :class:`AgentRunTrace`
per submitted goal. They used to live in two unbounded module-level dicts in
``app.api.agent_runtime``: memory grew with every goal for the life of the
process, and ``GET /agent-runtime/traces/{id}`` 404'd on every replica except
the one that happened to submit the goal.

This store keeps a small in-process LRU (bounded count + TTL) in front of Redis
(wired in the app lifespan). Redis keys are namespaced by tenant, so a lookup can
only ever find the caller's own tenant's records; the in-process copy is
tenant-checked too. A Redis outage degrades to the in-process copy.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar

from app.agent_runtime.models import (
    AgentExecutionPlan,
    AgentRole,
    AgentRunTrace,
    PlanStep,
    RiskLevel,
    StepStatus,
)

_log = logging.getLogger(__name__)

DEFAULT_MAX_ITEMS = 2_000
DEFAULT_TTL_SECONDS = 7 * 86_400

_T = TypeVar("_T")


def _known(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    names = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in names}


def _step_from_dict(data: dict[str, Any]) -> PlanStep:
    d = _known(PlanStep, data)
    if "role" in d:
        d["role"] = AgentRole(d["role"])
    if "risk_level" in d:
        d["risk_level"] = RiskLevel(d["risk_level"])
    if "status" in d:
        d["status"] = StepStatus(d["status"])
    return PlanStep(**d)


def plan_from_dict(data: dict[str, Any]) -> AgentExecutionPlan:
    d = _known(AgentExecutionPlan, data)
    d["steps"] = [_step_from_dict(s) for s in d.get("steps") or []]
    return AgentExecutionPlan(**d)


def trace_from_dict(data: dict[str, Any]) -> AgentRunTrace:
    d = _known(AgentRunTrace, data)
    if d.get("plan") is not None:
        d["plan"] = plan_from_dict(d["plan"])
    return AgentRunTrace(**d)


class _Lru:
    """Count- and age-bounded map of id → (stored_at, value)."""

    def __init__(self, max_items: int, ttl_seconds: float, clock: Callable[[], float]) -> None:
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._max = max(1, max_items)
        self._ttl = ttl_seconds
        self._clock = clock

    def __len__(self) -> int:
        return len(self._data)

    def put(self, key: str, value: Any) -> None:
        self._data[key] = (self._clock(), value)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        stored_at, value = item
        if self._clock() - stored_at > self._ttl:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value


class AgentRuntimeStore:
    def __init__(
        self,
        *,
        max_items: int = DEFAULT_MAX_ITEMS,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._plans = _Lru(max_items, ttl_seconds, clock)
        self._traces = _Lru(max_items, ttl_seconds, clock)
        self._ttl = int(ttl_seconds)
        self._redis: Any = None

    def set_redis(self, redis: Any) -> None:
        self._redis = redis

    @staticmethod
    def _key(kind: str, tenant_id: str, item_id: str) -> str:
        return f"agent_runtime:{kind}:{tenant_id}:{item_id}"

    async def _write_shared(self, kind: str, tenant_id: str, item_id: str, obj: Any) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(
                self._key(kind, tenant_id, item_id),
                json.dumps(dataclasses.asdict(obj), default=str),
                ex=self._ttl,
            )
        except Exception as exc:
            _log.warning("agent_runtime_store_write_failed kind=%s error=%s", kind, exc)

    async def _read_shared(
        self, kind: str, tenant_id: str, item_id: str, parse: Callable[[dict[str, Any]], _T]
    ) -> _T | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(self._key(kind, tenant_id, item_id))
            if not raw:
                return None
            return parse(json.loads(raw))
        except Exception as exc:
            _log.warning("agent_runtime_store_read_failed kind=%s error=%s", kind, exc)
            return None

    # ── plans ─────────────────────────────────────────────────────────────

    async def put_plan(self, plan: AgentExecutionPlan) -> None:
        self._plans.put(plan.plan_id, plan)
        await self._write_shared("plan", plan.tenant_id, plan.plan_id, plan)

    async def get_plan(self, tenant_id: str, plan_id: str) -> AgentExecutionPlan | None:
        plan = self._plans.get(plan_id)
        if plan is not None:
            return plan if plan.tenant_id == tenant_id else None
        shared = await self._read_shared("plan", tenant_id, plan_id, plan_from_dict)
        if shared is None or shared.tenant_id != tenant_id:
            return None
        self._plans.put(plan_id, shared)
        return shared

    # ── traces ────────────────────────────────────────────────────────────

    async def put_trace(self, trace: AgentRunTrace) -> None:
        self._traces.put(trace.trace_id, trace)
        await self._write_shared("trace", trace.tenant_id, trace.trace_id, trace)

    async def get_trace(
        self, tenant_id: str, trace_id: str, *, prefer_shared: bool = False
    ) -> AgentRunTrace | None:
        """A trace of *tenant_id*; ``prefer_shared`` re-reads Redis first (fresh)."""
        if prefer_shared and self._redis is not None:
            shared = await self._read_shared("trace", tenant_id, trace_id, trace_from_dict)
            if shared is not None and shared.tenant_id == tenant_id:
                self._traces.put(trace_id, shared)
                return shared
        trace = self._traces.get(trace_id)
        if trace is not None:
            return trace if trace.tenant_id == tenant_id else None
        shared = await self._read_shared("trace", tenant_id, trace_id, trace_from_dict)
        if shared is None or shared.tenant_id != tenant_id:
            return None
        self._traces.put(trace_id, shared)
        return shared

    async def update_trace(self, tenant_id: str, trace_id: str, **fields: Any) -> bool:
        """Set fields on a trace (e.g. success/error/duration_ms) and share it."""
        trace = await self.get_trace(tenant_id, trace_id, prefer_shared=True)
        if trace is None:
            return False
        for name, value in fields.items():
            if hasattr(trace, name):
                setattr(trace, name, value)
        await self.put_trace(trace)
        return True


# Process-wide instance (Redis wired in the app lifespan).
agent_runtime_store = AgentRuntimeStore()
