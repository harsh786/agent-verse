"""Mixin extracted from app.agent.graph — zero semantic changes."""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import hashlib
import json
import time
from typing import Any

from app.agent.checkpoint_resume import COMPLETED_STEPS_KEY, RESUME_COMPLETED_KEY
from app.agent.goal_action_ledger import action_approval_key
from app.agent.nodes.planner_mixin import GRANTED_TOOLS_KEY
from app.agent.prompts import (
    EXECUTOR_SYSTEM,
)
from app.agent.risk_classifier import assess_step_risk
from app.agent.sanitization import (
    _EXECUTOR_CONTEXT_MAX_LENGTH,
)
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus, SubGoal
from app.agent.step_watchdog import approval_wait, run_step_with_deadline
from app.agent.tool_calls import ToolCall, extract_tool_call, repair_tool_call_arguments
from app.agent.tool_risk import classify_tool_risk
from app.governance.audit import AuditEvent
from app.governance.grants import enforce_tool_call
from app.governance.hitl import ApprovalStatus, HITLDeliveryError
from app.governance.permissions import ActionLevel
from app.governance.policies import PolicyResult
from app.intelligence.explainability import DecisionTrace
from app.observability.metrics import (
    record_approval_wait,
    record_tool_call,
    track_tool_call,
)
from app.pipeline.steps import smart_context_fetch
from app.providers.base import CompletionRequest, Message, ToolDefinition
from app.rag.contracts import RAGStrategy
from app.reliability.circuit_breaker import CircuitBreaker
from app.reliability.dedup import DeduplicationCache
from app.reliability.rollback import RollbackEngine
from app.tenancy.context import TenantContext

# Guardrails 2.0 integration
try:
    from app.guardrails_v2.engine import guardrails_engine
    from app.guardrails_v2.models import GuardrailLayer

    _GUARDRAILS_AVAILABLE = True
except ImportError:
    _GUARDRAILS_AVAILABLE = False
    guardrails_engine = None  # type: ignore[assignment]
    GuardrailLayer = None  # type: ignore[assignment]

import contextlib

from app.agent.graph_types import (  # noqa: F401
    STEP_FAILURES_KEY,
    GraphState,
    RetrievalEntryPointError,
    StepNotExecutedError,
)
from app.agent.nodes._helpers import (
    _extract_scope_value,
    _guardrail_should_fail_closed,
    _is_high_risk_step,
    resolve_effective_tool_risk,
)

# Per-goal tool-call budget. Once the goal has spent this many tool calls across
# all steps, the executor stops calling tools and is instructed to synthesize a
# final answer from the data already gathered. Without this, a weak planner
# re-searches on every replan and never converges (observed: 34 web searches
# across 11 replans before a goal failed). Overridable via ``_tool_call_budget``.
_DEFAULT_TOOL_CALL_BUDGET = 12
# "No timeout argument" for _await_approval_decision (None is a real value).
_NO_TIMEOUT_ARG: Any = object()

# Prefixes that mark plain LLM reasoning ("I'll call the tool…") rather than an
# actual tool result. Such text must never be cached or served as a step result.
_LLM_REASONING_PREFIXES = (
    "i'll ",
    "i will ",
    "i'll use",
    "i will use",
    "to complete",
    "let me ",
    "i need to ",
    "i can ",
    "i should ",
    "step 1",
    "first,",
    "first i",
    "i'll now",
    "i'll start",
    "i'll call",
    "i'll search",
    "now i'll",
    "next, i",
    "to search",
)

# Empty-collection markers — a "no rows" result is not a reusable answer.
_EMPTY_RESULT_MARKERS = (
    '{"issues": [], "total": 0}',
    '{"projects": []}',
    '{"items": []}',
    "[]",
    "{}",
)


# Pipeline outputs that are refusals/skips, not step results — never dedup-cached.
_DEDUP_NON_RESULT_PREFIXES = (
    "Step skipped",
    "Guardrail blocked",
    "GuardrailEnforcer blocked",
    "Action blocked",
    "[Bulkhead",
)


# Outputs that record a refusal (a gate said no) rather than a result. A refused
# step must never populate the semantic cache, or the refusal — or worse, a
# partial answer assembled around it — would be replayed to later runs.
_REFUSAL_MARKERS = (
    "tool call denied",
    "denied as destructive",
    "denied by governance",
    "denied by agent permission",
    "requires approval",
    "requires human approval",
    "was not executed",
    "[denied:",
    "[rejected:",
)


def _is_step_refusal(output: str | None) -> bool:
    """True when *output* is a governance refusal/skip, not a step result."""
    text = (output or "").strip()
    if text.startswith(_DEDUP_NON_RESULT_PREFIXES):
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


ACTION_SCOPE_KEY = "_action_scope_id"


def _action_scope_id(state: Any) -> str:
    """Identity of the goal's side-effecting actions (ledger + idempotency keys).

    The goal id, except for a goal-tree child: it runs under a new id per
    execution (its own checkpoint thread) but keeps ``<parent>:<sub_goal_id>``
    here, so a child re-run after a crash replays the calls its interrupted run
    made and re-issues an in-flight one under the same key (a01-F007-01).
    """
    ctx = getattr(state, "context", None)
    scope = ctx.get(ACTION_SCOPE_KEY) if isinstance(ctx, dict) else None
    return str(scope or getattr(state, "goal_id", "") or "")


# ── Governed semantic step cache ────────────────────────────────────────────
# A step's cached answer is reused only from INSIDE the governed pipeline, after
# every step-level gate (guardrails, action safety, permission matrix, policy,
# HITL) has passed. Each entry is an envelope naming the agent and the tools
# that produced it, so a hit is re-authorised against the tool-level gates
# (per-agent permissions, grants, policy, risk class) before it is served.
# Entries without a valid envelope (legacy or foreign writers) are never served.
_STEP_CACHE_MARKER = "_agentverse_step_cache"
_STEP_CACHE_VERSION = 1


@dataclasses.dataclass
class _StepCacheScope:
    """Per-step cache bookkeeping, carried through the pipeline in a contextvar
    (parallel wave steps each run in their own task, so each gets its own)."""

    prefetched: str | None = None
    embedding: list[float] | None = None
    tools: dict[str, str] = dataclasses.field(default_factory=dict)  # name -> server
    uncacheable: bool = False
    served: bool = False


_STEP_CACHE_SCOPE: contextvars.ContextVar[_StepCacheScope | None] = contextvars.ContextVar(
    "agentverse_step_cache_scope", default=None
)


def _checkpoint_degraded(state: Any) -> bool:
    """True once a checkpoint write failed for good this run (CORE-26).

    Every non-read tool call then fails closed: without a durable record of
    what already ran, a crash/redelivery would repeat it.
    """
    ctx = getattr(state, "context", None)
    return isinstance(ctx, dict) and bool(ctx.get("checkpoint_degraded"))


def _taint_step_cache() -> None:
    """Mark the current step's result as never cacheable (a gate refused or a
    side-effecting path ran)."""
    scope = _STEP_CACHE_SCOPE.get()
    if scope is not None:
        scope.uncacheable = True


def _note_step_tool(name: str, server_name: str = "") -> None:
    """Record a tool the current step dispatched. Only read-only tools keep the
    step cacheable: replaying a write from cache would report an action that
    never happened."""
    scope = _STEP_CACHE_SCOPE.get()
    if scope is None:
        return
    scope.tools[name] = server_name or ""
    if classify_tool_risk(name, server_name or "") != "read":
        scope.uncacheable = True


def _wrap_step_cache_entry(output: str, agent_id: str, tools: dict[str, str]) -> str:
    return json.dumps(
        {
            _STEP_CACHE_MARKER: _STEP_CACHE_VERSION,
            "agent_id": agent_id,
            "tools": [{"name": n, "server": s} for n, s in sorted(tools.items())],
            "output": output,
        }
    )


def _unwrap_step_cache_entry(raw: str | None) -> tuple[str, str, dict[str, str]] | None:
    """Return ``(output, agent_id, tools)`` for a valid envelope, else None."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get(_STEP_CACHE_MARKER) != _STEP_CACHE_VERSION:
        return None
    output, agent_id, tools = data.get("output"), data.get("agent_id"), data.get("tools")
    if not isinstance(output, str) or not isinstance(agent_id, str) or not isinstance(tools, list):
        return None
    parsed: dict[str, str] = {}
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            return None
        parsed[tool["name"]] = str(tool.get("server") or "")
    return output, agent_id, parsed


# LLM-generated template values that must never reach a real MCP server.
_PLACEHOLDER_ARG_PATTERNS = (
    "your_organization",
    "your_repository",
    "your_org",
    "your_repo",
    "your_project",
    "your_workspace",
    "your_team",
    "your_board",
    "<organization>",
    "<repository>",
    "<repo>",
    "{organization}",
    "{repository}",
    "{repo}",
    "example.com",
    "placeholder",
)


def _log_background_failure(task: Any) -> None:
    """Done-callback: retrieve and log a fire-and-forget task's exception."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        with contextlib.suppress(Exception):
            from app.observability.logging import get_logger

            get_logger(__name__).warning("background_task_failed", error=str(exc)[:200])


def _is_uncacheable_output(output: str | None) -> bool:
    """Single source of truth for "must not enter or be served from the cache".

    Applied symmetrically on BOTH write and read so a poisoned entry — an error,
    an approval placeholder, an empty collection, or plain LLM reasoning text —
    can never be stored *or* served as if it were a successful tool result.
    Previously the read paths were weaker than the write path, so a stale
    "requires approval (non-supervised mode)" or error response could be served
    as a fake success and satisfy the verifier.
    """
    text = (output or "").strip()
    if len(text) < 10:
        return True
    lowered = text.lower()
    if (
        lowered.startswith('{"error')
        or lowered.startswith("error:")
        or "requires approval" in lowered
        or "model_not_found" in lowered
        or "invalid model" in lowered
        or "rate_limit_exceeded" in lowered
        or "mcp client unavailable" in lowered
        or "tool not available" in lowered
        or "argument validation failed" in lowered
        or "circuit open" in lowered
    ):
        return True
    if text in _EMPTY_RESULT_MARKERS or '"total": 0' in text or '"issues": []' in text or (
        '"projects": []' in text
    ):
        return True
    # Plain LLM reasoning text ("I'll call the tool…") — not an actual result.
    if lowered.startswith(_LLM_REASONING_PREFIXES):
        return True
    if "will use" in lowered and "tool" in lowered:
        return True
    if "will call" in lowered and len(text) < 500:  # noqa: SIM103
        return True
    return False


class ExecutorMixin:
    """Mixin: _node_execute, _execute_step_with_loop, _execute_step, _execute_step_with_cache."""

    def _record_stream_failure(self, exec_model: str, start: float) -> None:
        """A failed executor step: every model actually attempted failed (PROV-21),
        not just the step's nominal model."""
        for model in getattr(self, "_failed_models", None) or [exec_model]:
            self._record_provider_health(model, ok=False, start=start)

    def _record_provider_health(self, model: str, *, ok: bool, start: float) -> None:
        """D-13: report a live LLM provider-call outcome to the model router's health
        policy so orchestrator failover learns. Fully guarded — never raises."""
        from app.ai_router.health_feed import record_llm_outcome

        # PROV-16: also the registry's provider health (GET /models/health).
        record_llm_outcome(
            model=model or "", ok=ok, latency_ms=(time.monotonic() - start) * 1000.0
        )
        router = getattr(self, "_model_router", None)
        if router is None or not hasattr(router, "record_provider_result"):
            return
        try:
            latency_ms = (time.monotonic() - start) * 1000.0
            # Pass ok/latency_ms by keyword so the call is robust to both the
            # adapter (positional model) and the raw orchestrator (keyword-only ok).
            router.record_provider_result(model or "", ok=ok, latency_ms=latency_ms)
        except Exception:
            pass

    async def _record_rpa_failure(
        self,
        state: AgentState,
        tenant_ctx: TenantContext,
        *,
        tool_name: str,
        url: str,
        error: str,
    ) -> None:
        """MEM-37: write an RPA failure to execution memory, awaited (one INSERT).

        It was a fire-and-forget task, so a lost write never reached the goal.
        A lost write (False or an error) marks ``execution_memory_write`` in
        ``memory_degraded``, as the success path does.
        """
        exec_memory = getattr(self, "_exec_memory", None)
        db = getattr(self, "_db_session_factory", None)
        if exec_memory is None or db is None:
            return
        exc: BaseException | None = None
        try:
            ok = await exec_memory.record_failure_async(
                goal=state.goal,
                error=f"RPA {tool_name} failed on {url}: {error}",
                tenant_id=tenant_ctx.tenant_id,
                db=db,
                goal_id=str(state.goal_id or ""),
            )
        except Exception as caught:
            ok, exc = False, caught
        if ok is False:
            await self._memory_degraded(state, "execution_memory_write", exc)  # type: ignore[attr-defined]

    async def _record_tool_reliability(
        self,
        tenant_ctx: TenantContext,
        tool_name: str,
        *,
        success: bool,
        started: float,
        error: object = "",
    ) -> None:
        """MEM-01: record one real tool dispatch outcome in ToolReliabilityStore.

        Awaited (not fire-and-forget) so the next step's reliability read sees it.
        A store failure is logged; it never fails the tool call.
        """
        store = getattr(self, "_tool_reliability_store", None)
        if store is None or not tool_name:
            return
        error_class = ""
        if not success:
            error_class = (
                type(error).__name__ if isinstance(error, BaseException) else str(error or "")
            )[:120]
        try:
            await store.record(
                tenant_id=tenant_ctx.tenant_id,
                tool_name=tool_name,
                success=success,
                latency_ms=(time.monotonic() - started) * 1000.0,
                error=error_class,
            )
        except Exception as exc:
            self._logger.warning(
                "tool_reliability_record_failed", tool=tool_name, error=str(exc)[:200]
            )

    async def _tool_reliability_view(
        self, state: AgentState, tenant_ctx: TenantContext
    ) -> tuple[dict[str, float], set[str]]:
        """MEM-01: ``({unreliable tool: success_rate}, {blacklisted tools})``.

        A store outage is logged and flagged in ``state.context`` (never read as
        "every tool is reliable").
        """
        store = getattr(self, "_tool_reliability_store", None)
        if store is None:
            return {}, set()
        try:
            rows = await store.get_unreliable_tools(tenant_id=tenant_ctx.tenant_id)
        except Exception as exc:
            self._logger.warning(
                "tool_reliability_read_failed",
                tenant_id=tenant_ctx.tenant_id,
                error=str(exc)[:200],
            )
            state.context["_tool_reliability_degraded"] = True
            return {}, set()
        unreliable: dict[str, float] = {}
        blacklisted: set[str] = set()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("tool_name") or "")
            if not name:
                continue
            unreliable[name] = float(row.get("success_rate", 0.0) or 0.0)
            if row.get("blacklisted"):
                blacklisted.add(name)
        return unreliable, blacklisted

    async def _charge_grant_spend(
        self, state: AgentState, tenant_ctx: TenantContext, cost_usd: float
    ) -> None:
        """Record *cost_usd* against the grant that authorised this goal's calls.

        It used to go to the FIRST active capped grant, inside
        ``suppress(Exception)``: spend under one grant exhausted an unrelated one
        while the authorising grant's cap never bound, and a failed write was
        silent. The tool gate records ``_authorizing_grant_id``; with a single
        capped grant that one is charged; otherwise the spend is logged as
        unattributed rather than charged to a guess.
        """
        try:
            from datetime import UTC as _UTC
            from datetime import datetime as _dt

            from app.governance.grants.enforcer import active_grants

            now = _dt.now(_UTC)
            grants = await active_grants(
                self._grant_store, tenant_ctx.tenant_id, self._agent_id or "", now
            )
            capped = [g for g in grants if g.max_cost_usd is not None]
            authorising = state.context.get("_authorizing_grant_id")
            target = next(
                (g for g in capped if g.grant_id == authorising),
                capped[0] if len(capped) == 1 else None,
            )
            if target is not None:
                await self._grant_store.record_spend(
                    tenant_ctx.tenant_id, target.grant_id, cost_usd
                )
            elif capped:
                # Several capped grants and the tool gate has not yet decided which
                # one authorises this goal (the step-start charge runs before the
                # gate): hold the spend and charge it to the authorising grant as
                # soon as the gate names it (GRANT-03) — never to a guess, never lost.
                state.context["_pending_grant_spend"] = (
                    float(state.context.get("_pending_grant_spend", 0.0) or 0.0) + cost_usd
                )
                self._logger.info(
                    "grant_spend_deferred", capped_grants=len(capped), cost_usd=cost_usd
                )
        except Exception as exc:
            # Never silent: an unrecorded spend means the cap cannot bind.
            self._logger.warning("grant_spend_record_failed", error=str(exc)[:200])

    async def _set_authorizing_grant(
        self, state: AgentState, tenant_ctx: TenantContext, grant_id: str
    ) -> None:
        """Record the grant the tool gate matched and charge any deferred spend to it."""
        state.context["_authorizing_grant_id"] = grant_id
        pending = float(state.context.pop("_pending_grant_spend", 0.0) or 0.0)
        if pending > 0.0 and self._grant_store is not None:
            await self._charge_grant_spend(state, tenant_ctx, pending)

    async def _delegate_grants_to_child(
        self, state: Any, tenant_ctx: Any, child_agent_id: str
    ) -> list[Any]:
        """Grantex delegation: mint narrowed grants for a spawned child agent.

        The parent is the agent whose grants the tool gate enforces
        (``self._agent_id``, else the goal's ``context['agent_id']``) — it used to
        be ``state.agent_id``, a field AgentState does not have, so every child got
        no grant. A failure is logged, never silent; the child then holds no grant
        and is denied under enforcement (fail closed, never over-permitted).
        """
        parent = str(
            getattr(self, "_agent_id", None)
            or (getattr(state, "context", None) or {}).get("agent_id")
            or ""
        )
        if not parent or not child_agent_id:
            self._logger.warning(
                "grant_delegation_skipped", parent=parent, child=child_agent_id
            )
            return []
        try:
            from datetime import UTC, datetime

            from app.governance.grants import delegate_active_grants

            minted: list[Any] = await delegate_active_grants(
                self._grant_store,
                tenant_id=tenant_ctx.tenant_id,
                parent_agent_id=parent,
                child_agent_id=child_agent_id,
                now=datetime.now(UTC),
            )
            return minted
        except Exception as exc:
            self._logger.warning(
                "grant_delegation_failed", parent=parent, child=child_agent_id, error=str(exc)[:200]
            )
            return []

    async def _agent_permission_gate(
        self,
        *,
        state: AgentState,
        tenant_ctx: TenantContext,
        tool_name: str,
        step: str,
    ) -> str | None:
        """Enforce the agent's persisted ``agent_permissions`` rules for one tool call.

        Returns ``None`` to allow, or a denial reason. APPROVAL blocks on a HITL
        decision (denied when no gateway is wired). A load failure fails CLOSED
        for high-risk tools and open (with a warning) for read-only ones.
        """
        agent_id = getattr(self, "_agent_id", None)
        db = getattr(self, "_db_session_factory", None)
        if not agent_id or db is None or tenant_ctx is None:
            return None
        from app.governance.agent_permissions import (
            AgentPermissionsUnavailableError,
            load_agent_permissions,
            resolve_level,
        )

        try:
            rules = await load_agent_permissions(db, tenant_ctx.tenant_id, agent_id)
        except AgentPermissionsUnavailableError:
            if classify_tool_risk(tool_name) in ("write_high", "destructive") or (
                _guardrail_should_fail_closed(step, state.context.get("_risk_level"))
            ):
                return "agent permissions unavailable; failing closed for a high-risk tool"
            self._logger.warning(
                "agent_permissions_unavailable_allowing_low_risk", tool=tool_name
            )
            return None
        if not rules:
            return None
        counts: dict[str, int] = state.context.setdefault("_agent_perm_calls", {})
        level, rule, reason = resolve_level(
            rules,
            tool_name,
            scope_value=_extract_scope_value(step),
            goal_call_count=int(counts.get(tool_name, 0)),
        )
        if level is None:
            return None
        if level in (ActionLevel.ALLOW, ActionLevel.ALLOW_LOG):
            daily_denial = await self._reserve_daily_permission_call(
                tenant_ctx, agent_id, tool_name, rule
            )
            if daily_denial is not None:
                return daily_denial
            counts[tool_name] = int(counts.get(tool_name, 0)) + 1
            return None
        if level is ActionLevel.DENY:
            return f"denied by agent permission ({reason})"
        # APPROVAL: a persisted per-agent approval rule must actually block.
        if self._hitl_gateway is None:
            return "agent permission requires approval but no approval gateway is configured"
        _perm_action = f"{tool_name}: {step}"[:500]
        _perm_key = action_approval_key(_perm_action, tool_name)
        req_id = ""
        _reused = await self._reuse_approval(
            state, tenant_ctx, _perm_key, action=_perm_action, scope="agent_permission"
        )
        if not _reused:
            try:
                req_id = await self._file_approval_request(
                    goal_id=state.goal_id,
                    action=_perm_action,
                    risk_level="high",
                    tenant_ctx=tenant_ctx,
                )
            except PermissionError as exc:
                return str(exc)  # not durable, so nobody could approve it: deny
            await self._emit({"type": "waiting_approval", "request_id": req_id, "action": step})
            final_status = await self._await_approval_decision(
                req_id, tenant_ctx=tenant_ctx, timeout=self._hitl_timeout
            )
            if final_status != ApprovalStatus.APPROVED:
                return f"agent permission approval {str(final_status).lower()}"
            await self._remember_approval(
                state, tenant_ctx, _perm_key, request_id=req_id, action=_perm_action
            )
        daily_denial = await self._reserve_daily_permission_call(
            tenant_ctx, agent_id, tool_name, rule
        )
        if daily_denial is not None:
            return daily_denial
        counts[tool_name] = int(counts.get(tool_name, 0)) + 1
        if not _reused:
            await self._emit({"type": "approval_granted", "request_id": req_id})
        return None

    async def _guard_tool_args(
        self,
        tool_name: str,
        arguments: Any,
        step: str,
        state: AgentState,
        tenant_ctx: TenantContext,
    ) -> None:
        """Tool-argument guardrails for ONE tool call; raises PermissionError.

        Used for the primary call and every parallel extra call of a turn.
        GuardrailEngine v2 (app state) then Guardrails 2.0 TOOL_ARGS; an engine
        error fails closed on high-risk work (SAFE-4).
        """
        _guardrail_engine_v2 = (
            getattr(self._app_state, "guardrail_engine", None) if self._app_state else None
        )
        if _guardrail_engine_v2 is not None:
            try:
                from app.intelligence.guardrail_engine import GuardrailContext as _GCtx

                _ge_ctx = _GCtx(
                    tenant_id=tenant_ctx.tenant_id if tenant_ctx else "",
                    goal_id=state.goal_id or "",
                    agent_id=self._agent_id or "",
                    domain=getattr(tenant_ctx, "domain_context", "general")
                    if tenant_ctx
                    else "general",
                )
                _ge_args_result = await _guardrail_engine_v2.evaluate_tool_args(
                    tool_name=tool_name,
                    arguments=arguments or {},
                    context=_ge_ctx,
                )
                if not _ge_args_result.allowed:
                    _ge_viol = (
                        _ge_args_result.violations[0] if _ge_args_result.violations else None
                    )
                    raise PermissionError(
                        f"Guardrail blocked tool call '{tool_name}': "
                        f"{_ge_viol.matched_pattern if _ge_viol else 'policy violation'}"
                    )
            except PermissionError:
                raise
            except Exception as _ge_exc:
                self._logger.warning("guardrail_engine_v2_pre_check_failed", error=str(_ge_exc))
                # SAFE-4 (P0-15): an errored guardrail check must not read as
                # "allowed" on high-risk work — fail closed.
                if _guardrail_should_fail_closed(step, state.context.get("_risk_level")):
                    raise PermissionError(
                        f"Guardrail check errored on high-risk tool "
                        f"'{tool_name}'; failing closed."
                    ) from _ge_exc

        # Guardrail check: tool_args (Guardrails 2.0)
        if _GUARDRAILS_AVAILABLE and guardrails_engine is not None and tenant_ctx:
            try:
                _g2_args_str = (
                    json.dumps(arguments) if isinstance(arguments, dict) else str(arguments)
                )
                _g2_args_result = await guardrails_engine.evaluate(
                    content=_g2_args_str,
                    layer=GuardrailLayer.TOOL_ARGS,
                    tenant_id=tenant_ctx.tenant_id,
                    goal_id=getattr(state, "goal_id", None),
                    step_description=step,
                )
                if _g2_args_result.get("blocked"):
                    _g2_viol_name = (_g2_args_result.get("violations") or [{}])[0].get(
                        "rule_name", "policy"
                    )
                    raise PermissionError(f"Tool call blocked by guardrail: {_g2_viol_name}")
            except PermissionError:
                raise
            except Exception as _g2_exc:
                # SAFE-4 (P0-15): fail closed on high-risk work when the
                # guardrail engine errors instead of silently allowing.
                if _guardrail_should_fail_closed(step, state.context.get("_risk_level")):
                    raise PermissionError(
                        f"Guardrail (tool_args) errored on high-risk step "
                        f"'{tool_name}'; failing closed."
                    ) from _g2_exc

    def _prepare_call_arguments(self, tool_call: Any, state: AgentState) -> str | None:
        """Normalise ``tool_call.arguments`` in place to the tool's schema (MCPGOV-01).

        Returns None when the call may proceed to governance, or the rejection
        text when its arguments do not fit the schema (unknown or missing keys):
        such a call is never governed, approved or dispatched.
        """
        from app.agent.tool_calls import prepare_tool_arguments

        tool_context = state.context.get("tool_context")
        tool_ref = (
            tool_context.find_tool(tool_call.tool)
            if tool_context is not None and hasattr(tool_context, "find_tool")
            else None
        )
        prepared = prepare_tool_arguments(
            tool_call.arguments, getattr(tool_ref, "input_schema", None) or {}
        )
        tool_call.arguments = prepared.arguments
        if not prepared.errors:
            return None
        return (
            f"[ARGUMENT VALIDATION FAILED] Tool '{tool_call.tool}' "
            f"called with invalid arguments:\n"
            + "\n".join(f"  - {e}" for e in prepared.errors)
            + "\nPlease retry with correct arguments from the tool schema."
        )

    async def _tool_policy_gate(
        self,
        *,
        tool_name: str,
        step: str,
        state: AgentState,
        tenant_ctx: TenantContext,
        already_checked: str | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> str | None:
        """Tenant tool policy for the tool a call ACTUALLY targets.

        Returns None to allow or a denial reason (the call is not run). The
        step-level check only sees a tool name guessed from the step text, so it
        is skipped here only for that same name (already enforced, and any
        approval already granted). REQUIRE_APPROVAL blocks on a human decision
        in a supervised run (raising PermissionError unless APPROVED) and is a
        denial everywhere else. An evaluation error fails closed.
        """
        # Agent-scoped API key restriction (AGKEY-01): enforced at dispatch
        # whether or not a policy engine is wired.
        from app.auth.agent_credentials import agent_key_tool_denial

        _key_denial = agent_key_tool_denial(tenant_ctx, tool_name) if tool_name else None
        if _key_denial is not None:
            record_tool_call(tool_name, "agent_key", "denied", 0.0)
            return _key_denial
        # Policy-as-code rules (POL-01: they were CRUD + dry-run only).
        if tool_name and tenant_ctx is not None:
            from app.governance.policy_rules import policy_rules_denial

            _rule_denial = await policy_rules_denial(
                getattr(self, "_db_session_factory", None),
                tenant_ctx.tenant_id,
                {
                    "tool_name": tool_name,
                    "arguments": arguments or {},
                    "agent_id": getattr(self, "_agent_id", None) or "",
                    "goal_id": getattr(state, "goal_id", "") or "",
                    "step": step,
                },
            )
            if _rule_denial is not None:
                record_tool_call(tool_name, "policy_rule", "denied", 0.0)
                return _rule_denial
            # Compliance bundle required_hitl_for (TRUST-02: it had no caller).
            from app.governance.compliance_bundles import bundle_hitl_requirement

            try:
                _bundle = await bundle_hitl_requirement(
                    getattr(self, "_db_session_factory", None), tenant_ctx.tenant_id, tool_name
                )
            except Exception as exc:
                record_tool_call(tool_name, "compliance_bundle", "denied", 0.0)
                return (
                    f"compliance bundles could not be read ({type(exc).__name__}); "
                    "failing closed"
                )
            if _bundle:
                _what = f"compliance bundle '{_bundle}' on tool '{tool_name}'"
                _b_denial = self._approval_unawaitable_error(step, _what)
                if _b_denial is not None or self._hitl_gateway is None:
                    record_tool_call(tool_name, "compliance_bundle", "approval_required", 0.0)
                    return str(_b_denial or f"{_what} requires a human approval")
                _b_action = f"{tool_name}: {step}"[:500]
                await self._await_tool_approval(
                    tool_name=tool_name,
                    action=_b_action,
                    risk_level="high",
                    state=state,
                    tenant_ctx=tenant_ctx,
                    approval_key=action_approval_key(_b_action, tool_name, arguments),
                )
        if self._policy_engine is None or not tool_name or tool_name == already_checked:
            return None
        try:
            result = self._policy_engine.evaluate(tool_name=tool_name, tenant_ctx=tenant_ctx)
        except Exception as exc:
            return f"tool policy could not be evaluated ({type(exc).__name__}); failing closed"
        if result == PolicyResult.DENY:
            record_tool_call(tool_name, "policy", "denied", 0.0)
            return "denied by tenant tool policy"
        if result == PolicyResult.REQUIRE_APPROVAL:
            denial = self._approval_unawaitable_error(
                step, f"tenant policy on tool '{tool_name}'"
            )
            if denial is not None or self._hitl_gateway is None:
                record_tool_call(tool_name, "policy", "approval_required", 0.0)
                return str(denial or f"tool '{tool_name}' requires approval by policy")
            _p_action = f"{tool_name}: {step}"[:500]
            await self._await_tool_approval(
                tool_name=tool_name,
                action=_p_action,
                risk_level="high",
                state=state,
                tenant_ctx=tenant_ctx,
                approval_key=action_approval_key(_p_action, tool_name, arguments),
            )
        return None

    async def _await_tool_approval(
        self,
        *,
        tool_name: str,
        action: str,
        risk_level: str,
        state: AgentState,
        tenant_ctx: TenantContext,
        approval_key: str | None = None,
    ) -> None:
        """File a durable approval for one tool call and block on the decision.

        Raises PermissionError unless a human explicitly APPROVED it. With an
        ``approval_key`` (the exact action / call), an approval of the identical
        request earlier in this goal is reused instead of asking again (OI-1).
        """
        if self._hitl_gateway is None:
            raise PermissionError(f"Tool '{tool_name}' requires approval; no gateway.")
        if approval_key is not None and await self._reuse_approval(
            state, tenant_ctx, approval_key, action=action, scope="tool_call"
        ):
            return
        req_id = await self._file_approval_request(
            goal_id=state.goal_id, action=action, risk_level=risk_level, tenant_ctx=tenant_ctx
        )
        await self._emit(
            {"type": "waiting_approval", "request_id": req_id, "action": action,
             "tool": tool_name}
        )
        started = time.monotonic()
        final_status = await self._await_approval_decision(
            req_id, tenant_ctx=tenant_ctx, timeout=self._hitl_timeout
        )
        record_approval_wait(time.monotonic() - started)
        if final_status == ApprovalStatus.REJECTED:
            raise PermissionError(f"Tool '{tool_name}' was rejected by human approver.")
        if final_status == ApprovalStatus.TIMED_OUT:
            raise PermissionError(f"Tool '{tool_name}' approval timed out.")
        if final_status != ApprovalStatus.APPROVED:
            raise PermissionError(f"Tool '{tool_name}' was not approved ({final_status}).")
        if approval_key is not None:
            await self._remember_approval(
                state, tenant_ctx, approval_key, request_id=req_id, action=action
            )
        await self._emit({"type": "approval_granted", "request_id": req_id})

    async def _reserve_daily_permission_call(
        self, tenant_ctx: TenantContext, agent_id: str, tool_name: str, rule: Any
    ) -> str | None:
        """Enforce the matched rule's ``daily_limit``; a denial reason or None."""
        limit = getattr(rule, "daily_limit", None)
        if not limit:
            return None
        from app.governance.agent_permissions import (
            DailyLimitUnavailableError,
            reserve_daily_call,
        )

        aps: Any = getattr(self, "_app_state", None)
        redis = getattr(getattr(aps, "state", aps), "_redis", None) if aps is not None else None
        try:
            ok = await reserve_daily_call(redis, tenant_ctx.tenant_id, agent_id, tool_name, limit)
        except DailyLimitUnavailableError:
            return "agent permission daily limit could not be checked; failing closed"
        return None if ok else f"denied by agent permission (daily_limit {limit} reached)"

    async def _node_execute(self, state: GraphState) -> dict[str, Any]:
        agent_state: AgentState = state["agent_state"]
        tenant_ctx: TenantContext = state["tenant_ctx"]
        plan: list[str] = state.get("plan") or agent_state.plan

        agent_state.status = GoalStatus.EXECUTING
        # Steps that could not run in THIS execute pass (StepNotExecutedError). The
        # verifier fails verification deterministically when this is non-empty.
        agent_state.context[STEP_FAILURES_KEY] = []
        # Tool outcomes are judged per pass: a replan's pass starts clean.
        from app.agent.tool_outcomes import ledger_for

        ledger_for(self).reset()

        # Goal-tree decomposition: delegate large plans to parallel sub-agents.
        # A parent re-queued by its last goal-tree child resumes the tree whatever
        # its re-plan's length; a goal-tree child never builds a tree itself.
        from app.agent.fanout_ledger import FANOUT_KIND_KEY, FANOUT_WAIT_KEY

        _fanout_wait = agent_state.context.get(FANOUT_WAIT_KEY)
        _resume_tree = isinstance(_fanout_wait, dict) and _fanout_wait.get("kind") == "goal_tree"
        _tree_child = agent_state.context.get(FANOUT_KIND_KEY) == "goal_tree"
        if (
            self._enable_goal_tree
            and not _tree_child
            and (len(plan) >= self._goal_tree_threshold or _resume_tree)
        ):
            from app.agent.goal_tree import advance_goal_tree, execute_goal_tree

            def _sub_graph_factory() -> Any:
                from opentelemetry import context as otel_context

                from app.agent.graph import (
                    AgentGraph as AgentGraph_,  # local import to avoid circular
                )

                graph = AgentGraph_(
                    planner=self._planner,
                    executor=self._executor,
                    verifier=self._verifier,
                    max_iterations=5,
                    # Inherit governance + reliability from parent
                    permission_matrix=self._permission_matrix,
                    audit_log=self._audit_log,
                    cost_controller=self._cost_controller,
                    hitl_gateway=self._hitl_gateway,
                    policy_engine=self._policy_engine,
                    result_processor=self._result_processor,
                    dedup_cache=DeduplicationCache(),  # fresh instance per sub-agent
                    rollback_engine=RollbackEngine(),  # fresh instance per sub-agent
                    guardrail_checker=self._guardrail_checker,
                    # Inherit memory + RAG
                    exec_memory=self._exec_memory,
                    long_term_memory=self._long_term_memory,
                    knowledge_store=self._knowledge_store,
                    retrieval_gateway=self._retrieval_gateway,
                    mcp_client=self._mcp_client,
                    eval_runner=self._eval_runner,
                    # Sub-agents don't recurse into goal trees
                    enable_goal_tree=False,
                    autonomy_mode=self._autonomy_mode,
                    # Grant enforcement, model routing, cost and concurrency
                    # limits are inherited too. They were omitted, so a plan
                    # large enough to fan out to sub-agents escaped grant
                    # enforcement (sub-agents ran tools with no grant check) and
                    # the tenant's bulkhead/cost tracking.
                    grant_store=getattr(self, "_grant_store", None),
                    enforce_grants=bool(getattr(self, "_enforce_grants", False)),
                    model_router=getattr(self, "_model_router", None),
                    bulkhead_registry=getattr(self, "_bulkhead_registry", None),
                    cost_tracker=getattr(self, "_cost_tracker", None),
                    semantic_cache=getattr(self, "_semantic_cache", None),
                )
                # Grants are keyed by agent: a sub-agent acts as its parent agent.
                graph._agent_id = getattr(self, "_agent_id", None)
                graph._db_session_factory = getattr(self, "_db_session_factory", None)
                # A child's synthetic id has no goals row (and exceeds goals.id), so
                # every checkpoint write failed the FK — a wasted, swallowed DB round
                # trip per step. Children do not checkpoint themselves; each child's
                # completion is recorded in the parent's fan-out ledger instead, so
                # a resumed parent skips finished children (CORE-10).
                graph._checkpoints_enabled = False
                graph._agent_collection_ids = list(self._agent_collection_ids)
                graph._event_callback = self._event_callback
                graph._parent_trace_context = otel_context.get_current()
                return graph

            try:
                _tree_model = ""
                if self._model_router is not None:
                    with contextlib.suppress(Exception):
                        _tree_model = self._model_router.model_for("planning") or ""
                from app.agent.fanout_ledger import goal_child_timeout_default
                from app.agent.fanout_ledger import ledger_for as fanout_ledger_for
                from app.agent.supervisor import SUBGOAL_MARKER
                from app.providers.guarded_completion import GuardedDecisionProvider

                # Decomposition through the guarded path: circuit breaker,
                # timeout and a charge to the parent goal's budget (it called
                # planner.complete directly — uncharged, no circuit).
                _tree_planner = GuardedDecisionProvider(
                    self._planner,
                    role="goal_tree",
                    tenant_ctx=tenant_ctx,
                    goal_id=agent_state.goal_id,
                )
                _tree_ledger = fanout_ledger_for(
                    getattr(self, "_db_session_factory", None),
                    tenant_id=getattr(tenant_ctx, "tenant_id", None),
                    parent_goal_id=agent_state.goal_id,
                    kind="goal_tree",
                )
                _tree_goal_service = getattr(self, "_goal_service", None)
                # a01-F007-01 / F006-05: on a worker, children are real goals and
                # the parent parks between waves instead of holding its slot.
                _durable_tree = (
                    _tree_ledger is not None
                    and _tree_goal_service is not None
                    and bool(getattr(self, "_fanout_continuations", False))
                    and not agent_state.context.get(SUBGOAL_MARKER)
                )
                sub_goals: list[SubGoal]
                if _durable_tree:
                    assert _tree_ledger is not None
                    try:
                        _advance = await advance_goal_tree(
                            agent_state.goal,
                            planner=_tree_planner,
                            tenant_ctx=tenant_ctx,
                            parent_goal_id=agent_state.goal_id,
                            goal_service=_tree_goal_service,
                            ledger=_tree_ledger,
                            agent_id=getattr(self, "_agent_id", None),
                            event_callback=self._event_callback,
                            model=_tree_model,
                            child_timeout_s=goal_child_timeout_default(
                                agent_state.context, getattr(self, "_subgoal_timeout_s", None)
                            ),
                        )
                    except Exception as tree_exc:
                        # Children may already run as goals: never fall through to
                        # executing the same plan in-process next to them.
                        agent_state.status = GoalStatus.FAILED
                        agent_state.error_message = (
                            f"goal tree could not continue: {type(tree_exc).__name__}"
                        )
                        await self._emit(
                            {"type": "goal_tree_error", "error": type(tree_exc).__name__}
                        )
                        return {"agent_state": agent_state}
                    if _advance.parked:
                        agent_state.status = GoalStatus.WAITING_CHILDREN
                        agent_state.context["fanout_parked"] = "goal_tree"
                        await self._emit(
                            {
                                "type": "goal_waiting_children",
                                "pattern": "goal_tree",
                                "pending": _advance.pending,
                            }
                        )
                        return {"agent_state": agent_state}
                    sub_goals = _advance.sub_goals
                else:
                    sub_goals = await execute_goal_tree(
                        agent_state.goal,
                        planner=_tree_planner,
                        tenant_ctx=tenant_ctx,
                        parent_goal_id=agent_state.goal_id,
                        graph_factory=_sub_graph_factory,
                        event_callback=self._event_callback,
                        model=_tree_model,
                        ledger=_tree_ledger,
                    )
                agent_state.sub_goals = sub_goals
                if sub_goals:
                    child_failures = [
                        sub_goal for sub_goal in sub_goals if sub_goal.status is GoalStatus.FAILED
                    ]
                    for sub_goal in sub_goals:
                        agent_state.provenance.extend(sub_goal.provenance)
                    agent_state.context["child_retrieval_traces"] = [
                        trace for sub_goal in sub_goals for trace in sub_goal.retrieval_trace
                    ]
                    # Aggregate sub-goal results as steps so the verifier sees them
                    for sg in sub_goals:
                        step = StepResult(
                            description=sg.description,
                            output=sg.result or sg.error,
                            status=StepStatus.COMPLETE if not sg.error else StepStatus.FAILED,
                        )
                        agent_state.steps.append(step)
                    if child_failures:
                        agent_state.status = GoalStatus.FAILED
                        agent_state.error_message = "Nested retrieval failed"
                        await self._emit(
                            {
                                "type": "nested_goal_failed",
                                "failed_sub_goals": [
                                    sub_goal.sub_goal_id for sub_goal in child_failures
                                ],
                                "provenance": agent_state.provenance,
                                "retrieval_trace": agent_state.context["child_retrieval_traces"],
                            }
                        )
                    return {"agent_state": agent_state}
            except Exception as exc:
                # Fall through to normal execution if goal-tree fails
                await self._emit({"type": "goal_tree_error", "error": str(exc)})

        # Build StructuredPlan for wave-based parallel execution (Fix 1 + Fix 3)
        import asyncio as _asyncio

        from app.agent.structured_plan import StructuredPlan, StructuredStep

        _structured: StructuredPlan | None = None
        for _entry in plan:
            try:
                _parsed = json.loads(_entry)
                if isinstance(_parsed, dict) and "steps" in _parsed:
                    _structured = StructuredPlan.from_llm_response(_entry)
                    break
            except Exception:
                pass

        if _structured is None:
            # Plain string steps — treat as sequential (each depends on the previous)
            _structured = StructuredPlan(
                steps=[
                    StructuredStep(
                        id=f"s{i}", description=sd, depends_on=[f"s{i - 1}"] if i > 0 else []
                    )
                    for i, sd in enumerate(plan)
                ]
            )

        waves = _structured.execution_waves()
        step_global_index = 0
        # P1.1: Track completed StructuredStep objects for condition evaluation
        _completed_steps: dict[str, Any] = {}

        # ── Batch cache prefetch ────────────────────────────────────────────
        # Embed ALL plan step descriptions at once (single embedding API call)
        # then batch-check the cache, so the per-step path needs no embedding
        # call. A prefetched entry is only a CANDIDATE: it is handed to the
        # governed pipeline, which serves it only after every approval,
        # permission, policy and guardrail gate passed for that step (it used
        # to be returned here, before any gate ran).
        _batch_cache_results: dict[str, str] = {}  # step_desc → raw cache entry
        _batch_embeddings: dict[str, list[float]] = {}  # step_desc → embedding
        if self._semantic_cache is not None and self._embedder is not None:
            try:
                from app.providers.base import EmbedRequest as _EmbedReq

                _all_descs = [s.description for w in waves for s in w]
                if _all_descs:
                    _batch_resp = await self._embedder.embed(_EmbedReq(texts=_all_descs))
                    _batch_embs = _batch_resp.embeddings or []
                    for desc, emb in zip(_all_descs, _batch_embs, strict=False):
                        if emb:
                            _batch_embeddings.setdefault(desc, emb)
                    if _batch_embs and hasattr(self._semantic_cache, "get_batch"):
                        _batch_hits = await self._semantic_cache.get_batch(
                            embeddings=_batch_embs,
                            tenant_id=tenant_ctx.tenant_id,
                        )
                        for desc, hit in zip(_all_descs, _batch_hits, strict=False):
                            if hit is not None:
                                cached_resp = hit.response if hasattr(hit, "response") else str(hit)
                                _batch_cache_results.setdefault(desc, cached_resp)
                        if _batch_cache_results:
                            self._logger.info(
                                "batch_cache_prefetch",
                                total=len(_all_descs),
                                candidates=len(_batch_cache_results),
                            )
            except Exception as _bp_exc:
                self._logger.debug("batch_cache_prefetch_skipped", error=str(_bp_exc)[:80])

        # Crash resume: steps a previous (crashed) run of this goal already
        # finished. They are not re-run — their tools may have had side effects.
        _resumed_done: dict[str, str] = agent_state.context.pop(RESUME_COMPLETED_KEY, None) or {}
        _ckpt_done: dict[str, str] = agent_state.context.setdefault(COMPLETED_STEPS_KEY, {})

        for wave_idx, wave in enumerate(waves):
            # Honour an operator pause between steps (never mid-tool-call).
            _pause_gate = getattr(self, "_pause_gate", None)
            if _pause_gate is not None:
                await _pause_gate()
            # P1.1: Filter out steps whose condition evaluates to False
            eligible_steps = [s for s in wave if s.should_execute(_completed_steps)]
            if _resumed_done:
                _already = [s for s in eligible_steps if s.id in _resumed_done]
                for _done in _already:
                    _done.output = _resumed_done[_done.id]
                    _done.status = "complete"
                    _completed_steps[_done.id] = _done
                    await self._emit(
                        {"type": "step_resumed_from_checkpoint", "step": _done.description}
                    )
                if _already:
                    step_global_index += len(_already)
                    eligible_steps = [s for s in eligible_steps if s.id not in _resumed_done]
                    if not eligible_steps:
                        continue
            if not eligible_steps:
                self._logger.info(
                    "wave_all_steps_skipped_by_condition",
                    wave=wave_idx,
                    skipped=[s.id for s in wave],
                )
                continue

            if len(eligible_steps) == 1:
                # Single step — execute normally (with loop support if configured)
                struct_step = eligible_steps[0]
                step_desc = struct_step.description
                step = StepResult(description=step_desc, status=StepStatus.RUNNING)
                agent_state.steps.append(step)
                await self._emit({"type": "step_started", "step": step_desc})

                with self._tracer.start_as_current_span("agentverse.step.execute") as span:
                    span.set_attribute("step.description", step_desc[:200])
                    try:
                        if struct_step.loop_until is not None:
                            # P1.1: Loop execution
                            output = await self._execute_step_with_loop(
                                struct_step, agent_state, tenant_ctx
                            )
                        elif self._semantic_cache is not None:
                            output = await self._execute_step_with_cache(
                                step_desc,
                                agent_state,
                                tenant_ctx,
                                prefetched=_batch_cache_results.get(step_desc),
                                prefetched_embedding=_batch_embeddings.get(step_desc),
                            )
                        else:
                            output = await self._execute_step(step_desc, agent_state, tenant_ctx)
                    except PermissionError as exc:
                        agent_state.status = GoalStatus.FAILED
                        agent_state.error_message = str(exc)
                        step.status = StepStatus.FAILED
                        step.error = str(exc)
                        raise  # re-raise so LangGraph propagates it out of ainvoke
                    except StepNotExecutedError as exc:
                        # The step never ran (e.g. open circuit). Record it as FAILED —
                        # never as a completed step whose "output" is a skip message —
                        # and stop this pass: later steps depend on it.
                        await self._record_step_not_executed(
                            agent_state, step, struct_step, _completed_steps, exc
                        )
                        break

                step.output = output
                step.status = StepStatus.COMPLETE
                # P1.1: Update StructuredStep runtime state for condition evaluation
                struct_step.output = output
                struct_step.status = "complete"
                _completed_steps[struct_step.id] = struct_step
                _ckpt_done[struct_step.id] = output or ""
                await self._emit({"type": "step_complete", "step": step_desc, "output": output})
                # Persist tool outcome for cross-restart trust scores
                try:
                    _orch_persist = (
                        getattr(self._app_state, "orchestration_persistence", None)
                        if self._app_state
                        else None
                    )
                    if _orch_persist is not None:
                        _tool_nm = self._extract_tool_name(step_desc) or step_desc[:50]
                        _step_ok = (
                            output
                            and "error" not in output.lower()[:50]
                            and "failed" not in output.lower()[:50]
                        )
                        _step_lat = float(agent_state.context.get("last_step_latency_ms", 200.0))
                        import asyncio as _tp_asyncio

                        _tp_asyncio.ensure_future(  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled
                            _orch_persist.persist_tool_outcome(
                                tool_name=_tool_nm,
                                success=bool(_step_ok),
                                latency_ms=_step_lat,
                                tenant_id=tenant_ctx.tenant_id,
                            )
                        )
                except Exception:
                    pass
                # Invoke step_callback for streaming simulation support
                if self._step_callback is not None:
                    try:
                        import asyncio as _asyncio_cb

                        _asyncio_cb.create_task(  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled
                            self._step_callback(
                                "step_completed",
                                {
                                    "description": step_desc,
                                    "tool_called": self._extract_tool_name(step_desc),
                                    "output": output[:500] if output else "",
                                    # The goal's running metered LLM spend
                                    # (llm_cost accumulates it); the listener
                                    # derives the per-step increment. The old
                                    # "last_step_cost" key was never set.
                                    "total_cost_usd": (
                                        float(
                                            agent_state.context.get("total_cost_usd", 0.0)
                                            or 0.0
                                        )
                                        if isinstance(agent_state.context, dict)
                                        else 0.0
                                    ),
                                },
                            )
                        )
                    except Exception:
                        pass
                await self._write_checkpoint(
                    agent_state.goal_id, step_global_index, agent_state, tenant_ctx
                )
                step_global_index += 1

            else:
                # Multiple independent steps — execute in parallel via asyncio.gather
                await self._emit(
                    {
                        "type": "steps_parallel_start",
                        "wave": wave_idx,
                        "steps": [s.description for s in eligible_steps],
                        "count": len(eligible_steps),
                    }
                )

                # Pre-create StepResult objects before parallel execution to maintain order
                parallel_steps: list[StepResult] = []
                for s in eligible_steps:
                    sr = StepResult(description=s.description, status=StepStatus.RUNNING)
                    agent_state.steps.append(sr)
                    await self._emit({"type": "step_started", "step": s.description})
                    parallel_steps.append(sr)

                # Lock to protect shared agent_state mutations across concurrent coroutines
                _state_lock = _asyncio.Lock()

                async def _run_wave_step(desc: str, sr: StepResult) -> None:
                    try:
                        if self._semantic_cache is not None:
                            out = await self._execute_step_with_cache(
                                desc,
                                agent_state,
                                tenant_ctx,
                                prefetched=_batch_cache_results.get(desc),
                                prefetched_embedding=_batch_embeddings.get(desc),
                            )
                        else:
                            out = await self._execute_step(desc, agent_state, tenant_ctx)
                        async with _state_lock:  # noqa: B023  # closure runs + is awaited within the same wave iteration that defines _state_lock (gather() below completes before the next wave), so the late-binding this rule warns about never happens here
                            sr.output = out
                            sr.status = StepStatus.COMPLETE
                        await self._emit({"type": "step_complete", "step": desc, "output": out})
                        # H4: Persist tool outcome for parallel wave steps
                        try:
                            _orch_persist_wave = (
                                getattr(self._app_state, "orchestration_persistence", None)
                                if self._app_state
                                else None
                            )
                            if _orch_persist_wave is not None:
                                _tool_nm_wave = self._extract_tool_name(desc) or desc[:50]
                                _step_ok_wave = bool(out and "error" not in out.lower()[:50])
                                import asyncio as _wp_asyncio

                                _wp_asyncio.ensure_future(  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled
                                    _orch_persist_wave.persist_tool_outcome(
                                        tool_name=_tool_nm_wave,
                                        success=_step_ok_wave,
                                        latency_ms=200.0,
                                        tenant_id=tenant_ctx.tenant_id,
                                    )
                                )
                        except Exception:
                            pass
                    except PermissionError as exc:
                        async with _state_lock:  # noqa: B023  # closure runs + is awaited within the same wave iteration that defines _state_lock (gather() below completes before the next wave), so the late-binding this rule warns about never happens here
                            agent_state.status = GoalStatus.FAILED
                            agent_state.error_message = str(exc)
                            sr.status = StepStatus.FAILED
                            sr.error = str(exc)
                        raise
                    except StepNotExecutedError as exc:
                        # Not executed (e.g. open circuit): FAILED, not a fake output.
                        # Siblings in the wave still finish; later waves are skipped.
                        async with _state_lock:  # noqa: B023  # see note above
                            await self._record_step_not_executed(
                                agent_state, sr, None, None, exc
                            )
                    except Exception as exc:
                        async with _state_lock:  # noqa: B023  # closure runs + is awaited within the same wave iteration that defines _state_lock (gather() below completes before the next wave), so the late-binding this rule warns about never happens here
                            sr.status = StepStatus.FAILED
                            sr.error = str(exc)
                        raise

                tasks = [
                    _asyncio.create_task(
                        _run_wave_step(eligible_steps[i].description, parallel_steps[i])
                    )
                    for i in range(len(eligible_steps))
                ]
                try:
                    await _asyncio.gather(*tasks)
                except (PermissionError, Exception):
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await _asyncio.gather(*tasks, return_exceptions=True)
                    raise

                # P1.1: Update StructuredStep runtime state for parallel steps
                for i, struct_step_par in enumerate(eligible_steps):
                    struct_step_par.output = parallel_steps[i].output
                    struct_step_par.status = (
                        "complete" if parallel_steps[i].status == StepStatus.COMPLETE else "failed"
                    )
                    _completed_steps[struct_step_par.id] = struct_step_par
                    if parallel_steps[i].status == StepStatus.COMPLETE:
                        _ckpt_done[struct_step_par.id] = parallel_steps[i].output or ""

                for i in range(len(eligible_steps)):
                    await self._write_checkpoint(
                        agent_state.goal_id, step_global_index + i, agent_state, tenant_ctx
                    )
                step_global_index += len(eligible_steps)

                await self._emit(
                    {
                        "type": "steps_parallel_complete",
                        "wave": wave_idx,
                        "count": len(eligible_steps),
                    }
                )
                if any(p.status == StepStatus.FAILED for p in parallel_steps):
                    break  # a step did not execute — later waves depend on it

        return {"agent_state": agent_state}

    async def _record_step_not_executed(
        self,
        agent_state: AgentState,
        step: StepResult,
        struct_step: Any,
        completed_steps: dict[str, Any] | None,
        exc: StepNotExecutedError,
    ) -> None:
        """Mark a step that never ran as FAILED with its reason (no fake output)."""
        reason = str(exc)
        step.status = StepStatus.FAILED
        step.error = reason
        step.output = ""
        if struct_step is not None:
            struct_step.status = "failed"
            if completed_steps is not None:
                completed_steps[struct_step.id] = struct_step
        agent_state.context.setdefault(STEP_FAILURES_KEY, []).append(
            {"step": step.description, "reason": reason}
        )
        await self._emit({"type": "step_failed", "step": step.description, "error": reason})

    async def _execute_step_with_loop(
        self,
        step: Any,
        agent_state: AgentState,
        tenant_ctx: TenantContext,
    ) -> str:
        """Execute a step with loop-until support (P1.1).

        Calls ``_execute_step`` repeatedly until ``loop_until`` evaluates to True
        or ``max_loop_iter`` is exceeded. Uses exponential backoff between iterations.
        """
        for iteration in range(step.max_loop_iter):
            step.iterations_used = iteration + 1
            output = await self._execute_step(step.description, agent_state, tenant_ctx)
            step.output = output

            try:
                from app.agent.structured_plan import _safe_eval_condition as _loop_eval

                done = _loop_eval(
                    step.loop_until,
                    {"output": output, "iteration": iteration + 1, "iterations": iteration + 1},
                )
            except Exception:
                done = True  # On eval error, exit loop

            if done:
                self._logger.info(
                    "loop_step_completed",
                    step_id=step.id,
                    iterations=step.iterations_used,
                )
                return output

            if iteration < step.max_loop_iter - 1:
                delay = min(2**iteration, 30)  # exponential backoff, max 30s
                self._logger.info(
                    "loop_step_retry",
                    step_id=step.id,
                    iteration=iteration + 1,
                    next_delay_s=delay,
                )
                await asyncio.sleep(delay)

        # Max iterations reached
        self._logger.warning(
            "loop_step_max_iterations_reached",
            step_id=step.id,
            max=step.max_loop_iter,
        )
        return step.output  # Return last output

    @staticmethod
    def _dedup_hash(step: str, state: AgentState) -> str:
        # Scoped to the goal run: identical text in a *different* goal is not a dup.
        return hashlib.sha256(f"{state.goal_id}:{step}:{state.goal}".encode()).hexdigest()

    def _dedup_lookup(self, step: str, state: AgentState, tenant_ctx: TenantContext) -> str | None:
        """Return the real recorded output of an already-executed identical step, or None."""
        cache = self._dedup_cache
        if cache is None:
            return None
        content_hash = self._dedup_hash(step, state)
        if not cache.is_duplicate(content_hash=content_hash, tenant_ctx=tenant_ctx):
            cache.mark_seen(content_hash=content_hash, tenant_ctx=tenant_ctx)
            return None
        get_result = getattr(cache, "get_result", None)
        cached = (
            get_result(content_hash=content_hash, tenant_ctx=tenant_ctx)
            if callable(get_result)
            else None
        )
        if isinstance(cached, str) and not _is_uncacheable_output(cached):
            return cached
        return None  # seen, but nothing real cached → re-execute

    def _dedup_store(
        self, step: str, state: AgentState, tenant_ctx: TenantContext, output: str
    ) -> None:
        cache = self._dedup_cache
        store = getattr(cache, "store_result", None) if cache is not None else None
        if (
            callable(store)
            and not _is_uncacheable_output(output)
            and not output.startswith(_DEDUP_NON_RESULT_PREFIXES)
        ):
            with contextlib.suppress(Exception):
                store(
                    content_hash=self._dedup_hash(step, state),
                    output=output,
                    tenant_ctx=tenant_ctx,
                )

    def _approval_unawaitable_error(self, step: str, reason: str) -> PermissionError | None:
        """Return the denial for an approval-required step nobody can approve, else None.

        CORE-01: only a supervised run with a wired HITL gateway waits for an
        approval decision. Everywhere else the gates used to file an approval
        request and then run the step anyway (or deny it and leave the request
        pending forever) — approving or rejecting it changed nothing. An
        approval-required step is therefore denied here, before any request is
        filed, with an error that says how to get it approved.
        """
        label = step if len(step) <= 120 else step[:117] + "..."
        if self._autonomy_mode != "supervised":
            return PermissionError(
                f"Step '{label}' requires human approval ({reason}), but the goal runs in "
                f"'{self._autonomy_mode}' mode where no approval is awaited; the step was "
                "not executed. Run the goal in supervised mode to approve it."
            )
        if self._hitl_gateway is None:
            return PermissionError(
                f"Step '{label}' requires human approval ({reason}), but no approval "
                "gateway is configured; the step was not executed."
            )
        return None

    async def _file_approval_request(
        self, *, goal_id: str, action: str, risk_level: str, tenant_ctx: TenantContext
    ) -> str:
        """File an approval request that is durable before anyone waits on it.

        CORE-02: the sync ``request_approval`` persisted the row fire-and-forget,
        so when the write failed the request lived in one process's memory — the
        approver's replica never saw it and the goal waited the full timeout.
        A gateway that offers ``request_approval_async`` awaits the write; if it
        cannot be made durable the step fails now, closed, with a clear error.
        """
        gateway = self._hitl_gateway
        if gateway is None:  # callers check first; never wait on nothing
            raise PermissionError("No approval gateway is configured; the step was not executed.")
        from app.agent.hitl_filing import file_persisted_approval

        try:
            return await file_persisted_approval(
                gateway,
                goal_id=goal_id,
                action=action,
                risk_level=risk_level,
                tenant_ctx=tenant_ctx,
            )
        except HITLDeliveryError as exc:
            raise PermissionError(
                f"Approval request for '{action[:120]}' could not be persisted, so no "
                f"approver can see it; the step was not executed ({exc})."
            ) from exc

    # ── OI-1: goal-scoped ledger of executed calls and approval decisions ──────

    @staticmethod
    def _tool_idempotency_scope(state: AgentState, tool_ref: Any, arguments: Any) -> Any:
        """MCP idempotency scope for one tool dispatch of this goal (a06-F101-04).

        A side-effecting call carries ``goal:<goal_id>:<call fingerprint>`` (the
        workflow tool step's WF-14 mechanism), so the call that was in flight
        when a worker crashed is not applied twice when the redelivered goal
        re-issues it. Read calls carry no key.
        """
        from app.agent.goal_action_ledger import call_fingerprint, call_idempotency_key
        from app.mcp.client import idempotency_scope

        server_id = str(getattr(tool_ref, "server_id", "") or "")
        name = str(getattr(tool_ref, "name", "") or "")
        args = arguments if isinstance(arguments, dict) else {}
        key: str | None = None
        if classify_tool_risk(name, str(getattr(tool_ref, "server_name", "") or ""), args) != (
            "read"
        ):
            key = call_idempotency_key(
                _action_scope_id(state), call_fingerprint(server_id, name, args)
            )
        return idempotency_scope(key)

    def _goal_action_ledger(self, state: AgentState, tenant_ctx: TenantContext) -> Any:
        """The goal's action ledger: state context mirror + the shared Redis hash."""
        from app.agent.goal_action_ledger import GoalActionLedger

        app_state = getattr(self._app_state, "state", self._app_state)
        redis = getattr(app_state, "_redis", None) if app_state is not None else None
        if redis is None:
            redis = getattr(self._hitl_gateway, "_redis", None)
        return GoalActionLedger(
            state.context,
            tenant_id=getattr(tenant_ctx, "tenant_id", "") or "",
            goal_id=_action_scope_id(state),
            redis=redis,
        )

    async def _reuse_approval(
        self,
        state: AgentState,
        tenant_ctx: TenantContext,
        key: str,
        *,
        action: str,
        scope: str,
    ) -> bool:
        """True when this exact action was already APPROVED earlier in this goal.

        Only explicit approvals are recorded (never a rejection or a timeout), and
        only in supervised mode, where approvals are awaited at all.
        """
        if self._autonomy_mode != "supervised" or self._hitl_gateway is None:
            return False
        entry = await self._goal_action_ledger(state, tenant_ctx).approval(key)
        if entry is None:
            return False
        await self._emit(
            {
                "type": "approval_reused",
                "request_id": str(entry.get("request_id") or ""),
                "action": action[:300],
                "scope": scope,
            }
        )
        return True

    async def _remember_approval(
        self,
        state: AgentState,
        tenant_ctx: TenantContext,
        key: str,
        *,
        request_id: str,
        action: str,
    ) -> None:
        await self._goal_action_ledger(state, tenant_ctx).record_approval(
            key, request_id=request_id, action=action
        )

    async def _record_executed_call(
        self,
        state: AgentState,
        tenant_ctx: TenantContext,
        fingerprint: str,
        *,
        tool_ref: Any,
        arguments: dict[str, Any] | None,
        output: Any,
    ) -> None:
        """Record a succeeded side-effecting call so it is never dispatched again."""
        ok = await self._goal_action_ledger(state, tenant_ctx).record_executed(
            fingerprint,
            step_id=state.steps[-1].step_id if state.steps else "",
            tool=str(getattr(tool_ref, "name", "") or ""),
            server_id=str(getattr(tool_ref, "server_id", "") or ""),
            arguments=arguments,
            output=self._sanitize_tool_raw_output(output),
        )
        if not ok:
            await self._emit(
                {
                    "type": "action_ledger_degraded",
                    "tool": str(getattr(tool_ref, "name", "") or ""),
                    "reason": "the executed call could not be shared with other replicas",
                }
            )

    async def _execute_step(self, step: str, state: AgentState, tenant_ctx: TenantContext) -> str:
        """Run the governed per-step pipeline and record its real output for dedup.

        GOAL-STALL: the pipeline runs under the step watchdog — ``step_heartbeat``
        events while it runs, and a deadline on its active time (approval waits
        excluded) after which it is cancelled and raises
        ``StepDeadlineExceededError`` (recorded as a FAILED step, then replanned).
        """
        output = await run_step_with_deadline(
            step,
            lambda: self._execute_step_pipeline(step, state, tenant_ctx),
            emit=self._emit,
            timeout_s=getattr(self, "_step_timeout_s", None),
            heartbeat_s=getattr(self, "_step_heartbeat_s", None),
        )
        self._dedup_store(step, state, tenant_ctx, output)
        return output

    async def _await_approval_decision(
        self, request_id: str, *, tenant_ctx: TenantContext, timeout: Any = _NO_TIMEOUT_ARG
    ) -> Any:
        """Block on a human decision; the wait does not count against the step deadline."""
        gateway = self._hitl_gateway
        if gateway is None:  # callers check first; never wait on nothing
            raise PermissionError("No approval gateway is configured; the step was not executed.")
        with approval_wait():
            if timeout is _NO_TIMEOUT_ARG:
                return await gateway.wait_for_approval(request_id, tenant_ctx=tenant_ctx)
            return await gateway.wait_for_approval(
                request_id, tenant_ctx=tenant_ctx, timeout=timeout
            )

    async def _audit_grant_denial(
        self, tool_name: str, reason: str, state: AgentState, tenant_ctx: TenantContext
    ) -> None:
        """P8-2: a durable audit row for every call the grant gate refused.

        (tool, agent, tenant, reason): the tenant is the row's tenant, the agent
        and the reason are in the note. A requested name too long for the
        column is recorded as an explicit prefix + digest, with the whole name
        in the note — never silently cut. A failed write is logged as an error;
        the call stays refused either way.
        """
        import hashlib

        if self._audit_log is None:
            self._logger.warning("grant_denial_unaudited_no_audit_log", tool=tool_name[:120])
            return
        recorded_name = tool_name
        if len(tool_name) > 200:
            digest = hashlib.sha256(tool_name.encode()).hexdigest()[:16]
            recorded_name = f"{tool_name[:150]}...sha256:{digest}"
        try:
            await self._audit_log.record_async(
                AuditEvent(
                    goal_id=str(state.goal_id or ""),
                    tool_name=recorded_name,
                    action_level=ActionLevel.DENY,
                    outcome="denied",
                    note=(
                        f"grant_gate agent={self._agent_id or ''} reason={reason} "
                        f"tool={tool_name}"
                    )[:1000],
                    api_key_id=getattr(tenant_ctx, "api_key_id", None) or None,
                ),
                tenant_ctx=tenant_ctx,
            )
        except Exception as exc:
            self._logger.error(
                "grant_denial_audit_failed", tool=tool_name[:120], error=str(exc)[:200]
            )

    async def _deny_ungranted_call(
        self,
        tool_name: str,
        state: AgentState,
        tenant_ctx: TenantContext,
        *,
        parallel: bool = False,
    ) -> str | None:
        """The grant gate for a call the model was never offered (P8-2).

        Ungranted tools are hidden from the planner and executor prompts, so a
        call to one can only be a hallucinated or injected name. It used to be
        rejected as an "unknown tool" before the grant gate ran: no
        ``tool_call_blocked_by_grant`` event and no audit row. With grants
        enforced, it now goes through the gate: a refusal returns the refusal
        reason (event + audit row written); ``None`` means the gate allows it.
        """
        if not getattr(self, "_enforce_grants", False):
            return None
        decision = await enforce_tool_call(
            self._grant_store,
            tenant_id=tenant_ctx.tenant_id,
            agent_id=self._agent_id or "",
            tool_name=tool_name,
            enabled=True,
        )
        if decision.allowed:
            return None
        reason = str(decision.reason)
        event: dict[str, Any] = {
            "type": "tool_call_blocked_by_grant",
            "tool": tool_name[:200],
            "reason": reason,
            "offered": False,
        }
        if parallel:
            event["parallel"] = True
        await self._emit(event)
        record_tool_call(tool_name[:200], "grant", "denied", 0.0)
        await self._audit_grant_denial(tool_name, reason, state, tenant_ctx)
        return reason

    async def _screen_step_output(
        self, step: str, output: str, state: AgentState, tenant_ctx: TenantContext
    ) -> str:
        """Apply the tenant's ``tool_output`` guardrail rules to a step's output.

        BLOCK withholds the output, REDACT replaces it with the redacted text;
        both emit an event naming the rules. An errored check fails closed on
        high-risk work (SAFE-4), else the output passes with a warning.
        """
        if guardrails_engine is None or GuardrailLayer is None:
            return output
        try:
            verdict = await guardrails_engine.evaluate(
                content=output,
                layer=GuardrailLayer.TOOL_OUTPUT,
                tenant_id=tenant_ctx.tenant_id,
                goal_id=getattr(state, "goal_id", None),
                step_description=step,
            )
        except Exception as exc:
            if _guardrail_should_fail_closed(step, state.context.get("_risk_level")):
                self._logger.warning("step_output_guardrail_failed_closed", error=str(exc)[:200])
                return "[Output withheld: the guardrail check could not be completed]"
            self._logger.warning("step_output_guardrail_failed", error=str(exc)[:200])
            return output
        rules = [str(v.get("rule_name") or "") for v in verdict.get("violations") or []]
        if verdict.get("blocked"):
            await self._emit(
                {"type": "guardrail_blocked", "scope": "step_output", "rules": rules}
            )
            return "[Output blocked by guardrail policy]"
        redacted = verdict.get("redacted_content")
        if isinstance(redacted, str) and redacted != output:
            await self._emit({"type": "pii_redacted", "issues": rules, "scope": "step_output"})
            return redacted
        return output

    async def _execute_step_pipeline(
        self, step: str, state: AgentState, tenant_ctx: TenantContext
    ) -> str:
        """Run the canonical governed per-step execution pipeline."""
        tool_name = self._extract_tool_name(step)

        # Guardrail check: step (Guardrails 2.0). STEP was declared in
        # GuardrailLayer but never actually checked anywhere before this fix
        # (only GOAL/TOOL_ARGS shared the baseline injection rule's *layer
        # list* — nothing ever evaluated against GuardrailLayer.STEP itself),
        # so HIPAA's "PHI anywhere" rule and the baseline injection rule's
        # STEP entry had zero real effect. Gate the step description itself
        # before any safety-profile / HITL / tool-dispatch work begins,
        # mirroring the tool_args block further down in this same function.
        if _GUARDRAILS_AVAILABLE and guardrails_engine is not None:
            try:
                guardrails_engine.ensure_default_rules(tenant_ctx.tenant_id)
                _g2_step_result = await guardrails_engine.evaluate(
                    content=step[:2000],
                    layer=GuardrailLayer.STEP,
                    tenant_id=tenant_ctx.tenant_id,
                    goal_id=getattr(state, "goal_id", None),
                    step_description=step,
                )
                if _g2_step_result.get("blocked"):
                    _g2_step_viol = (_g2_step_result.get("violations") or [{}])[0].get(
                        "rule_name", "policy"
                    )
                    raise PermissionError(f"Step blocked by guardrail: {_g2_step_viol}")
            except PermissionError:
                raise
            except Exception as _g2_step_exc:
                # SAFE-4 (P0-15): fail closed on high-risk work when the
                # guardrail engine errors instead of silently allowing.
                if _guardrail_should_fail_closed(step, state.context.get("_risk_level")):
                    raise PermissionError(
                        f"Guardrail (step) errored on high-risk step '{step[:60]}'; "
                        "failing closed."
                    ) from _g2_step_exc

        # H23-H26: Action safety profile — assess per-tool risk
        _asp_hitl_required = False
        _asp_reason = ""
        try:
            from app.security_runtime.action_safety_profile import (
                ActionSafetyLevel,
                ActionSafetyProfileSelector,
            )

            _asp_selector = ActionSafetyProfileSelector()
            _risk = state.context.get("_risk_level", "low")
            _asp = _asp_selector.select(
                tool_name=tool_name,
                tool_args={},
                risk_level=str(_risk),
            )
            if _asp.safety_level.value == ActionSafetyLevel.BLOCKED.value:
                return f"Action blocked by safety profile: {_asp.reason}"
            if _asp.safety_level.value == ActionSafetyLevel.HITL_REQUIRED.value:
                _asp_hitl_required = True
                _asp_reason = _asp.reason
        except Exception as _asp_exc:
            # Fail closed: an action-safety assessment that cannot be made must
            # not let the step run unassessed (this used to be ``except: pass``).
            raise StepNotExecutedError(
                f"Action-safety assessment failed for '{tool_name or 'llm'}' "
                f"({type(_asp_exc).__name__}); step was not executed (fail-closed)."
            ) from _asp_exc

        # SAFE-3 (P0-14): route an action-safety HITL_REQUIRED verdict through the
        # HITL gateway. This is intentionally OUTSIDE the try/except above so an
        # approval rejection/timeout can never be swallowed and silently allowed.
        # True only once a human explicitly APPROVED this step (supervised wait).
        _step_approved = False
        # OI-1: an approval of this exact step text earlier in the goal is reused
        # (a replan / retry of the same step must not ask a human again).
        from app.agent.goal_action_ledger import step_approval_key

        _step_key = step_approval_key(step)
        if _asp_hitl_required:
            _asp_denial = self._approval_unawaitable_error(step, f"action-safety: {_asp_reason}")
            if _asp_denial is not None or self._hitl_gateway is None:
                record_tool_call(tool_name, "policy", "approval_required", 0.0)
                raise _asp_denial or PermissionError(f"Step '{step}' requires approval.")
            # OI-1: this exact step was already approved in this goal.
            _step_approved = await self._reuse_approval(
                state, tenant_ctx, _step_key, action=step, scope="step"
            )
        if _asp_hitl_required and not _step_approved:
            # Supervised with a gateway: block until a human decides.
            req_id = await self._file_approval_request(
                goal_id=state.goal_id,
                action=step,
                risk_level="high",
                tenant_ctx=tenant_ctx,
            )
            await self._emit({"type": "waiting_approval", "request_id": req_id, "action": step})
            approval_started = time.monotonic()
            final_status = await self._await_approval_decision(
                req_id, tenant_ctx=tenant_ctx, timeout=self._hitl_timeout
            )
            record_approval_wait(time.monotonic() - approval_started)
            if final_status == ApprovalStatus.REJECTED:
                raise PermissionError(
                    f"Step '{step}' rejected by human approver (action-safety: {_asp_reason})."
                )
            if final_status == ApprovalStatus.TIMED_OUT:
                raise PermissionError(
                    f"Step '{step}' approval timed out (action-safety: {_asp_reason})."
                )
            if final_status != ApprovalStatus.APPROVED:
                # Only an explicit approval lets the step run (a still-pending
                # status used to fall through and execute).
                raise PermissionError(
                    f"Step '{step}' approval not granted ({final_status}) "
                    f"(action-safety: {_asp_reason})."
                )
            _step_approved = True
            await self._remember_approval(
                state, tenant_ctx, _step_key, request_id=req_id, action=step
            )
            await self._emit({"type": "approval_granted", "request_id": req_id})

        # 1. Cost check deferred — actual cost calculated after LLM call below.

        # 2. Exec memory recall — already done in rag_retrieval; skip here.

        # 3. Dedup — moved below the governance gates (see "8-pre. Dedup"): a
        # duplicate used to return the literal "Duplicate step, returning cached
        # result." with no cache behind it, and did so before permission/policy/HITL.

        # 3b. Smart context fetch (per-step RAG)
        app_state = getattr(self._app_state, "state", self._app_state)
        step_strategy = RAGStrategy(
            str(state.context.get("retrieval_strategy", RAGStrategy.HYBRID.value))
        )
        step_context = await smart_context_fetch(
            goal=state.goal,
            step=step,
            tenant_ctx=tenant_ctx,
            retrieval_gateway=(
                self._retrieval_gateway or getattr(app_state, "retrieval_gateway", None)
            ),
            collection_ids=list(self._agent_collection_ids),
            strategy=step_strategy,
            top_k=int(state.context.get("retrieval_top_k", 3)),
            filters=state.context.get("retrieval_filters", {}),
            execution_id=state.goal_id,
        )

        # 4. Circuit breakers (a08-F198-01). Every applicable breaker gates the
        # step: the LLM provider's ("llm", which also records this step's LLM
        # outcome) AND one registered for the step's tool — ``get("llm") or
        # get(tool)`` skipped the tool's breaker whenever an LLM breaker existed.
        # The async API is used: a RedisCircuitBreaker's sync methods only read
        # its per-instance in-memory fallback, so a provider outage observed on
        # one replica / worker never stopped the others. (MCP connector calls
        # are additionally guarded per connector by the MCP client itself.)
        _active_breaker: CircuitBreaker | None = None
        if self._circuit_breakers:
            _llm_breaker = self._circuit_breakers.get("llm")
            _tool_breaker = (
                self._circuit_breakers.get(tool_name)
                if tool_name and tool_name != "llm"
                else None
            )
            for _name, _breaker in (("llm", _llm_breaker), (tool_name, _tool_breaker)):
                if _breaker is None:
                    continue
                if not await _breaker.can_call_async():
                    # Fail the step honestly — returning a skip message here made it
                    # the step's "output" and the step was marked COMPLETE.
                    raise StepNotExecutedError(
                        f"Circuit breaker open for '{_name or 'llm'}': step was not executed."
                    )
            _active_breaker = _llm_breaker  # records the LLM call's outcome

        # 5. Governance — permission check with scope extraction
        if self._permission_matrix is not None:
            scope_value = _extract_scope_value(step)
            level = self._permission_matrix.check(
                tool_name=tool_name,
                tenant_ctx=tenant_ctx,
                scope_value=scope_value,
            )
            if level == ActionLevel.DENY:
                record_tool_call(tool_name, "policy", "denied", 0.0)
                raise PermissionError(
                    f"Tool '{tool_name}' denied by governance policy "
                    f"for tenant '{tenant_ctx.tenant_id}'."
                )

        # 6. Guardrails — validate step text for injection, then tool name
        # 6a. Check the plan STEP TEXT for injection phrases (e.g. "ignore all previous instructions")  # noqa: E501
        # This is important: a compromised tool could return an injection-crafted step description.
        if self._guardrail_checker is not None:
            step_issues = self._guardrail_checker.check_goal(step)
            if step_issues:
                return f"Guardrail blocked step: {'; '.join(step_issues)}"

        # 6b. Check tool name (only check the name; do NOT pass the step description as tool_args
        # since it triggers false positives on benign words like "extract", "format").
        if self._guardrail_checker is not None:
            violations = self._guardrail_checker.check(
                tool_name=tool_name,
                tool_args={},
            )
            if violations:
                return f"Guardrail blocked step: {'; '.join(violations)}"

        # 6c. Profile-based GuardrailEnforcer (dynamic bundle selection from Part 11/13)
        from app.core.runtime_flags import get_runtime_flags as _ge_rtf

        _ge_flags = _ge_rtf()
        _runtime_profile = state.context.get("_runtime_profile")
        if (
            _ge_flags.dynamic_orchestration or _ge_flags.enable_guardrail_profile
        ) and _runtime_profile is not None:
            try:
                from app.security_runtime.guardrail_enforcer import GuardrailEnforcer

                _ge = GuardrailEnforcer()
                _ge_result = await _ge.check_tool_args(
                    tool_name=tool_name,
                    tool_args={},  # C2 fix: tool_args not defined at pre-LLM check stage
                    profile=_runtime_profile,
                )
            except Exception as _ge_exc:
                # Fail closed: the enabled profile guardrail could not evaluate the
                # tool, so the step must not run unchecked (was: ``except: pass``).
                raise StepNotExecutedError(
                    f"Profile guardrail check failed for '{tool_name or 'llm'}' "
                    f"({type(_ge_exc).__name__}); step was not executed (fail-closed)."
                ) from _ge_exc
            if _ge_result.blocked:
                return f"GuardrailEnforcer blocked tool '{tool_name}': {_ge_result.reason}"

        # N6b: guardrail_profile_selected SSE — only when dynamic orchestration profile present
        if self._event_callback is not None and _runtime_profile is not None:
            try:
                from app.observability.runtime_decision_trace import RuntimeSSEEmitter

                _sse_gps = RuntimeSSEEmitter()
                _bundle = (
                    getattr(
                        getattr(_runtime_profile, "security", None), "guardrail_bundle", "default"
                    )
                    or "default"
                )
                await self._emit(
                    _sse_gps.guardrail_profile_selected(
                        goal_id=state.goal_id,
                        bundle=_bundle,
                        scanners=["injection", "pii", "tool_args"],
                    )
                )
            except Exception:
                pass

        # 6b. Policy engine check (glob-based policies). This sees the tool name
        # guessed from the step text; the tool the model actually calls is
        # checked again at dispatch (``_tool_policy_gate``) unless it is this one.
        _step_policy_tool = tool_name
        if self._policy_engine is not None:
            policy_result = self._policy_engine.evaluate(tool_name=tool_name, tenant_ctx=tenant_ctx)
            if policy_result == PolicyResult.DENY:
                record_tool_call(tool_name, "policy", "denied", 0.0)
                raise PermissionError(
                    f"Tool '{tool_name}' denied by governance policy "
                    f"for tenant '{tenant_ctx.tenant_id}'."
                )
            elif policy_result == PolicyResult.REQUIRE_APPROVAL and not _step_approved:
                # A tenant policy demands approval. Only a supervised run with a
                # gateway waits for it; anywhere else the step is denied before a
                # request is filed (one used to be filed and left pending forever).
                _policy_denial = self._approval_unawaitable_error(
                    step, f"tenant policy on tool '{tool_name}'"
                )
                if _policy_denial is not None or self._hitl_gateway is None:
                    record_tool_call(tool_name, "policy", "approval_required", 0.0)
                    raise _policy_denial or PermissionError(
                        f"Tool '{tool_name}' requires approval by policy."
                    )
                _step_approved = await self._reuse_approval(
                    state, tenant_ctx, _step_key, action=step, scope="step"
                )
            if policy_result == PolicyResult.REQUIRE_APPROVAL and not _step_approved:
                req_id = await self._file_approval_request(
                    goal_id=state.goal_id,
                    action=step,
                    risk_level="high",
                    tenant_ctx=tenant_ctx,
                )
                await self._emit({"type": "waiting_approval", "request_id": req_id, "action": step})
                approval_started = time.monotonic()
                final_status = await self._await_approval_decision(
                    req_id, tenant_ctx=tenant_ctx, timeout=self._hitl_timeout
                )
                record_approval_wait(time.monotonic() - approval_started)
                if final_status == ApprovalStatus.REJECTED:
                    raise PermissionError(
                        f"Step '{step}' was rejected by human approver via policy."
                    )
                # Only an explicit APPROVED lets the step run: a timed-out (or
                # still-pending) policy approval used to fall through and execute.
                if final_status != ApprovalStatus.APPROVED:
                    raise PermissionError(
                        f"Step '{step}' policy approval not granted ({final_status})."
                    )
                _step_approved = True
                await self._remember_approval(
                    state, tenant_ctx, _step_key, request_id=req_id, action=step
                )
                await self._emit({"type": "approval_granted", "request_id": req_id})

        # 7. HITL gate — a high-risk step needs an explicit approval unless one was
        # already granted for this step above. It used to be skipped when no gateway
        # was wired, and outside supervised mode it filed a request and ran the step
        # anyway. Risk is classified on the step's normalised verbs and targets, the
        # GOAL's intent and the targeted tool's risk metadata — not on the surface
        # words a planner happened to pick ("Remove" for "delete", RW-20).
        _gate7_risk = (
            None
            if _step_approved
            else assess_step_risk(step, goal=state.goal, tool_name=tool_name)
        )
        if _gate7_risk is not None and _gate7_risk.high_risk:
            _gate7_reason = f"high-risk step: {_gate7_risk.summary()}"[:300]
            _gate7_denial = self._approval_unawaitable_error(step, _gate7_reason)
            if _gate7_denial is not None or self._hitl_gateway is None:
                record_tool_call(tool_name, "policy", "approval_required", 0.0)
                raise _gate7_denial or PermissionError(f"Step '{step}' requires approval.")
            _step_approved = await self._reuse_approval(
                state, tenant_ctx, _step_key, action=step, scope="step"
            )
        if _gate7_risk is not None and _gate7_risk.high_risk and not _step_approved:
            req_id = await self._file_approval_request(
                goal_id=state.goal_id,
                action=step,
                risk_level="high",
                tenant_ctx=tenant_ctx,
            )
            # Actually BLOCK until a human approves or rejects
            await self._emit(
                {
                    "type": "waiting_approval",
                    "request_id": req_id,
                    "action": step,
                    "risk_reasons": list(_gate7_risk.reasons),
                }
            )
            approval_started = time.monotonic()
            final_status = await self._await_approval_decision(
                req_id, tenant_ctx=tenant_ctx
            )
            record_approval_wait(time.monotonic() - approval_started)
            if final_status == ApprovalStatus.REJECTED:
                raise PermissionError(f"Step '{step}' was rejected by human approver.")
            elif final_status != ApprovalStatus.APPROVED:
                raise PermissionError(f"Step '{step}' approval timed out.")
            _step_approved = True
            await self._remember_approval(
                state, tenant_ctx, _step_key, request_id=req_id, action=step
            )
            await self._emit({"type": "approval_granted", "request_id": req_id})

        # 8-pre. Dedup — AFTER every governance gate above, so a duplicate is never
        # a governance bypass. A hit serves the step's REAL recorded output; a hash
        # that was seen but has no stored output is re-executed (never a fake
        # "Duplicate step" placeholder presented as the result).
        _cached_dup = self._dedup_lookup(step, state, tenant_ctx)
        if _cached_dup is not None:
            await self._emit({"type": "dedup_hit", "step": step})
            return _cached_dup

        # 8-pre-b. Semantic cache — likewise only AFTER every step-level gate, and
        # re-authorised against the tool-level gates of the tools behind the hit.
        _cached_answer = await self._serve_governed_cache_hit(
            step, state, tenant_ctx, step_approved=_step_approved
        )
        if _cached_answer is not None:
            return _cached_answer

        # 8. Execute via LLM executor
        recent_outputs = "\n".join(
            (s.output or "")[:_EXECUTOR_CONTEXT_MAX_LENGTH] for s in state.steps[-3:] if s.output
        )
        context_parts = []
        if recent_outputs:
            context_parts.append(f"Recent outputs:\n{recent_outputs}")
        if step_context:
            context_parts.append(f"Relevant knowledge:\n{step_context}")

        # Working memory (T1.2): bounded, salience-ranked recall across the WHOLE
        # run (not just the last 3 steps covered by ``Recent outputs``). Volatile —
        # lives in the checkpointed state, never the durable memory store.
        # Best-effort context: a failure here must never fail the step.
        try:
            from app.agent.working_memory_wiring import (
                sync_working_memory,
                working_memory_block,
            )

            sync_working_memory(state.context, state.steps)
            _wm_block = working_memory_block(state.context, focus=f"{state.goal}\n{step}")
            if _wm_block:
                context_parts.append(f"[Working memory]\n{_wm_block}")
        except Exception:
            pass

        # Entity/knowledge-graph memory (T2.1): extract entities observed in prior
        # step outputs into the tenant knowledge graph so later plans can recall
        # what we already learned. Recall itself is wired in PlannerMixin via
        # KnowledgeGraphFactsSource; here we close the population gap. Best-effort.
        try:
            from app.agent.entity_memory_wiring import arecord_entities_from_steps

            await arecord_entities_from_steps(
                self._knowledge_graph_store,
                tenant_ctx.tenant_id,
                state.steps,
                source_id=state.goal_id,
            )
        except Exception:
            pass

        # ── Search directive parsing ───────────────────────────────────────
        try:
            from app.rag.agentic.search_directive_parser import SearchDirectiveParser

            _directive_parser = SearchDirectiveParser()
            _directives = _directive_parser.extract(step)
            if _directives and self._agent_collection_ids:
                from app.rag.agentic.retriever_tool import RetrieverTool

                _retriever = RetrieverTool(
                    retrieval_gateway=(
                        self._retrieval_gateway or getattr(app_state, "retrieval_gateway", None)
                    )
                )
                _directive_contexts: list[str] = []
                for _directive in _directives:
                    _strategy = {
                        "kb": RAGStrategy.HYBRID,
                        "graph": RAGStrategy.GRAPH,
                        "web": RAGStrategy.WEB_AUGMENTED,
                    }.get(_directive.source_type)
                    if _strategy is None:
                        raise ValueError("Unsupported search directive source")
                    _retrieval = await _retriever.retrieve(
                        query=_directive.query,
                        tenant_ctx=tenant_ctx,
                        strategy=_strategy,
                        collection_ids=list(self._agent_collection_ids),
                        top_k=3,
                        execution_id=state.goal_id,
                    )
                    if _retrieval.chunks:
                        _directive_contexts.append(
                            f"[{_directive.source_type.upper()} SEARCH: {_directive.query}]\n"
                            + _retrieval.context_text[:1000]
                        )
                if _directive_contexts:
                    _directive_context_str = "\n\n".join(_directive_contexts)
                    context_parts.append(_directive_context_str)
        except ValueError:
            raise
        # ── end search directives ──────────────────────────────────────────

        # Ground the executor in the overall GOAL, not just the step. Weak
        # planners sometimes emit a degenerate step that is the answer itself
        # (e.g. plan=["Step 1: Rome"] for "capital of Italy"); without the goal
        # the executor has no actionable instruction and returns INSUFFICIENT
        # DATA, stalling the loop. Including the goal lets it answer regardless.
        content = f"Goal: {state.goal}\nStep: {step}"
        if context_parts:
            content += "\n\n" + "\n\n".join(context_parts)

        # N9: Prepend executor context from ContextPipeline if available
        _exec_ctx = state.context.get("_executor_context", "") or ""
        if _exec_ctx and len(_exec_ctx) > 50:
            content = f"[Relevant context for this step]\n{_exec_ctx[:1200]}\n\n{content}"

        # Collect available tools for structured tool calling (Task 1)
        # IMPORTANT: OpenAI function names must match ^[a-zA-Z0-9_-]{1,64}$
        # DO NOT include server_name in the name — "Jira Connector.jira_search_issues"
        # is invalid and causes OpenAI to return text instead of a tool call.
        _tool_defs: list[ToolDefinition] = []
        _tc_ctx = state.context.get("tool_context")
        # Grant enforcement: offer the model only granted tools (computed by the
        # planner, see PlannerMixin._granted_tool_names); dispatch still checks.
        _granted_names = state.context.get(GRANTED_TOOLS_KEY)
        # MEM-01: tool reliability learned from real dispatches. Unreliable tools
        # are offered last (with a hint); blacklisted tools are not offered when
        # another tool remains.
        _unreliable_rates: dict[str, float] = {}
        _avoid_names: set[str] = set()
        if _tc_ctx is not None and hasattr(_tc_ctx, "tools") and _tc_ctx.tools:
            _unreliable_rates, _blacklisted = await self._tool_reliability_view(
                state, tenant_ctx
            )
            _offerable = {
                _t.name
                for _t in _tc_ctx.tools
                if not (isinstance(_granted_names, list) and _t.name not in _granted_names)
            }
            if _offerable - _blacklisted:
                _avoid_names = _blacklisted & _offerable
        if _tc_ctx is not None and hasattr(_tc_ctx, "tools"):
            _ordered_tools = sorted(
                _tc_ctx.tools, key=lambda _t: getattr(_t, "name", "") in _unreliable_rates
            )
            for _t in _ordered_tools:
                if isinstance(_granted_names, list) and _t.name not in _granted_names:
                    continue
                if _t.name in _avoid_names:
                    continue
                import re as _re

                # Use only the bare tool name, sanitized to valid function-name chars
                _raw_name = _t.name if hasattr(_t, "name") else ""
                _safe_name = _re.sub(r"[^a-zA-Z0-9_-]", "_", _raw_name)[:64]
                if not _safe_name:
                    continue
                _tool_defs.append(
                    ToolDefinition(
                        name=_safe_name,
                        description=getattr(_t, "description", ""),
                        input_schema=getattr(_t, "input_schema", {}),
                    )
                )

        # Civilization: advertise the spawn tool so the LLM can actually call it.
        # Without this the SPAWN_TOOL_DEFINITION is never offered and the spawn
        # dispatch branch below is unreachable.
        if self._civilization_spawn_enabled and self._civilization_id:
            from app.civilization.spawn_tool import SPAWN_TOOL_DEFINITION

            _tool_defs.append(
                ToolDefinition(
                    name=str(SPAWN_TOOL_DEFINITION["name"]),
                    description=str(SPAWN_TOOL_DEFINITION["description"]),
                    input_schema=dict(SPAWN_TOOL_DEFINITION["parameters"]),
                )
            )

        # Build allowed-tools allowlist for anti-hallucination grounding
        _allowed_tools_set: set[str] = set()
        if _tc_ctx is not None:
            try:
                _tools_list = getattr(_tc_ctx, "tools", []) or []
                _allowed_tools_set = {t.name for t in _tools_list if hasattr(t, "name")}
                if isinstance(_granted_names, list):
                    # The ALLOWED TOOLS grounding must not list ungranted tools
                    # either: the model called them from this list.
                    _allowed_tools_set &= set(_granted_names)
                _allowed_tools_set -= _avoid_names
            except Exception:
                pass

        # Keep the civilization spawn tool in the anti-hallucination allowlist so
        # it is listed in the executor system prompt's ALLOWED TOOLS grounding.
        if self._civilization_spawn_enabled and self._civilization_id:
            from app.civilization.spawn_tool import (
                SPAWN_TOOL_DEFINITION as _SPAWN_TOOL_DEFINITION,
            )

            _allowed_tools_set.add(str(_SPAWN_TOOL_DEFINITION["name"]))

        # ToolPromptBuilder — enrich content with formatted tool descriptions (M5b)
        try:
            from app.context.tool_prompt_builder import ToolPromptBuilder

            if _tool_defs:
                _tpb = ToolPromptBuilder()
                _defs_as_dicts = [
                    {"name": td.name, "description": td.description} for td in _tool_defs
                ]
                _tool_context = _tpb.build(tools=_defs_as_dicts, step_context=step)
                if _tool_context:
                    content = f"{content}\n\nAvailable tools:\n{_tool_context}"
        except Exception:
            pass

        # N10 / MEM-01: tell the model which offered tools have been unreliable
        # (they are already ordered last) so it prefers an alternative.
        _offered_names = {getattr(_t, "name", "") for _t in (getattr(_tc_ctx, "tools", None) or [])}
        _unreliable_offered = sorted(
            (n for n in _unreliable_rates if n in _offered_names and n not in _avoid_names),
            key=lambda n: _unreliable_rates[n],
        )
        if _unreliable_offered:
            state.context["_unreliable_tools"] = _unreliable_offered
            _unreliable_hint = (
                "\n[Tool reliability: these tools have failed often for this tenant — "
                "prefer an alternative when one fits: "
                + ", ".join(
                    f"{n} ({round(_unreliable_rates[n] * 100)}% success)"
                    for n in _unreliable_offered[:5]
                )
                + "]"
            )
            content = content + _unreliable_hint if content else _unreliable_hint
        if _avoid_names:
            state.context["_avoided_tools"] = sorted(_avoid_names)

        # Select executor system prompt via PromptOptimizer if wired (Task 7)
        _executor_prompt = EXECUTOR_SYSTEM
        _exec_optimizer = getattr(self, "_prompt_optimizer", None)
        if _exec_optimizer is not None:
            # Scoped to the goal's tenant (it used to read only the "global"
            # scope, so a tenant's own executor variants were never used).
            try:
                if getattr(_exec_optimizer, "db_mode", False) is True:
                    _exec_variant = await _exec_optimizer.aselect_variant(
                        "executor", tenant_id=tenant_ctx.tenant_id
                    )
                else:
                    _exec_variant = _exec_optimizer.select_variant(
                        "executor", tenant_id=tenant_ctx.tenant_id
                    )
            except Exception:
                _exec_variant = None
            if _exec_variant is not None:
                _executor_prompt = _exec_variant.prompt_text

        # Inject allowed-tools list into executor system prompt
        if _allowed_tools_set:
            _tool_lines = "\n".join(f"  - {n}" for n in sorted(_allowed_tools_set)[:30])
            _executor_prompt = (
                _executor_prompt + f"\n\nALLOWED TOOLS (ONLY use these exact names):\n{_tool_lines}"
                # Tools exist, but not every step needs one. Weak models otherwise
                # emit garbage (e.g. a bare "Hello!") when no listed tool fits the
                # step — because the prompt pressures them to call something. Make
                # "answer directly" an explicit, valid option so a self-contained
                # step (arithmetic, a definition) is answered in one turn instead of
                # looping until the verifier rejects the noise.
                + "\n\nIf NONE of these tools is relevant to the current step "
                "(e.g. it is arithmetic, a definition, or reasoning you can do "
                "yourself), do NOT force a tool call — answer the step directly in "
                "plain, concise prose."
            )
        elif not _tool_defs:
            # No tools are available for this step. Weaker models, still steered by
            # the tool-calling system prompt, otherwise emit a (usually
            # hallucinated) tool call such as {"tool": "openai_chat_completion", …}
            # whose raw JSON then leaks into the result. Make "answer directly" the
            # only valid move so the model returns clean prose instead.
            _executor_prompt = _executor_prompt + (
                "\n\nNO TOOLS ARE AVAILABLE for this step. Do NOT emit a tool call or "
                "any JSON — there is nothing to call. Answer the step directly in "
                "plain, concise prose using the goal and provided context. Only if you "
                'genuinely lack the information, reply exactly with: {"tool": null, '
                '"result": "INSUFFICIENT DATA: <what is missing>"}.'
            )

        # Tool-call budget — force convergence. Once the goal has spent its budget
        # of tool calls across all steps, stop searching and make the executor
        # synthesize the final answer from what it already gathered, rather than
        # re-searching on every replan (which never converges for weak planners).
        _tool_budget = int(getattr(self, "_tool_call_budget", _DEFAULT_TOOL_CALL_BUDGET))
        _tool_calls_used = sum(len(s.tool_calls or []) for s in state.steps)
        _budget_exhausted = _tool_budget > 0 and _tool_calls_used >= _tool_budget
        # Tools + tool-call policy for this step. Default: offer every tool and let
        # the provider force a call (tool_choice defaults to "required").
        _step_tool_defs = _tool_defs
        _step_tool_choice: str | None = None
        if _budget_exhausted:
            # Budget spent: stop the re-search loop but do NOT starve the final
            # delivery action. Keep only ACTION tools (writes/deliveries), drop
            # read/search tools, and use tool_choice="auto" so a pure synthesis
            # step can answer in text while a delivery step can still fire its tool.
            from app.agent.nodes._helpers import select_action_tools_for_convergence

            _step_tool_defs = select_action_tools_for_convergence(_tool_defs)
            _step_tool_choice = "auto"
            _executor_prompt = _executor_prompt + (
                f"\n\nTOOL-CALL BUDGET REACHED ({_tool_calls_used}/{_tool_budget}). Do NOT "
                "perform any more search/retrieval. Using the information already gathered "
                "in the context above, produce the best possible final answer now. If the "
                "goal requires a delivery/notification action (e.g. sending a message), you "
                "MAY still call that one action tool to complete the goal; otherwise answer "
                "directly. If some data is missing, answer with what you have and note gaps."
            )

        # Resolve executor model via model_router when available (Bug 3 fix)
        _exec_model = ""
        if self._model_router is not None:
            with contextlib.suppress(Exception):
                _exec_model = self._model_router.model_for("execution") or ""

        req = CompletionRequest(
            messages=[
                Message(role="system", content=_executor_prompt),
                Message(role="user", content=content),
            ],
            model=_exec_model,
            # Once the budget is spent, only ACTION tools remain (so the model cannot
            # keep searching but can still deliver the final answer); tool_choice
            # relaxes to "auto" so a synthesis step is not forced to call a tool.
            tools=_step_tool_defs,
            tool_choice=_step_tool_choice,
        )

        # Tool steps need a provider that can actually send tool definitions.
        # A tool-less provider (e.g. Gemini's text adapter) used to raise mid-run
        # from inside the LLM call; fail the step clearly before spending on it.
        if req.tools:
            _supports_tools = getattr(self._executor, "supports_tool_use", None)
            _tool_capable: object = True
            if callable(_supports_tools):
                try:
                    _tool_capable = _supports_tools()
                except Exception:
                    _tool_capable = True
            if _tool_capable is False:
                raise StepNotExecutedError(
                    "The executor provider does not support tool calling, but this step "
                    f"has {len(req.tools)} tool(s) available; configure a tool-capable "
                    "executor model (e.g. Anthropic or an OpenAI-compatible provider)."
                )

        # 0. Cost pre-flight: the only budget check on this path previously ran
        # AFTER the LLM call completed (below, using the actual token cost) —
        # meaning an already-over-budget goal would still pay for, and make,
        # another full LLM call on every subsequent iteration before being
        # told "budget exceeded" post-hoc, compounding real spend past
        # per_goal_usd / per_tenant_daily_usd on every replan until
        # max_iterations finally stopped it.
        #
        # Note this can't be closed by re-checking CostController's recorded
        # totals: check_and_record deliberately does NOT charge a denied call
        # (see its Lua script docstring — "a denied request never permanently
        # charges the tenant"), so goal_total()/daily_total() stay unchanged
        # after a denial and a totals-based pre-check would immediately pass
        # again next iteration, reproducing the same bug one layer down.
        # Instead latch onto agent_state once any call has been denied for
        # this goal, and refuse to start another for the rest of the run.
        if state.context.get("_budget_exhausted"):
            return "Step skipped: budget exceeded."

        # 8a. Bulkhead — distributed concurrency limit per tenant (RedisBulkhead or Semaphore)
        _bulkhead = None
        if self._bulkhead_registry is not None and tenant_ctx is not None:
            try:
                _bulkhead = self._bulkhead_registry.get_bulkhead(tenant_ctx.tenant_id)
            except Exception:
                _bulkhead = None

        _bulkhead_acquired = False
        if _bulkhead is not None:
            from app.reliability.bulkhead import (
                BulkheadFullError,
                acquire_with_wait,
                bulkhead_wait_seconds,
            )

            _bh_wait_s = bulkhead_wait_seconds()
            try:
                # A full bulkhead is waited on (bounded, backoff + jitter) and
                # refused only once the wait expires (a08-F199-02).
                _bh_waited = await acquire_with_wait(_bulkhead, wait_s=_bh_wait_s)
                _bulkhead_acquired = True
                if _bh_waited >= 0.5:
                    self._logger.info(
                        "bulkhead_slot_waited",
                        tenant_id=getattr(tenant_ctx, "tenant_id", ""),
                        waited_s=round(_bh_waited, 2),
                    )
            except BulkheadFullError:
                _bulkhead_acquired = False
            except Exception as bulkhead_exc:
                # Fail closed: the concurrency limit could not be checked, so the
                # step does not run unthrottled (it used to proceed without a slot).
                self._logger.warning("bulkhead_acquire_failed", error=str(bulkhead_exc))
                raise StepNotExecutedError(
                    f"Tenant concurrency limit could not be checked "
                    f"({type(bulkhead_exc).__name__}); step was not executed."
                ) from bulkhead_exc
            if not _bulkhead_acquired:
                self._logger.warning(
                    "bulkhead_full",
                    tenant_id=getattr(tenant_ctx, "tenant_id", ""),
                    step=step[:100],
                )
                # Not a step result: it used to be returned AS the step's output.
                raise StepNotExecutedError(
                    "Bulkhead: too many concurrent operations for this tenant; no slot "
                    f"became free within {_bh_wait_s:g}s; step was not executed."
                )

        # Token streaming — buffer for accumulation and closure for on_token callback.
        # Defined before the bulkhead try so the closure captures step by value.
        _token_buffer: list[str] = []
        _step_for_token = step

        async def _on_token(chunk: str) -> None:
            _token_buffer.append(chunk)
            await self._emit(
                {
                    "type": "token_chunk",
                    "step": _step_for_token,
                    "token": chunk,
                    "cumulative": "".join(_token_buffer),
                }
            )

        _llm_call_start = time.monotonic()
        try:
            try:
                async with track_tool_call(tool_name=tool_name, tenant_id=tenant_ctx.tenant_id):
                    resp = await self._stream_with_failover(req, _on_token, _token_buffer)
                if _active_breaker is not None:
                    await _active_breaker.record_success_async()
                # D-13: feed provider health so ModelOrchestrator failover learns —
                # attributed to the model that actually served (after failover) and
                # a failure for each model that did not.
                for _failed_model in getattr(self, "_failed_models", []) or []:
                    self._record_provider_health(_failed_model, ok=False, start=_llm_call_start)
                self._record_provider_health(
                    getattr(self, "_last_served_model", "") or _exec_model,
                    ok=True,
                    start=_llm_call_start,
                )
            except Exception:
                if _active_breaker is not None:
                    await _active_breaker.record_failure_async()
                self._record_stream_failure(_exec_model, _llm_call_start)
                raise
        finally:
            if _bulkhead_acquired and _bulkhead is not None:
                try:
                    if hasattr(_bulkhead, "release"):
                        await _bulkhead.release()  # RedisBulkhead
                    else:
                        _bulkhead.release()  # asyncio.Semaphore
                except Exception:
                    pass

        # A goal-tree sub-agent runs under its own child id but spends (and is
        # ledgered against) its parent goal — the same attribution charge_llm_call
        # applies to the planner / verifier / reasoning calls.
        _charge_goal_id = str(state.context.get("_budget_goal_id") or state.goal_id or "")

        # 1. Calculate actual LLM cost from token usage and check budget
        if self._cost_controller is not None:
            from app.agent.nodes.llm_cost import llm_call_tokens as _gate_tokens
            from app.intelligence.cost_tracker import calculate_cost as _gate_cost

            # Same pricing function, model and token counts as the ledger (1b).
            _actual_cost = _gate_cost(
                resp.model if hasattr(resp, "model") and resp.model else _exec_model,
                *_gate_tokens(resp),
            )
            async with self._state_lock:
                state.context["total_cost_usd"] = (
                    state.context.get("total_cost_usd", 0.0) + _actual_cost
                )
            from app.governance.cost import llm_spend

            ok = await llm_spend(
                self._cost_controller.check_and_record(
                    goal_id=_charge_goal_id,
                    cost_usd=_actual_cost,
                    tenant_ctx=tenant_ctx,
                )
            )
            if not ok:
                # Latch so no further LLM call is attempted for the rest of
                # this goal's run (see the pre-flight check above).
                state.context["_budget_exhausted"] = True
                return "Step skipped: budget exceeded."

        # 1b. Record ACTUAL token cost via CostTracker. Uses provider ``usage`` when
        # present, else the response token totals: streamed (tool-less) steps carry
        # no ``usage`` object, and used to skip the ledger entirely.
        from app.agent.nodes.llm_cost import llm_call_tokens as _llm_tokens

        _ledger_prompt_tok, _ledger_completion_tok = _llm_tokens(resp)
        if self._cost_tracker is not None and (_ledger_prompt_tok or _ledger_completion_tok):
            try:
                from app.intelligence.cost_tracker import calculate_cost as _calc_cost

                _model_name = resp.model if hasattr(resp, "model") and resp.model else _exec_model
                _real_cost = _calc_cost(
                    _model_name,
                    _ledger_prompt_tok,
                    _ledger_completion_tok,
                )
                async with self._state_lock:
                    # This is the SAME LLM call already charged above (via the
                    # deprecated governance.pricing estimate, used only to drive
                    # the real-time budget check) — replace that estimate with
                    # CostTracker's authoritative figure instead of adding on
                    # top of it, so one call is billed once, not twice.
                    _already_charged = _actual_cost if "_actual_cost" in locals() else 0.0
                    state.context["total_cost_usd"] = (
                        state.context.get("total_cost_usd", 0.0) - _already_charged + _real_cost
                    )
                await self._cost_tracker.record_llm_usage(
                    model=_model_name,
                    prompt_tokens=_ledger_prompt_tok,
                    completion_tokens=_ledger_completion_tok,
                    tenant_ctx=tenant_ctx,
                    goal_id=_charge_goal_id,
                    agent_id=state.context.get("agent_id"),
                    role="executor",
                )
            except Exception as _ct_exc:
                self._logger.warning("cost_tracker_record_failed", error=str(_ct_exc))

        # 1c. Charge this step's spend to the grant that authorised the agent.
        # Grant.max_cost_usd is documented as the spend a grant authorises, but
        # nothing ever recorded spend against a grant, so the cap could not bind
        # — the tool gate below passes no cost_usd (it cannot know one in
        # advance), leaving the comparison permanently 0.0 > cap. Recording the
        # step's real LLM cost here is what makes the budget deny later calls.
        if self._enforce_grants and self._grant_store is not None:
            _step_cost = 0.0
            if "_real_cost" in locals():
                _step_cost = float(_real_cost)
            elif "_actual_cost" in locals():
                _step_cost = float(_actual_cost)
            if _step_cost > 0.0:
                await self._charge_grant_spend(state, tenant_ctx, _step_cost)

        # 2.3: Per-goal executor cost tracking
        try:
            # Durable (Postgres, tenant-scoped) when bound — not this process's memory.
            from app.observability.cost_breakdown import arecord_role_cost as _rrc

            # Provenance: the model that SERVED the call — after a failover the
            # requested ``_exec_model`` (e.g. a dead pinned model) did not, and
            # it used to be recorded as the executor's model anyway.
            from app.providers.circuit_breaker import fallback_from_of as _fb_of

            _served_exec_model = str(
                resp.model if hasattr(resp, "model") and resp.model else _exec_model
            )
            _exec_fallback_from = [m for m in _fb_of(resp) if m != _served_exec_model]
            await _rrc(
                goal_id=_charge_goal_id,
                tenant_id=tenant_ctx.tenant_id,
                role="executor",
                model=_served_exec_model,
                fallback_from=_exec_fallback_from,
                input_tok=getattr(resp, "input_tokens", 0),
                output_tok=getattr(resp, "output_tokens", 0),
                cost=(
                    _real_cost
                    if "_real_cost" in locals()
                    else (_actual_cost if "_actual_cost" in locals() else 0.0)
                ),
            )
        except Exception:
            pass
        raw_output = resp.content
        raw_output_sanitized = False

        # Prefer structured tool_calls from provider; fall back to text parsing (Task 1)
        _structured_tcs: list[dict[str, Any]] = resp.tool_calls if resp.tool_calls else []
        if _structured_tcs:
            _first_stc = _structured_tcs[0]
            _stc_name = _first_stc.get("name") or _first_stc.get("tool_name", "")
            _stc_args = _first_stc.get("input") or _first_stc.get("arguments") or {}
            if not isinstance(_stc_args, dict):
                _stc_args = {}
            tool_call = ToolCall(tool=_stc_name, arguments=_stc_args) if _stc_name else None
            # Update tool_name from structured response (Task 3)
            if _stc_name:
                tool_name = self._extract_tool_name(
                    step, tool_calls_result=[{"tool_name": _stc_name}]
                )
        else:
            tool_call = extract_tool_call(raw_output)
        if tool_call is not None:
            tool_call = await repair_tool_call_arguments(tool_call, step, goal=state.goal)
        # Set once an MCP tool call actually SUCCEEDED; carries the tool's output
        # (ids of created objects) for the rollback registration in step 10.
        _rb_executed: dict[str, Any] | None = None
        # OI-1: fingerprint of a side-effecting (non-read) MCP call, recorded in the
        # goal's action ledger once it succeeded so no replan re-issues it.
        _sfx_fp: str | None = None
        _sfx_tool_ref: Any = None
        # Validate tool name before dispatching
        if tool_call is not None and tool_call.tool:
            from app.agent.tool_calls import validate_tool_name as _validate_tn

            _tn_rejection = _validate_tn(tool_call.tool, _allowed_tools_set)
            _offlist_grant_denial = (
                await self._deny_ungranted_call(tool_call.tool, state, tenant_ctx)
                if _tn_rejection
                else None
            )
            if _offlist_grant_denial is not None:
                # P8-2: an ungranted (never offered) tool — refused by the grant
                # gate with an event and an audit row, not as an "unknown tool".
                _taint_step_cache()
                raw_output = self._sanitize_tool_raw_output(
                    f"Tool call denied: '{tool_call.tool}' is not granted to this agent "
                    f"({_offlist_grant_denial}). Do not call it again; complete the step "
                    "with the information already available or other permitted tools."
                )
                raw_output_sanitized = True
                tool_call = None  # never dispatched
            elif _tn_rejection:
                raw_output = _tn_rejection
                _taint_step_cache()
                raw_output_sanitized = True
                await self._emit(
                    {
                        "type": "tool_call_failed",
                        "tool": tool_call.tool,
                        "error": _tn_rejection[:300],
                    }
                )
                record_tool_call(
                    tool_call.tool,
                    "unknown",
                    "rejected",
                    0.0,
                )
                tool_call = None  # prevent dispatch
        if tool_call is not None:
            # MCPGOV-01: normalise the arguments to the tool's schema and validate
            # them BEFORE any governance, so the guardrails, policy rules, grants,
            # risk gate and approval decide on exactly what is dispatched
            # (MCPClient.call_tool no longer rewrites them after the decision).
            _arg_rejection = self._prepare_call_arguments(tool_call, state)
            if _arg_rejection is not None:
                _taint_step_cache()
                raw_output = self._sanitize_tool_raw_output(_arg_rejection)
                raw_output_sanitized = True
                await self._emit(
                    {
                        "type": "tool_call_failed",
                        "tool": tool_call.tool,
                        "error": _arg_rejection[:300],
                    }
                )
                record_tool_call(tool_call.tool, "unknown", "arg_validation_failed", 0.0)
                tool_call = None  # never dispatched
        if tool_call is not None:
            # Tool-argument guardrails BEFORE the MCP call (shared with the
            # parallel extra-call path, so every call of a turn is checked).
            await self._guard_tool_args(tool_name, tool_call.arguments, step, state, tenant_ctx)

            tool_call_started = time.monotonic()
            if self._mcp_client is None:
                self._logger.warning("mcp_client_none_at_tool_dispatch tool=%s", tool_call.tool)
                error = self._sanitize_tool_raw_output("MCP client unavailable")
                await self._emit(
                    {
                        "type": "tool_call_failed",
                        "tool": tool_call.tool,
                        "error": error,
                    }
                )
                record_tool_call(
                    tool_call.tool,
                    "unknown",
                    "failed",
                    time.monotonic() - tool_call_started,
                )
                raw_output = error
                raw_output_sanitized = True
            else:
                tool_context = state.context.get("tool_context")
                tool_ref = (
                    tool_context.find_tool(tool_call.tool)
                    if tool_context is not None and hasattr(tool_context, "find_tool")
                    else None
                )
                # Persisted per-agent permissions (agent_permissions): these were
                # written by PUT /agents/{id}/permissions but never enforced.
                _perm_tool_name = tool_ref.name if tool_ref is not None else tool_call.tool
                # Tenant tool policy on the tool actually being called.
                _pol_denial = await self._tool_policy_gate(
                    tool_name=_perm_tool_name,
                    step=step,
                    state=state,
                    tenant_ctx=tenant_ctx,
                    already_checked=_step_policy_tool,
                    arguments=tool_call.arguments,
                )
                _perm_denial = (
                    None
                    if _pol_denial is not None
                    else await self._agent_permission_gate(
                        state=state, tenant_ctx=tenant_ctx, tool_name=_perm_tool_name, step=step
                    )
                )
                # Grantex governance gate (mandatory, opt-in): an agent may only
                # run a tool it holds a covering, active, unrevoked grant for.
                # Pass-through until enforcement is enabled for the deploy.
                _grant_denial = None
                if tool_ref is not None and _perm_denial is None and _pol_denial is None:
                    _grant_decision = await enforce_tool_call(
                        self._grant_store,
                        tenant_id=tenant_ctx.tenant_id,
                        agent_id=self._agent_id or "",
                        tool_name=tool_ref.name,
                        enabled=self._enforce_grants,
                    )
                    if not _grant_decision.allowed:
                        _grant_denial = _grant_decision
                    elif _grant_decision.grant_id:
                        await self._set_authorizing_grant(
                            state, tenant_ctx, _grant_decision.grant_id
                        )
                if _pol_denial is not None:
                    _taint_step_cache()
                    await self._emit(
                        {
                            "type": "tool_call_blocked_by_policy",
                            "tool": _perm_tool_name,
                            "reason": _pol_denial,
                        }
                    )
                    raw_output = self._sanitize_tool_raw_output(
                        f"Tool call denied: '{_perm_tool_name}' is blocked by tenant policy "
                        f"({_pol_denial}). Do not call it again; complete the step with the "
                        "information already available or other permitted tools."
                    )
                    raw_output_sanitized = True
                elif _perm_denial is not None:
                    _taint_step_cache()
                    await self._emit(
                        {
                            "type": "tool_call_blocked_by_agent_permission",
                            "tool": _perm_tool_name,
                            "reason": _perm_denial,
                        }
                    )
                    record_tool_call(_perm_tool_name, "agent_permission", "denied", 0.0)
                    raw_output = self._sanitize_tool_raw_output(
                        f"Tool call denied: '{_perm_tool_name}' is not permitted for this "
                        f"agent ({_perm_denial}). Do not call it again; complete the step "
                        "with the information already available or other permitted tools."
                    )
                    raw_output_sanitized = True
                elif _grant_denial is not None:
                    _taint_step_cache()
                    # The tool is NOT run. The refusal used to raise and fail the
                    # whole goal — a real model that reached for an ungranted tool
                    # (with the answer already in its retrieved context) killed an
                    # otherwise answerable goal. It is now an observation the model
                    # can act on, like "Tool not found" below.
                    await self._emit(
                        {
                            "type": "tool_call_blocked_by_grant",
                            "tool": tool_ref.name,
                            "reason": _grant_denial.reason,
                        }
                    )
                    record_tool_call(tool_ref.name, "grant", "denied", 0.0)
                    await self._audit_grant_denial(
                        tool_ref.name, str(_grant_denial.reason), state, tenant_ctx
                    )
                    raw_output = self._sanitize_tool_raw_output(
                        f"Tool call denied: '{tool_ref.name}' is not granted to this agent "
                        f"({_grant_denial.reason}). Do not call it again; complete the step "
                        "with the information already available or other permitted tools."
                    )
                    raw_output_sanitized = True
                elif tool_ref is None:
                    _taint_step_cache()
                    # Tracks whether the civilization spawn branch already handled
                    # this call, so the RPA / "tool not found" fallthrough below
                    # does not clobber its result with a spurious failure.
                    _civ_spawn_handled = False
                    # Check if it's a civilization spawn tool call
                    if (
                        self._civilization_spawn_enabled
                        and self._civilization_id
                        and tool_call.tool == "civilization_spawn"
                    ):
                        _civ_spawn_handled = True
                        try:
                            from app.civilization.governor import Governor
                            from app.civilization.spawn_tool import execute_spawn_tool

                            _gov_kwargs: dict[str, Any] = {
                                "civilization_id": self._civilization_id,
                                "tenant_id": tenant_ctx.tenant_id,
                            }
                            if self._db_session_factory is not None:
                                _gov_kwargs["db_session_factory"] = self._db_session_factory
                            _civ_const_placeholder = None
                            try:
                                from app.civilization.models import Constitution

                                _civ_const_placeholder = Constitution()
                            except Exception:
                                pass
                            if _civ_const_placeholder is not None:
                                _gov_kwargs["constitution"] = _civ_const_placeholder
                            governor = Governor(**_gov_kwargs)
                            _spawn_args = tool_call.arguments or {}
                            _spawn_ctx = state.context if isinstance(state.context, dict) else {}
                            spawn_result = await execute_spawn_tool(
                                capability=str(_spawn_args.get("capability", "")),
                                goal=str(_spawn_args.get("goal", "")),
                                priority=str(_spawn_args.get("priority", "normal")),
                                governor=governor,
                                requester_agent_id=str(getattr(state, "agent_id", "") or ""),
                                depth=int(_spawn_ctx.get("civilization_depth", 0) or 0),
                                parent_budget_usd=float(
                                    _spawn_ctx.get("civilization_parent_budget_usd", 0.0) or 0.0
                                ),
                                parent_policy_ids=list(
                                    _spawn_ctx.get("civilization_parent_policy_ids", []) or []
                                ),
                                tenant_ctx=tenant_ctx,
                                goal_service=self._goal_service,
                                civilization_id=self._civilization_id,
                            )
                            raw_output = str(spawn_result)
                            await self._emit(
                                {
                                    "type": "child_agent_spawned",
                                    "parent_agent_id": getattr(state, "agent_id", ""),
                                    "child_agent_id": spawn_result.get("agent_id"),
                                    "child_goal_id": spawn_result.get("goal_id"),
                                    "depth": spawn_result.get("depth", 0),
                                    "capability": str(_spawn_args.get("capability", "")),
                                }
                            )
                            raw_output_sanitized = True
                            # Grantex delegation: mint narrowed grants for the child
                            # so its tool calls are enforced against authority that
                            # can only be <= the parent's (never widen). Best-effort:
                            # on failure the child simply holds no grant (fail-closed
                            # under enforcement), never over-permitted.
                            if self._enforce_grants and self._grant_store:
                                await self._delegate_grants_to_child(
                                    state, tenant_ctx, str(spawn_result.get("agent_id") or "")
                                )
                            record_tool_call(
                                tool_call.tool,
                                "civilization",
                                "success",
                                time.monotonic() - tool_call_started,
                            )
                        except Exception as _spawn_exc:
                            raw_output = f"Civilization spawn error: {_spawn_exc}"
                            await self._emit(
                                {
                                    "type": "tool_call_failed",
                                    "tool": tool_call.tool,
                                    "error": str(_spawn_exc),
                                }
                            )
                            raw_output_sanitized = True
                    # Check if it's a built-in RPA tool (rpa_open_url, rpa_click, etc.)
                    from app.rpa.tools import RPA_TOOLS as _RPA_TOOLS

                    _rpa_tool_names = {str(t["name"]) for t in _RPA_TOOLS}
                    if tool_call.tool in _rpa_tool_names or any(
                        tool_call.tool.endswith(f".{t}") for t in _rpa_tool_names
                    ):
                        # Dispatch directly to RPAExecutor
                        rpa_tool_name = (
                            tool_call.tool.split(".")[-1]
                            if "." in tool_call.tool
                            else tool_call.tool
                        )
                        rpa_executor = getattr(
                            state.context.get("_app_state"), "rpa_executor", None
                        ) or getattr(self, "_rpa_executor", None)
                        if rpa_executor is not None:
                            try:
                                goal_id_str = str(getattr(state, "goal_id", ""))
                                rpa_result = await rpa_executor.execute(
                                    tool_name=rpa_tool_name,
                                    arguments=tool_call.arguments or {},
                                    tenant_id=tenant_ctx.tenant_id,
                                    goal_id=goal_id_str,
                                )
                                raw_output = (
                                    rpa_result.output
                                    if rpa_result.success
                                    else f"RPA error: {rpa_result.error}"
                                )
                                await self._emit(
                                    {
                                        "type": "tool_call_complete",
                                        "tool": tool_call.tool,
                                        "server_id": "rpa",
                                        "success": rpa_result.success,
                                        "output": raw_output,
                                        "artifact_url": rpa_result.artifact_url,
                                        "artifact_name": rpa_result.artifact_name,
                                    }
                                )
                                record_tool_call(
                                    rpa_tool_name,
                                    "rpa",
                                    "success" if rpa_result.success else "failed",
                                    time.monotonic() - tool_call_started,
                                )
                                raw_output_sanitized = True
                                # ── RPA failure → ExecutionMemory + SelfOptimizer ──
                                if not rpa_result.success:
                                    _rpa_url_fail = (tool_call.arguments or {}).get("url", "") or (
                                        state.context.get("_current_rpa_url", "")
                                        if isinstance(state.context, dict)
                                        else ""
                                    )
                                    # Record failure in ExecutionMemory for recall
                                    await self._record_rpa_failure(
                                        state,
                                        tenant_ctx,
                                        tool_name=rpa_tool_name,
                                        url=str(_rpa_url_fail),
                                        error=rpa_result.error or "unknown",
                                    )
                                    # Generate RPA-specific suggestions
                                    if self._self_optimizer is not None:
                                        self._self_optimizer.analyze_rpa_failure(
                                            tool_name=rpa_tool_name,
                                            error=rpa_result.error or "",
                                            url=str(_rpa_url_fail),
                                            tenant_ctx=tenant_ctx,
                                        )
                                # ── RPA → LTM persistence ──────────────────
                                # Store extracted text and vision analysis so
                                # future goals can recall what was found on
                                # this page via semantic search.
                                if (
                                    rpa_result.success
                                    and self._long_term_memory is not None
                                    and rpa_tool_name in ("rpa_extract_text", "rpa_screenshot")
                                    and rpa_result.output
                                    and len(rpa_result.output) > 50
                                ):
                                    _rpa_url = (tool_call.arguments or {}).get(
                                        "url",
                                        (
                                            state.context.get("_current_rpa_url", "")
                                            if isinstance(state.context, dict)
                                            else ""
                                        ),
                                    )
                                    _rpa_src = (
                                        "rpa_vision"
                                        if rpa_tool_name == "rpa_screenshot"
                                        else "rpa_extraction"
                                    )
                                    _rpa_ltm_task = asyncio.create_task(
                                        self._long_term_memory.store_rpa_extraction(
                                            url=str(_rpa_url or "unknown"),
                                            extracted_text=rpa_result.output,
                                            goal_id=str(getattr(state, "goal_id", "")),
                                            tenant_ctx=tenant_ctx,
                                            db=self._db_session_factory,
                                            embedder=self._embedder,
                                            source_type=_rpa_src,
                                        )
                                    )
                                    self._background_tasks.add(_rpa_ltm_task)
                                    _rpa_ltm_task.add_done_callback(self._background_tasks.discard)
                                    _rpa_ltm_task.add_done_callback(_log_background_failure)
                                # Track current URL for extraction attribution
                                if rpa_tool_name == "rpa_open_url":
                                    _nav_url = (tool_call.arguments or {}).get("url", "")
                                    if isinstance(state.context, dict) and _nav_url:
                                        state.context["_current_rpa_url"] = _nav_url
                            except Exception as _rpa_exc:
                                raw_output = f"RPA execution error: {_rpa_exc}"
                                await self._emit(
                                    {
                                        "type": "tool_call_failed",
                                        "tool": tool_call.tool,
                                        "error": str(_rpa_exc),
                                    }
                                )
                                raw_output_sanitized = True
                        else:
                            raw_output = self._sanitize_tool_raw_output(
                                f"Tool not found: {tool_call.tool}"
                            )
                            raw_output_sanitized = True
                            await self._emit(
                                {
                                    "type": "tool_call_failed",
                                    "tool": tool_call.tool,
                                    "error": self._sanitize_tool_event_value("Tool not found"),
                                }
                            )
                            record_tool_call(
                                tool_call.tool,
                                "unknown",
                                "failed",
                                time.monotonic() - tool_call_started,
                            )
                    elif not _civ_spawn_handled:
                        # Existing "tool_ref is None" error handling (skipped when
                        # the civilization spawn branch already handled the call).
                        raw_output = self._sanitize_tool_raw_output(
                            f"Tool not found: {tool_call.tool}"
                        )
                        raw_output_sanitized = True
                        await self._emit(
                            {
                                "type": "tool_call_failed",
                                "tool": tool_call.tool,
                                "error": self._sanitize_tool_event_value("Tool not found"),
                            }
                        )
                        record_tool_call(
                            tool_call.tool,
                            "unknown",
                            "failed",
                            time.monotonic() - tool_call_started,
                        )
                else:
                    tool_risk = classify_tool_risk(
                        tool_ref.name, tool_ref.server_name, tool_call.arguments
                    )
                    # Gate write_high bypass behind an explicit env flag (default-secure).
                    import os as _os

                    _allow_fa_write_high = (
                        _os.getenv("ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH", "false").lower() == "true"
                    )
                    # Per-connector opt-in: the user explicitly marked this connector
                    # "Allow autonomous execution", so its high-risk tools run without
                    # a human approver in autonomous goals. Scoped to this connector.
                    _connector_auto_approve = bool(getattr(tool_ref, "auto_approve", False))
                    _effective_risk = resolve_effective_tool_risk(
                        tool_risk,
                        autonomy_mode=self._autonomy_mode,
                        connector_auto_approve=_connector_auto_approve,
                        allow_fa_write_high=_allow_fa_write_high,
                    )
                    if _effective_risk != tool_risk and _connector_auto_approve:
                        await self._emit(
                            {
                                "type": "tool_call_auto_approved",
                                "tool": tool_ref.name,
                                "server_id": tool_ref.server_id,
                                "reason": "connector opted into autonomous execution",
                            }
                        )
                    tool_risk = _effective_risk
                    # else: falls through to write_high HITL gate below (default-secure)
                    _ckpt_blocked = tool_risk != "read" and _checkpoint_degraded(state)
                    # OI-1: a side-effecting call this goal already ran (identical
                    # server, tool and arguments) is never dispatched again — a
                    # replanned or retried step gets the recorded result instead
                    # of a second side effect and a second approval request.
                    _sfx_prior: dict[str, Any] | None = None
                    if (
                        classify_tool_risk(tool_ref.name, tool_ref.server_name, tool_call.arguments)
                        != "read"
                    ):
                        from app.agent.goal_action_ledger import call_fingerprint

                        _sfx_fp = call_fingerprint(
                            tool_ref.server_id, tool_ref.name, tool_call.arguments
                        )
                        _sfx_tool_ref = tool_ref
                        _sfx_prior = await self._goal_action_ledger(state, tenant_ctx).executed(
                            _sfx_fp
                        )
                    if _sfx_prior is not None:
                        from app.agent.goal_action_ledger import replay_output

                        _sfx_fp = None  # already recorded; nothing new ran
                        raw_output = self._sanitize_tool_raw_output(replay_output(_sfx_prior))
                        raw_output_sanitized = True
                        await self._emit(
                            {
                                "type": "tool_call_already_executed",
                                "tool": tool_ref.name,
                                "server_id": tool_ref.server_id,
                                "first_step_id": str(_sfx_prior.get("step_id") or ""),
                            }
                        )
                        if state.steps:
                            # The replayed record is this step's evidence (the
                            # verifier / grounding gates check outputs against it).
                            state.steps[-1].tool_calls.append(
                                {
                                    "tool_name": tool_ref.name,
                                    "server_id": tool_ref.server_id,
                                    "success": True,
                                    "error": "",
                                    "output": raw_output[:1000],
                                    "replayed": True,
                                }
                            )
                        record_tool_call(
                            tool_ref.name,
                            tool_ref.server_id,
                            "deduplicated",
                            time.monotonic() - tool_call_started,
                        )
                    elif tool_risk == "destructive" or _ckpt_blocked:
                        _taint_step_cache()
                        error = self._sanitize_tool_raw_output(
                            f"Tool '{tool_ref.name}' was not run: this goal's checkpoints "
                            "could not be saved, so a crash could repeat side effects "
                            "(checkpoint_degraded)."
                            if _ckpt_blocked
                            else f"Tool '{tool_ref.name}' denied as destructive."
                        )
                        await self._emit(
                            {
                                "type": "tool_call_failed",
                                "tool": tool_ref.name,
                                "server_id": tool_ref.server_id,
                                "error": error,
                            }
                        )
                        record_tool_call(
                            tool_ref.name,
                            tool_ref.server_id,
                            "denied",
                            time.monotonic() - tool_call_started,
                        )
                        raw_output = error
                        raw_output_sanitized = True
                    elif tool_risk == "write_high":
                        if self._hitl_gateway is None or self._autonomy_mode != "supervised":
                            _taint_step_cache()
                            # Nobody will decide an approval here (no gateway, or a
                            # non-supervised run), so the tool is not dispatched and
                            # no approval request is filed — one used to be filed
                            # and left pending forever (CORE-01).
                            error = self._sanitize_tool_raw_output(
                                f"Tool '{tool_ref.name}' requires approval."
                                if self._hitl_gateway is None
                                else f"High-risk tool '{tool_ref.name}' requires approval "
                                f"(non-supervised mode); it was not executed. Run the goal "
                                f"in supervised mode to approve it."
                            )
                            await self._emit(
                                {
                                    "type": "tool_call_failed",
                                    "tool": tool_ref.name,
                                    "server_id": tool_ref.server_id,
                                    "error": error,
                                }
                            )
                            record_tool_call(
                                tool_ref.name,
                                tool_ref.server_id,
                                "failed",
                                time.monotonic() - tool_call_started,
                            )
                            raw_output = error
                            raw_output_sanitized = True
                        else:
                            from app.agent.goal_action_ledger import (
                                call_approval_key,
                                call_fingerprint,
                            )

                            _call_key = call_approval_key(
                                _sfx_fp
                                or call_fingerprint(
                                    tool_ref.server_id, tool_ref.name, tool_call.arguments
                                )
                            )
                            # OI-1: the identical call (same server, tool and
                            # arguments) was already APPROVED in this goal — e.g. it
                            # failed after approval and is retried — reuse that
                            # decision instead of asking again.
                            if not await self._reuse_approval(
                                state,
                                tenant_ctx,
                                _call_key,
                                action=tool_ref.name,
                                scope="tool_call",
                            ):
                                req_id = await self._file_approval_request(
                                    goal_id=state.goal_id,
                                    action=tool_ref.name,
                                    risk_level=tool_risk,
                                    tenant_ctx=tenant_ctx,
                                )
                                await self._emit(
                                    {
                                        "type": "waiting_approval",
                                        "request_id": req_id,
                                        "action": tool_ref.name,
                                        "tool": tool_ref.name,
                                    }
                                )
                                await self._emit(
                                    {
                                        "type": "tool_call_pending_approval",
                                        "tool": tool_ref.name,
                                        "server_id": tool_ref.server_id,
                                        "request_id": req_id,
                                        "risk": tool_risk,
                                    }
                                )
                                _hitl_start = time.monotonic()
                                final_status = await self._await_approval_decision(
                                    req_id, tenant_ctx=tenant_ctx
                                )
                                record_approval_wait(time.monotonic() - _hitl_start)
                                if final_status == ApprovalStatus.REJECTED:
                                    raise PermissionError(
                                        f"Tool '{tool_ref.name}' was rejected by human "
                                        "approver."
                                    )
                                elif final_status == ApprovalStatus.TIMED_OUT:
                                    raise PermissionError(
                                        f"Tool '{tool_ref.name}' approval timed out."
                                    )
                                elif final_status != ApprovalStatus.APPROVED:
                                    # Fail closed: only an explicit APPROVED runs the
                                    # tool. A still-PENDING result (e.g. the wait was
                                    # cut short by a Redis error) used to fall through.
                                    raise PermissionError(
                                        f"Tool '{tool_ref.name}' was not approved "
                                        f"({final_status})."
                                    )
                                await self._remember_approval(
                                    state,
                                    tenant_ctx,
                                    _call_key,
                                    request_id=req_id,
                                    action=tool_ref.name,
                                )
                                # APPROVED: now actually dispatch the tool call
                                await self._emit(
                                    {"type": "approval_granted", "request_id": req_id}
                                )
                            _note_step_tool(tool_ref.name, tool_ref.server_name)
                            with self._tool_idempotency_scope(
                                state, tool_ref, tool_call.arguments
                            ):
                                _approved_result = await self._mcp_client.call_tool(
                                    server_id=tool_ref.server_id,
                                    tool_name=tool_ref.name,
                                    arguments=tool_call.arguments,
                                    tenant_ctx=tenant_ctx,
                                )
                            raw_output = (
                                _approved_result.output
                                if _approved_result.success
                                else str(_approved_result.error)
                            )
                            if _approved_result.success and (
                                getattr(_approved_result, "stale", False) is True
                            ):
                                # a02-F030-04: cached while the circuit is open.
                                from app.mcp.client import with_stale_notice

                                raw_output = with_stale_notice(
                                    _approved_result,
                                    self._sanitize_tool_raw_output(raw_output),
                                )
                            raw_output_sanitized = False
                            if state.steps:
                                # GRD-1: the approved call is this step's evidence
                                # (the final-answer grounding gates check against
                                # step tool calls). It was never recorded, so the
                                # high-risk answer had nothing to be grounded in.
                                state.steps[-1].tool_calls.append(
                                    {
                                        "tool_name": tool_ref.name,
                                        "server_id": tool_ref.server_id,
                                        "success": bool(_approved_result.success),
                                        "error": self._sanitize_tool_raw_output(
                                            _approved_result.error or ""
                                        ),
                                        "output": (
                                            self._sanitize_tool_raw_output(
                                                _approved_result.output
                                            )[:1000]
                                            if _approved_result.output
                                            else ""
                                        ),
                                        "approved": True,
                                        "stale": getattr(_approved_result, "stale", False) is True,
                                    }
                                )
                            record_tool_call(
                                tool_ref.name,
                                tool_ref.server_id,
                                "success" if _approved_result.success else "failed",
                                time.monotonic() - tool_call_started,
                            )
                            await self._record_tool_reliability(
                                tenant_ctx,
                                tool_ref.name,
                                success=bool(_approved_result.success),
                                started=tool_call_started,
                                error=_approved_result.error,
                            )
                            if _approved_result.success:
                                _rb_executed = {
                                    "tool": tool_ref.name,
                                    "server_id": tool_ref.server_id,
                                    "output": _approved_result.output,
                                }
                    else:
                        # V4: Validate arguments against JSON schema before MCP dispatch
                        from app.agent.tool_calls import validate_tool_arguments as _validate_args

                        _tool_schema = getattr(tool_ref, "input_schema", None) or {}
                        _arg_errors_v4 = _validate_args(tool_call.arguments or {}, _tool_schema)
                        if _arg_errors_v4:
                            _arg_error_msg = (
                                f"[ARGUMENT VALIDATION FAILED] Tool '{tool_call.tool}' "
                                f"called with invalid arguments:\n"
                                + "\n".join(f"  - {e}" for e in _arg_errors_v4)
                                + "\nPlease retry with correct arguments from the tool schema."
                            )
                            raw_output = self._sanitize_tool_raw_output(_arg_error_msg)
                            raw_output_sanitized = True
                            await self._emit(
                                {
                                    "type": "tool_call_failed",
                                    "tool": tool_call.tool,
                                    "error": _arg_error_msg[:300],
                                }
                            )
                            record_tool_call(
                                tool_call.tool,
                                getattr(tool_ref, "server_id", "unknown"),
                                "arg_validation_failed",
                                time.monotonic() - tool_call_started,
                            )
                        else:
                            # V5: Placeholder argument guard — prevent LLM-generated
                            # placeholder values (e.g. "your_organization/your_repository")
                            # from reaching real MCP servers.
                            _ph_hits = [
                                f"{k}={v!r}"
                                for k, v in (tool_call.arguments or {}).items()
                                if isinstance(v, str)
                                and any(p in v.lower() for p in _PLACEHOLDER_ARG_PATTERNS)
                            ]
                            if _ph_hits:
                                _ph_msg = (
                                    f"[PLACEHOLDER ARGUMENTS DETECTED] Tool '{tool_call.tool}' "
                                    f"was called with generic placeholder values: "
                                    f"{', '.join(_ph_hits)}. "
                                    "Please use real values from the goal context or "
                                    "user-provided configuration instead of template placeholders."
                                )
                                raw_output = self._sanitize_tool_raw_output(_ph_msg)
                                raw_output_sanitized = True
                                await self._emit(
                                    {
                                        "type": "tool_call_failed",
                                        "tool": tool_call.tool,
                                        "error": _ph_msg[:300],
                                    }
                                )
                                record_tool_call(
                                    tool_call.tool,
                                    getattr(tool_ref, "server_id", "unknown"),
                                    "placeholder_args",
                                    time.monotonic() - tool_call_started,
                                )
                            else:
                                # No placeholders — dispatch to MCP
                                try:
                                    with self._tracer.start_as_current_span(
                                        "agentverse.tool.call"
                                    ) as span:
                                        span.set_attribute(
                                            "tool.name",
                                            tool_call.tool if hasattr(tool_call, "tool") else "",
                                        )
                                        _note_step_tool(tool_ref.name, tool_ref.server_name)
                                        with self._tool_idempotency_scope(
                                            state, tool_ref, tool_call.arguments
                                        ):
                                            result = await self._mcp_client.call_tool(
                                                server_id=tool_ref.server_id,
                                                tool_name=tool_ref.name,
                                                arguments=tool_call.arguments,
                                                tenant_ctx=tenant_ctx,
                                            )
                                except Exception as _dispatch_exc:
                                    record_tool_call(
                                        tool_ref.name,
                                        tool_ref.server_id,
                                        "failed",
                                        time.monotonic() - tool_call_started,
                                    )
                                    await self._record_tool_reliability(
                                        tenant_ctx,
                                        tool_ref.name,
                                        success=False,
                                        started=tool_call_started,
                                        error=_dispatch_exc,
                                    )
                                    raise
                                # Apply PII check to raw tool output (H3 fix: result is ToolCallResult not dict)  # noqa: E501
                                raw_output_text = ""
                                if isinstance(result.output, dict):
                                    raw_output_text = str(
                                        result.output.get("content")
                                        or result.output.get("result")
                                        or ""
                                    )
                                elif isinstance(result.output, str):
                                    raw_output_text = result.output[:500]
                                if self._guardrail_checker and raw_output_text:
                                    pii_issues = self._guardrail_checker.check_output(
                                        output=raw_output_text
                                    )
                                    if pii_issues:
                                        await self._emit(
                                            {
                                                "type": "pii_redacted",
                                                "tool": getattr(tool_call, "tool", "")
                                                if tool_call
                                                else "",
                                                "issues": pii_issues,
                                            }
                                        )
                                        if self._audit_log is not None:
                                            with contextlib.suppress(Exception):
                                                self._audit_log.record(
                                                    AuditEvent(
                                                        goal_id=state.goal_id,
                                                        tool_name="guardrail_checker",
                                                        action_level=ActionLevel.ALLOW_LOG,
                                                        outcome="pii_redacted",
                                                        step_id=state.steps[-1].step_id
                                                        if state.steps
                                                        else "",
                                                        api_key_id=getattr(
                                                            tenant_ctx, "api_key_id", None
                                                        )
                                                        or "",
                                                        note=f"issues_count={len(pii_issues)} step={step[:100]}",  # noqa: E501
                                                    ),
                                                    tenant_ctx=tenant_ctx,
                                                )
                                raw_result_output = self._sanitize_tool_raw_output(result.output)
                                raw_result_error = self._sanitize_tool_raw_output(result.error)

                                # Guardrail check: tool_output (Guardrails 2.0).
                                #
                                # This evaluated the result and discarded it outright — no
                                # `.get("blocked")`/redacted-content handling at all, unlike
                                # every other enforced layer in this file (STEP above,
                                # TOOL_ARGS below) and the equivalent TOOL_OUTPUT check in
                                # rag_mixin.py's RAG-retrieval path. A tool output containing
                                # a genuine violation (a secret, PHI, an injection payload)
                                # sailed straight into state.steps / the verifier / the SSE
                                # stream regardless of what the engine said. Now actually acts
                                # on the verdict, mirroring rag_mixin.py's block-or-redact.
                                if (
                                    _GUARDRAILS_AVAILABLE
                                    and guardrails_engine is not None
                                    and tenant_ctx
                                ):
                                    try:
                                        _g2_out_preview = (
                                            str(raw_result_output)[:500]
                                            if raw_result_output
                                            else ""
                                        )
                                        _g2_out_result = await guardrails_engine.evaluate(
                                            content=_g2_out_preview,
                                            layer=GuardrailLayer.TOOL_OUTPUT,
                                            tenant_id=tenant_ctx.tenant_id,
                                            goal_id=getattr(state, "goal_id", None),
                                        )
                                        if _g2_out_result.get("blocked"):
                                            raw_result_output = "[redacted by guardrail policy]"
                                        elif _g2_out_result.get("redacted_content"):
                                            raw_result_output = _g2_out_result["redacted_content"]
                                    except Exception:
                                        pass  # Guardrail errors must never break execution

                                # ── Indirect injection scan on tool output ──────────────
                                # External tool results (Confluence, web, email) may contain
                                # adversarial text designed to hijack the agent (tool poisoning).
                                if result.success and raw_result_output:
                                    try:
                                        from app.agent.exfil_guard import (
                                            check_tool_output_for_injection,
                                        )

                                        _injection_warning = check_tool_output_for_injection(
                                            tool_ref.name, raw_result_output
                                        )
                                        if _injection_warning:
                                            self._logger.warning(
                                                "indirect_injection_detected",
                                                tool=tool_ref.name,
                                                warning=_injection_warning[:120],
                                            )
                                            raw_result_output = (
                                                _injection_warning + "\n\n" + raw_result_output
                                            )
                                    except Exception:
                                        pass  # injection scan must never block execution

                                # a02-F030-04: a cached result served while the
                                # connector's circuit is open is not live data.
                                _stale = getattr(result, "stale", False) is True
                                if result.success and _stale:
                                    from app.mcp.client import with_stale_notice

                                    raw_result_output = with_stale_notice(
                                        result, raw_result_output
                                    )

                                # ── C4 Fix: Populate StepResult.tool_calls ─────────────
                                # This allows the verifier's [TOOL FAILED] markers to fire.
                                if state.steps:
                                    state.steps[-1].tool_calls.append(
                                        {
                                            "tool_name": tool_ref.name,
                                            "server_id": tool_ref.server_id,
                                            "success": result.success,
                                            "error": result.error or "",
                                            "output": (
                                                str(result.output)[:300]
                                                if result.output
                                                else ""
                                            ),
                                            "stale": _stale,
                                        }
                                    )

                                # ── H3 Fix: PII check on ToolCallResult (not dict) ──────
                                raw_output_text = ""
                                if isinstance(result.output, dict):
                                    raw_output_text = str(
                                        result.output.get("content")
                                        or result.output.get("result")
                                        or ""
                                    )
                                elif isinstance(result.output, str):
                                    raw_output_text = result.output[:500]
                                await self._emit(
                                    {
                                        "type": "tool_call_complete",
                                        "tool": tool_ref.name,
                                        "server_id": tool_ref.server_id,
                                        "success": result.success,
                                        "stale": _stale,
                                        "output": self._sanitize_tool_event_value(result.output),
                                        "error": self._sanitize_tool_event_value(result.error),
                                        # tool_output preserves the raw structured dict for result_artifacts.py  # noqa: E501
                                        # without truncation so downstream consumers can access full data.  # noqa: E501
                                        "tool_output": result.output
                                        if isinstance(result.output, dict)
                                        else None,
                                    }
                                )
                                # Check for artifact capture (RPA screenshot etc.)
                                # result is always ToolCallResult — use getattr not dict access
                                _artifact_uri: str = getattr(result, "artifact_url", "") or ""
                                _artifact_name: str = getattr(result, "artifact_name", "") or ""
                                if _artifact_uri and not _artifact_uri.startswith("data:"):
                                    await self._emit(
                                        {
                                            "type": "artifact_captured",
                                            "artifact_type": "screenshot",
                                            "artifact_url": _artifact_uri,
                                            "artifact_name": _artifact_name,
                                            "tool": tool_ref.name,
                                        }
                                    )
                                record_tool_call(
                                    tool_ref.name,
                                    tool_ref.server_id,
                                    "success" if result.success else "failed",
                                    time.monotonic() - tool_call_started,
                                )
                                await self._record_tool_reliability(
                                    tenant_ctx,
                                    tool_ref.name,
                                    success=bool(result.success),
                                    started=tool_call_started,
                                    error=result.error,
                                )
                                if result.success:
                                    _rb_executed = {
                                        "tool": tool_ref.name,
                                        "server_id": tool_ref.server_id,
                                        "output": result.output,
                                    }
                                raw_output = (
                                    raw_result_output if result.success else raw_result_error
                                )
                                # Surface the SENT content (e.g. the brief in a telegram
                                # send) so the verifier can confirm the deliverable — the
                                # tool result is only a receipt ({'ok': True, ...}).
                                if tool_call is not None:
                                    from app.agent.nodes._helpers import surface_delivered_content

                                    raw_output = surface_delivered_content(
                                        raw_output,
                                        tool_call.tool,
                                        tool_call.arguments,
                                        result.success,
                                    )
                                raw_output_sanitized = True

        # OI-1: a side-effecting call that succeeded is now done for this goal.
        if _sfx_fp is not None and _rb_executed is not None:
            await self._record_executed_call(
                state,
                tenant_ctx,
                _sfx_fp,
                tool_ref=_sfx_tool_ref,
                arguments=tool_call.arguments if tool_call is not None else None,
                output=_rb_executed.get("output"),
            )

        # ── Strategy B: parallel tool calls ─────────────────────────────────────
        # When the resolved strategy is PARALLEL and this turn produced more than
        # one structured tool call, dispatch the ADDITIONAL calls concurrently (the
        # first was handled by the full path above). Safety-gated helper; only
        # active for models whose profile opts into parallel tool calls, so the
        # default single-call path is completely unaffected.
        _strategy_b = state.context.get("_execution_strategy")
        _tool_mode_b = getattr(getattr(_strategy_b, "tool_mode", None), "value", "single")
        if (
            _tool_mode_b == "parallel"
            and _structured_tcs
            and len(_structured_tcs) > 1
            and tool_call is not None
        ):
            try:
                _extra_outputs = await self._dispatch_parallel_extra_tool_calls(
                    _structured_tcs[1:],
                    step,
                    state,
                    tenant_ctx,
                    locals().get("_allowed_tools_set") or set(),
                    policy_checked_tool=_step_policy_tool,
                )
                for _en, _eo in _extra_outputs:
                    raw_output = f"{raw_output or ''}\n\n[parallel tool: {_en}]\n{_eo}"
                raw_output_sanitized = True
                # P5 adaptivity: record whether parallel tool calls actually worked
                # for this executor model, so the engine can learn (up/down) whether
                # to keep using PARALLEL for it. Only sampled when the model really
                # emitted multiple calls (the relevant signal).
                _cap_tracker_b = getattr(self, "_capability_tracker", None)
                if _cap_tracker_b is not None and _extra_outputs:
                    _parallel_ok = all(
                        "[error" not in _o.lower() and "requires approval" not in _o.lower()
                        for _, _o in _extra_outputs
                    )
                    _exec_model_b = self._routed_model("execution", self._executor)
                    with contextlib.suppress(Exception):
                        await _cap_tracker_b.record(
                            _exec_model_b,
                            ok=_parallel_ok,
                            tenant_id=getattr(tenant_ctx, "tenant_id", None),
                            kind="parallel",
                        )
            except PermissionError:
                raise  # a refused extra call fails the step like the primary one
            except Exception as _pb_exc:  # pragma: no cover - defensive
                self._logger.warning("parallel_tool_dispatch_failed", error=str(_pb_exc)[:120])

        # 9. Result processor / graph sanitizer — redact secrets, truncate
        if not raw_output_sanitized:
            raw_output = self._sanitize_tool_raw_output(raw_output)

        # Check output for data leakage — redact only the genuine PII spans so a
        # single hit (e.g. a stray number) never destroys an otherwise-valid answer.
        if self._guardrail_checker is not None:
            _redactor = getattr(self._guardrail_checker, "redact_output", None)
            output_issues: list[str] = []
            _res = _redactor(output=raw_output) if callable(_redactor) else None
            if isinstance(_res, tuple) and len(_res) == 2 and isinstance(_res[1], list):
                # span redaction available (real GuardrailChecker)
                raw_output, output_issues = _res
            else:  # older checker without span redaction
                _co = self._guardrail_checker.check_output(output=raw_output)
                output_issues = _co if isinstance(_co, list) else []
                if output_issues:
                    raw_output = f"[Output redacted by guardrails: {'; '.join(output_issues)}]"
            if output_issues:
                await self._emit(
                    {"type": "pii_redacted", "issues": output_issues, "scope": "final_output"}
                )

        # GuardrailEngine v2: scan output for PII/secrets/cloud-destruction patterns
        _guardrail_engine_v2_out = (
            getattr(self._app_state, "guardrail_engine", None) if self._app_state else None
        )
        if _guardrail_engine_v2_out is not None and raw_output:
            try:
                from app.intelligence.guardrail_engine import GuardrailContext as _GCtxOut

                _ge_out_ctx = _GCtxOut(
                    tenant_id=tenant_ctx.tenant_id if tenant_ctx else "",
                    goal_id=state.goal_id or "",
                    agent_id=self._agent_id or "",
                    domain=getattr(tenant_ctx, "domain_context", "general")
                    if tenant_ctx
                    else "general",
                )
                _ge_out_result = await _guardrail_engine_v2_out.evaluate_tool_output(
                    tool_name=tool_name,
                    output=str(raw_output),
                    context=_ge_out_ctx,
                )
                if _ge_out_result.redacted_content:
                    raw_output = _ge_out_result.redacted_content
            except Exception as _ge_out_exc:
                self._logger.warning(
                    "guardrail_engine_v2_output_check_failed", error=str(_ge_out_exc)
                )

        # P8-1: the TENANT's own output rules (guardrails_v2, ``tool_output``
        # layer) on this step's output — LLM-only steps included, not just tool
        # results — before it is recorded, streamed and served as the answer. A
        # tenant PII ``redact`` rule used to apply nowhere on this path (and on a
        # worker its rules were never even loaded).
        if _GUARDRAILS_AVAILABLE and guardrails_engine is not None and tenant_ctx and raw_output:
            raw_output = await self._screen_step_output(step, str(raw_output), state, tenant_ctx)

        # 10. Record rollback point — only for a tool call that actually ran and
        # succeeded (nothing external to undo otherwise). The undo record carries
        # the tool's OUTPUT, because inverses need the ids it returned (issue id,
        # message ts, ...); registering the input args alone made every built-in
        # inverse skip while rollback still reported success. It also carries the
        # goal's real tenant context instead of a fabricated "rollback" tenant.
        if self._rollback_engine is not None and _rb_executed is not None:
            _rb_names = [
                (tool_call.tool if tool_call is not None else "") or "",
                str(_rb_executed["tool"]),
                tool_name,
            ]
            self._rollback_engine.register_tool_call(
                action=step,
                tool_names=list(dict.fromkeys(n for n in _rb_names if n)),
                arguments=dict(tool_call.arguments or {}) if tool_call is not None else {},
                output=_rb_executed["output"],
                server_id=str(_rb_executed["server_id"] or ""),
                tenant_ctx=tenant_ctx,
                mcp_client=self._mcp_client,
            )

        # 11. Decision trace for explainability — real LLM output (Task 6)
        _reasoning_text = raw_output[:500] if raw_output else "No output"
        if tool_call is not None and getattr(tool_call, "tool", None):
            _reasoning_text = f"Used tool '{tool_call.tool}': {raw_output[:300]}"
        trace = DecisionTrace(
            action=step,
            reasoning=_reasoning_text,
            evidence=[raw_output[:300]],
            alternatives=[],
            confidence=0.8,
        )
        state.context.setdefault("decision_traces", []).append(trace.to_dict())
        # Persist decision trace to DB
        if self._db_session_factory and hasattr(trace, "trace_id"):
            import asyncio as _asyncio

            _task = _asyncio.create_task(self._persist_decision_trace(trace, state, tenant_ctx))
            _task.add_done_callback(
                lambda t: (
                    (not t.cancelled() and t.exception())
                    and self._logger.warning(
                        "decision_trace_persist_failed", error=str(t.exception())
                    )
                )
            )
            # Hold a strong reference so the GC doesn't collect the task before it finishes
            self._background_tasks.add(_task)
            _task.add_done_callback(self._background_tasks.discard)

        # 12. Audit log
        if self._audit_log is not None:
            self._audit_log.record(
                AuditEvent(
                    goal_id=state.goal_id,
                    tool_name=tool_name,
                    action_level=ActionLevel.ALLOW_LOG,
                    outcome="step_complete",
                    step_id=state.steps[-1].step_id if state.steps else "",
                    api_key_id=getattr(tenant_ctx, "api_key_id", None) or "",
                    request_id=(
                        state.context.get("request_id")
                        or state.context.get("execution_context", {}).get("request_id")
                    ),
                ),
                tenant_ctx=tenant_ctx,
            )

        # 13. Claim grounding check — verify LLM claims against tool outputs.
        # SKIP when raw_output is already a structured tool result (JSON/dict),
        # as it IS the evidence and cannot be "ungrounded" against itself.
        try:
            from app.agent.grounding import annotate_ungrounded, check_grounding

            _raw_stripped = (raw_output or "").strip()
            _is_structured_tool_output = _raw_stripped.startswith(("{", "[", "{'"))
            # Ground against ALL evidence gathered for the goal — every step's tool
            # outputs plus the retrieved KB/RAG context — not just this step's tool
            # calls. A synthesis/delivery step draws on earlier retrievals, so the
            # narrow single-step view flagged KB-sourced facts as ungrounded and
            # failed correct answers until max_iterations.
            from app.agent.nodes._helpers import collect_grounding_sources

            # Gathered evidence decides whether the check runs; the goal text is
            # added as a source so user-supplied facts count as grounded (P5-6).
            _gathered_evidence = collect_grounding_sources(state.steps, step_context or "")
            _tool_outputs_for_grounding = collect_grounding_sources(
                state.steps, step_context or "", goal=state.goal or ""
            )
            # Arithmetic in the goal / this step / earlier step outputs, recomputed
            # deterministically: a correct computed result ("391" for "17*23") is
            # grounded, a wrong one is not (B7 live open item 1). Not "gathered
            # evidence": it never decides whether the check runs.
            from app.agent.arithmetic_evidence import derive_arithmetic, render_evidence

            _arith = derive_arithmetic(
                [
                    state.goal or "",
                    step or "",
                    *(s.output for s in state.steps if getattr(s, "output", "")),
                    raw_output or "",
                ]
            )
            if _arith:
                _tool_outputs_for_grounding.append(render_evidence(_arith))
            # P0-4: high/critical-risk goals get zero ungrounded tolerance.
            _rp_ground = state.context.get("_runtime_profile")
            _risk_ground = str(
                getattr(
                    getattr(getattr(_rp_ground, "properties", None), "risk", None),
                    "value",
                    "",
                )
                or ""
            ).lower()
            # CORE-03: on a high-risk goal a step asserting concrete claims with no
            # evidence at all is ungrounded too (check_grounding's own "claims +
            # no evidence" branch); the gate used to be skipped without evidence.
            _goal_high_risk = _risk_ground in ("high", "critical") or _is_high_risk_step(
                state.goal
            )
            if (
                raw_output
                and (_gathered_evidence or _goal_high_risk)
                and not _is_structured_tool_output
            ):
                _ground_ratio = 0.0 if _risk_ground in ("high", "critical") else None
                _use_policy = False
                with contextlib.suppress(Exception):
                    from app.core.config import get_settings as _gs

                    _use_policy = bool(getattr(_gs(), "grounding_policy_enabled", False))
                if _use_policy:
                    # Richer per-claim policy: exact tiers + optional embedding
                    # paraphrase tier + calibrated abstention. Produces a
                    # GroundingResult-compatible verdict so downstream is unchanged.
                    from app.agent.grounding import GroundingResult
                    from app.agent.grounding_policy import GroundingPolicy

                    _embed_fn = None
                    if self._embedder is not None:
                        async def _embed_fn(texts: list[str]) -> list[list[float]]:
                            from app.providers.base import EmbedRequest

                            _resp = await self._embedder.embed(EmbedRequest(texts=texts))
                            return list(getattr(_resp, "embeddings", []) or [])

                    _min_ratio = 1.0 if _risk_ground in ("high", "critical") else 0.75
                    _pr = await GroundingPolicy(
                        min_grounded_ratio=_min_ratio, embed_fn=_embed_fn
                    ).evaluate(raw_output, _tool_outputs_for_grounding)
                    _ground_result = GroundingResult(
                        grounded=_pr.grounded,
                        ungrounded_claims=_pr.abstain,
                        checked_claims=len(_pr.verdicts),
                        evidence_length=0,
                    )
                else:
                    _ground_result = check_grounding(
                        output=raw_output,
                        tool_outputs=_tool_outputs_for_grounding,
                        max_ungrounded_ratio=_ground_ratio,
                    )
                if not _ground_result.grounded:
                    state.consecutive_ungrounded += 1
                    self._logger.info(
                        "grounding_failed",
                        ungrounded=_ground_result.ungrounded_claims[:3],
                        consecutive=state.consecutive_ungrounded,
                        step=step[:100],
                    )
                    raw_output = annotate_ungrounded(raw_output, _ground_result)
                    state.ungrounded_claims.extend(_ground_result.ungrounded_claims[:5])
                    from app.agent.state import StepStatus

                    if state.consecutive_ungrounded >= 2:
                        # P0-4: two consecutive ungrounded steps → fail the step and
                        # drive a replan using only evidence present in tool outputs.
                        if state.steps and hasattr(state.steps[-1], "status"):
                            state.steps[-1].status = StepStatus.FAILED
                        state.verification_feedback = (
                            "Two consecutive steps produced ungrounded claims: "
                            f"{'; '.join(_ground_result.ungrounded_claims[:5])}. "
                            "Replan using only evidence present in tool outputs."
                        )
                        await self._emit(
                            {
                                "type": "grounding_blocked",
                                "ungrounded_claims": _ground_result.ungrounded_claims[:5],
                                "consecutive": state.consecutive_ungrounded,
                                "step": step,
                            }
                        )
                    else:
                        # C4: Mark the current step as UNGROUNDED (first occurrence)
                        if state.steps and hasattr(state.steps[-1], "status"):
                            state.steps[-1].status = StepStatus.UNGROUNDED
                        await self._emit(
                            {
                                "type": "grounding_warning",
                                "ungrounded_claims": _ground_result.ungrounded_claims[:5],
                                "step": step,
                            }
                        )
                else:
                    state.consecutive_ungrounded = 0
                state.context["grounding_checked"] = True
        except Exception as exc:
            # Log but don't block execution — fail-open only on grounding check errors
            self._logger.warning("grounding_check_error", error=str(exc)[:80])

        # M12: Update session memory with step output
        try:
            _session_mem = getattr(self, "_session_memory", None)
            if _session_mem is not None and hasattr(_session_mem, "add"):
                _session_mem.add(
                    key=f"step_{len(state.steps)}",
                    value={"description": step, "output": (raw_output or "")[:500]},
                )
        except Exception:
            pass

        return raw_output

    async def _dispatch_parallel_extra_tool_calls(
        self,
        extra_tcs: list[dict[str, Any]],
        step: str,
        state: AgentState,
        tenant_ctx: TenantContext,
        allowed_tools_set: set[str],
        policy_checked_tool: str | None = None,
    ) -> list[tuple[str, str]]:
        """Strategy B — run the *additional* tool calls of one executor turn
        concurrently, each through the same gates as the primary call (see
        ``_one``). The first tool call is handled by the full primary path; this
        is only reached for models whose profile opts into parallel tool calls.

        Returns ``(tool_name, sanitized_output)`` pairs. A failure in one call
        never sinks the batch — its error is captured and the others proceed.
        """
        import asyncio as _asyncio
        import os as _os

        from app.agent.goal_action_ledger import (
            call_approval_key,
            call_fingerprint,
            replay_output,
        )
        from app.agent.tool_calls import (
            prepare_tool_arguments as _prepare_args,
        )
        from app.agent.tool_calls import (
            validate_tool_name as _validate_tn,
        )
        from app.agent.tool_risk import classify_tool_risk

        _allow_fa_write_high = (
            _os.getenv("ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH", "false").lower() == "true"
        )
        _tc_ctx = state.context.get("tool_context")

        # Tool-call budget: the extra calls count against the goal's budget like
        # the primary call (which is already recorded on the step). Slots are
        # assigned in call order before anything runs concurrently.
        _budget = int(getattr(self, "_tool_call_budget", _DEFAULT_TOOL_CALL_BUDGET))
        _used = sum(len(s.tool_calls or []) for s in state.steps)
        _remaining = None if _budget <= 0 else max(_budget - _used, 0)

        async def _deny(name: str, kind: str, reason: str, text: str) -> tuple[str, str]:
            _taint_step_cache()
            await self._emit(
                {"type": f"tool_call_blocked_by_{kind}", "tool": name, "reason": reason,
                 "parallel": True}
            )
            record_tool_call(name, kind, "denied", 0.0)
            return (name, self._sanitize_tool_raw_output(text))

        async def _one(index: int, stc: dict[str, Any]) -> tuple[str, str] | None:
            """One extra call through the SAME gates as the primary call: name
            validation, budget, argument normalisation + validation, argument
            guardrails, tool policy, per-agent permissions, grants, risk class
            (destructive denied; write_high approved by a human in supervised
            mode, refused otherwise) and the placeholder guard. A refusal is reported in the
            step output; a guardrail block or a rejected approval raises
            PermissionError, exactly as it does for the primary call."""
            name = stc.get("name") or stc.get("tool_name", "")
            args = stc.get("input") or stc.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}
            if not name:
                return None
            if _validate_tn(name, allowed_tools_set):
                _taint_step_cache()
                _ungranted = await self._deny_ungranted_call(
                    name, state, tenant_ctx, parallel=True
                )
                if _ungranted is not None:
                    return (name, self._sanitize_tool_raw_output(
                        f"Tool call denied: '{name}' is not granted to this agent "
                        f"({_ungranted}). Do not call it again."
                    ))
                return (name, f"[rejected: unknown tool '{name}']")
            if _remaining is not None and index >= _remaining:
                return await _deny(
                    name, "budget", "tool-call budget reached",
                    f"Tool call denied: '{name}' was not run — the goal's tool-call budget "
                    f"({_budget}) is spent. Answer from the information already gathered.",
                )
            tool_ref = _tc_ctx.find_tool(name) if _tc_ctx is not None else None
            if tool_ref is None:
                _taint_step_cache()
                return (name, f"[tool not found: '{name}']")
            # MCPGOV-01: normalised + validated BEFORE governance — the gates
            # below decide on exactly the arguments that are dispatched.
            _prepared = _prepare_args(args, getattr(tool_ref, "input_schema", None) or {})
            args = _prepared.arguments
            if _prepared.errors:
                _taint_step_cache()
                return (
                    tool_ref.name,
                    f"[argument validation failed: {'; '.join(_prepared.errors)}]",
                )
            await self._guard_tool_args(name, args, step, state, tenant_ctx)
            pol_denial = await self._tool_policy_gate(
                tool_name=tool_ref.name,
                step=step,
                state=state,
                tenant_ctx=tenant_ctx,
                already_checked=policy_checked_tool,
                arguments=args,
            )
            if pol_denial is not None:
                return await _deny(
                    tool_ref.name, "policy", pol_denial,
                    f"Tool call denied: '{tool_ref.name}' is blocked by tenant policy "
                    f"({pol_denial}). Do not call it again.",
                )
            perm_denial = await self._agent_permission_gate(
                state=state, tenant_ctx=tenant_ctx, tool_name=tool_ref.name, step=step
            )
            if perm_denial is not None:
                return await _deny(
                    tool_ref.name, "agent_permission", perm_denial,
                    f"Tool call denied: '{tool_ref.name}' is not permitted for this agent "
                    f"({perm_denial}). Do not call it again.",
                )
            grant = await enforce_tool_call(
                self._grant_store,
                tenant_id=tenant_ctx.tenant_id,
                agent_id=self._agent_id or "",
                tool_name=tool_ref.name,
                enabled=self._enforce_grants,
            )
            if not grant.allowed:
                await self._audit_grant_denial(tool_ref.name, str(grant.reason), state, tenant_ctx)
                return await _deny(
                    tool_ref.name, "grant", str(grant.reason),
                    f"Tool call denied: '{tool_ref.name}' is not granted to this agent "
                    f"({grant.reason}). Do not call it again.",
                )
            if grant.grant_id:
                await self._set_authorizing_grant(state, tenant_ctx, grant.grant_id)
            # Risk gate — same rules as the primary path (per-connector opt-in).
            risk = classify_tool_risk(tool_ref.name, tool_ref.server_name, args)
            eff = resolve_effective_tool_risk(
                risk,
                autonomy_mode=self._autonomy_mode,
                connector_auto_approve=bool(getattr(tool_ref, "auto_approve", False)),
                allow_fa_write_high=_allow_fa_write_high,
            )
            if eff == "destructive":
                return await _deny(
                    tool_ref.name, "risk", "destructive",
                    f"[denied: '{tool_ref.name}' is destructive]",
                )
            if risk != "read" and _checkpoint_degraded(state):
                # CORE-26: checkpoints cannot be saved — no new side effects.
                return await _deny(
                    tool_ref.name, "checkpoint", "checkpoint_degraded",
                    f"[denied: '{tool_ref.name}' not run; checkpoints could not be saved]",
                )
            # OI-1: an identical side-effecting call this goal already ran is not
            # dispatched again; its recorded result is returned.
            _fp: str | None = None
            if risk != "read":
                _fp = call_fingerprint(tool_ref.server_id, tool_ref.name, args)
                _prior = await self._goal_action_ledger(state, tenant_ctx).executed(_fp)
                if _prior is not None:
                    await self._emit(
                        {
                            "type": "tool_call_already_executed",
                            "tool": tool_ref.name,
                            "server_id": tool_ref.server_id,
                            "first_step_id": str(_prior.get("step_id") or ""),
                            "parallel": True,
                        }
                    )
                    return (tool_ref.name, self._sanitize_tool_raw_output(replay_output(_prior)))
            if eff == "write_high":
                if self._hitl_gateway is None or self._autonomy_mode != "supervised":
                    # Nobody will decide an approval here: not run, none filed.
                    return await _deny(
                        tool_ref.name, "risk", "approval required",
                        f"High-risk tool '{tool_ref.name}' requires approval "
                        "(non-supervised mode); it was not executed.",
                    )
                # Supervised: block until a human decides (raises unless APPROVED).
                await self._await_tool_approval(
                    tool_name=tool_ref.name, action=tool_ref.name, risk_level=eff,
                    state=state, tenant_ctx=tenant_ctx,
                    approval_key=call_approval_key(
                        _fp or call_fingerprint(tool_ref.server_id, tool_ref.name, args)
                    ),
                )
            _ph_hits = [
                f"{k}={v!r}"
                for k, v in args.items()
                if isinstance(v, str) and any(p in v.lower() for p in _PLACEHOLDER_ARG_PATTERNS)
            ]
            if _ph_hits:
                _taint_step_cache()
                return (
                    tool_ref.name,
                    f"[PLACEHOLDER ARGUMENTS DETECTED] '{tool_ref.name}' was called with "
                    f"placeholder values: {', '.join(_ph_hits)}; it was not executed.",
                )
            _t0 = time.monotonic()
            try:
                _note_step_tool(tool_ref.name, tool_ref.server_name)
                with self._tool_idempotency_scope(state, tool_ref, args):
                    result = await self._mcp_client.call_tool(
                        server_id=tool_ref.server_id,
                        tool_name=tool_ref.name,
                        arguments=args,
                        tenant_ctx=tenant_ctx,
                    )
            except Exception as exc:
                record_tool_call(
                    tool_ref.name, tool_ref.server_id, "failed", time.monotonic() - _t0
                )
                await self._record_tool_reliability(
                    tenant_ctx, tool_ref.name, success=False, started=_t0, error=exc
                )
                return (tool_ref.name, f"[error: {exc}]")
            record_tool_call(
                tool_ref.name,
                tool_ref.server_id,
                "success" if result.success else "failed",
                time.monotonic() - _t0,
            )
            await self._record_tool_reliability(
                tenant_ctx,
                tool_ref.name,
                success=bool(result.success),
                started=_t0,
                error=result.error,
            )
            if result.success and _fp is not None:
                await self._record_executed_call(
                    state, tenant_ctx, _fp, tool_ref=tool_ref, arguments=args,
                    output=result.output,
                )
            if result.success and self._rollback_engine is not None:
                self._rollback_engine.register_tool_call(
                    action=f"{step} [{tool_ref.name}]",
                    tool_names=list(dict.fromkeys([name, tool_ref.name])),
                    arguments=dict(args),
                    output=result.output,
                    server_id=tool_ref.server_id,
                    tenant_ctx=tenant_ctx,
                    mcp_client=self._mcp_client,
                )
            if state.steps:
                state.steps[-1].tool_calls.append(
                    {
                        "tool_name": tool_ref.name,
                        "server_id": tool_ref.server_id,
                        "success": result.success,
                        "error": result.error or "",
                        "output": str(result.output)[:300] if result.output else "",
                        "stale": getattr(result, "stale", False) is True,
                    }
                )
            await self._emit(
                {
                    "type": "tool_call_complete",
                    "tool": tool_ref.name,
                    "server_id": tool_ref.server_id,
                    "success": result.success,
                    "stale": getattr(result, "stale", False) is True,
                    "parallel": True,
                }
            )
            out = self._sanitize_tool_raw_output(
                result.output if result.success else result.error
            )
            if result.success:
                # a02-F030-04: a cached result served while the circuit is open.
                from app.mcp.client import with_stale_notice

                out = with_stale_notice(result, out)
            return (tool_ref.name, out)

        results = await _asyncio.gather(
            *[_one(i, stc) for i, stc in enumerate(extra_tcs)], return_exceptions=True
        )
        # A guardrail block or a rejected / timed-out approval fails the step,
        # exactly as it does for the primary call (never swallowed).
        for r in results:
            if isinstance(r, PermissionError):
                raise r
        return [r for r in results if isinstance(r, tuple)]

    async def _execute_step_with_cache(
        self,
        step: str,
        state: AgentState,
        tenant_ctx: TenantContext,
        *,
        prefetched: str | None = None,
        prefetched_embedding: list[float] | None = None,
    ) -> str:
        """Execute a step through the governed pipeline with the semantic cache.

        The cache is consulted INSIDE ``_execute_step_pipeline``, after every
        step-level gate (guardrails, action safety, permission matrix, policy
        engine, HITL approval) — a cached answer can never let a step skip
        governance. A hit is further re-authorised against the tool-level gates
        for the tools that produced it (see ``_serve_governed_cache_hit``).

        After a real execution the result is stored — tenant-scoped, wrapped in
        an envelope naming the agent and its tools — only when no gate refused
        anything, no human approval was involved, every tool it dispatched was
        read-only, and the output is a real result (not an error, refusal,
        approval placeholder, empty collection or reasoning text).
        """
        scope = _StepCacheScope(prefetched=prefetched, embedding=prefetched_embedding)
        token = _STEP_CACHE_SCOPE.set(scope)
        try:
            raw_output = await self._execute_step(step, state, tenant_ctx)
        finally:
            _STEP_CACHE_SCOPE.reset(token)

        if (
            self._semantic_cache is not None
            and not scope.served
            and not scope.uncacheable
            and scope.embedding
            and tenant_ctx.tenant_id
            and not _is_uncacheable_output(raw_output)
            and not _is_step_refusal(raw_output)
        ):
            with contextlib.suppress(Exception):  # write failures must never block execution
                await self._semantic_cache.store_async(
                    embedding=scope.embedding,
                    query=step,
                    response=_wrap_step_cache_entry(
                        raw_output, getattr(self, "_agent_id", None) or "", scope.tools
                    ),
                    tenant_id=tenant_ctx.tenant_id,
                )

        return raw_output

    async def _serve_governed_cache_hit(
        self,
        step: str,
        state: AgentState,
        tenant_ctx: TenantContext,
        *,
        step_approved: bool,
    ) -> str | None:
        """Return a cached answer for *step*, or None to execute it for real.

        Called from the pipeline only after every step-level gate passed. A step
        that needed a human approval, or that is high-risk, is never served (nor
        stored): its approval covers this execution, and its effects must
        happen. A hit must be this tenant's (the cache is tenant-namespaced) and
        this agent's, and every tool that produced it must still be authorised.
        Anything uncertain is a miss — the real pipeline then enforces.
        """
        scope = _STEP_CACHE_SCOPE.get()
        if scope is None or self._semantic_cache is None:
            return None
        if step_approved or _is_high_risk_step(step) or not tenant_ctx.tenant_id:
            scope.uncacheable = True
            return None
        raw = scope.prefetched
        similarity: float | None = None
        source = "batch_prefetch"
        try:
            if raw is None and self._embedder is not None:
                if scope.embedding is None:
                    from app.providers.base import EmbedRequest

                    resp = await self._embedder.embed(EmbedRequest(texts=[step]))
                    scope.embedding = resp.embeddings[0] if resp.embeddings else None
                if scope.embedding:
                    hit = await self._semantic_cache.get_similar(
                        embedding=scope.embedding, tenant_id=tenant_ctx.tenant_id
                    )
                    if hit is not None:
                        raw = hit.response
                        similarity = hit.similarity
                        source = hit.source
        except Exception as exc:
            self._logger.debug("cache_lookup_failed", error=str(exc)[:80])
            return None
        entry = _unwrap_step_cache_entry(raw)
        if entry is None:
            return None
        output, agent_id, tools = entry
        if agent_id != (getattr(self, "_agent_id", None) or ""):
            return None
        if _is_uncacheable_output(output) or _is_step_refusal(output):
            return None
        if not await self._cached_tools_still_authorized(tools, step, state, tenant_ctx):
            return None
        scope.served = True
        event: dict[str, Any] = {"type": "cache_hit", "step": step, "source": source}
        if similarity is not None:
            event["similarity"] = round(similarity, 4)
        await self._emit(event)
        return output

    async def _cached_tools_still_authorized(
        self,
        tools: dict[str, str],
        step: str,
        state: AgentState,
        tenant_ctx: TenantContext,
    ) -> bool:
        """Re-check the tool-level gates for the tools behind a cached answer.

        Side-effect free (no approval is filed, no daily quota is consumed): any
        tool that is no longer read-only, is denied or approval-gated by the
        policy engine / permission matrix / per-agent permissions, or is not
        covered by a grant makes the hit unusable. Any error fails closed.
        """
        if not tools:
            return True
        try:
            agent_id = getattr(self, "_agent_id", None)
            db = getattr(self, "_db_session_factory", None)
            agent_rules: Any = ()
            if agent_id and db is not None:
                from app.governance.agent_permissions import load_agent_permissions

                agent_rules = await load_agent_permissions(db, tenant_ctx.tenant_id, agent_id)
            scope_value = _extract_scope_value(step)
            for name, server in tools.items():
                if classify_tool_risk(name, server) != "read":
                    return False
                if self._policy_engine is not None and (
                    self._policy_engine.evaluate(tool_name=name, tenant_ctx=tenant_ctx)
                    != PolicyResult.ALLOW
                ):
                    return False
                if self._permission_matrix is not None and self._permission_matrix.check(
                    tool_name=name, tenant_ctx=tenant_ctx, scope_value=scope_value
                ) not in (ActionLevel.ALLOW, ActionLevel.ALLOW_LOG):
                    return False
                if agent_rules:
                    from app.governance.agent_permissions import resolve_level

                    level, rule, _reason = resolve_level(
                        agent_rules, name, scope_value=scope_value
                    )
                    if level is not None and (
                        level not in (ActionLevel.ALLOW, ActionLevel.ALLOW_LOG)
                        or getattr(rule, "daily_limit", None)
                        or getattr(rule, "per_goal_limit", None)
                    ):
                        return False
                decision = await enforce_tool_call(
                    self._grant_store,
                    tenant_id=tenant_ctx.tenant_id,
                    agent_id=agent_id or "",
                    tool_name=name,
                    enabled=self._enforce_grants,
                )
                if not decision.allowed:
                    return False
        except Exception as exc:
            self._logger.debug("cache_tool_reauth_failed", error=str(exc)[:120])
            return False
        return True
