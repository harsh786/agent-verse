"""WorkflowCompiler — compiles WorkflowDefinition → LangGraph StateGraph.

One compiled graph is cached per (workflow_id, version) to avoid
re-compiling on every run. Cache is invalidated on re-publish.

The compiler:
  1. Registers each step as a LangGraph node
  2. Wires edges based on depends_on and step type
  3. Handles parallel fan-out/fan-in
  4. Sets up conditional routing
  5. Compiles with the shared Redis checkpointer
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json as _json
import os
import time
from collections import OrderedDict
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import WorkflowDefinition
from app.workflow.registry import StepTypeRegistry
from app.workflow.state import (
    StepStatus,
    WorkflowCancelled,
    WorkflowConfigurationError,
    WorkflowPaused,
    WorkflowRunStatus,
    WorkflowState,
)
from app.workflow.steps.hitl_step import classify_hitl_decision

_log = get_logger(__name__)

# State a step contributes besides its output. Persisted with the step result
# (``state_delta``) and replayed when a resumed run skips the completed step, so
# variables, foreach progress and cost/tokens survive an approval / pause / crash
# (WF-34). Run-control keys (status, error, hitl_*, paused_*) are never replayed.
_REPLAYED_STATE_KEYS = ("vars", "foreach_progress", "cost_usd", "tokens_used", "completed_branch")


def _state_delta(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    return {k: result[k] for k in _REPLAYED_STATE_KEYS if result.get(k) not in (None, {}, 0)}

# (direct approval deps, indirect approval ancestors) for one step.
_Barrier = tuple[frozenset[str], frozenset[str]]

# Run statuses at which an approval gate's router must not release anything.
_HALTED_STATUSES = frozenset(
    {
        WorkflowRunStatus.WAITING_HITL,
        WorkflowRunStatus.WAITING_TIMER,
        WorkflowRunStatus.PAUSED,
        WorkflowRunStatus.FAILED,
        WorkflowRunStatus.CANCELLED,
    }
)


def _default_cache_size() -> int:
    """Compiled-graph cache entries per process (``WORKFLOW_COMPILED_CACHE_SIZE``)."""
    try:
        return int(os.getenv("WORKFLOW_COMPILED_CACHE_SIZE", "256"))
    except ValueError:
        return 256


class CompiledWorkflow:
    """Wrapper around a compiled LangGraph graph."""

    def __init__(self, graph: Any, definition: WorkflowDefinition) -> None:
        self.graph = graph
        self.definition = definition

    async def ainvoke(
        self, state: WorkflowState, config: dict[str, Any] | None = None
    ) -> WorkflowState:
        result: Any = await self.graph.ainvoke(state, config or {})  # type: ignore[no-any-return]
        return result  # type: ignore[return-value]

    async def aupdate_state(self, config: dict[str, Any], state_update: dict[str, Any]) -> None:
        await self.graph.aupdate_state(config, state_update)

    async def aget_state(self, config: dict[str, Any]) -> Any:
        return await self.graph.aget_state(config)

    async def adelete_thread(self, thread_id: str) -> None:
        """Drop this process's checkpoint for ``thread_id`` (no-op without one)."""
        saver = getattr(self.graph, "checkpointer", None)
        deleter = getattr(saver, "adelete_thread", None)
        if deleter is not None:
            await deleter(thread_id)


def _step_timeout_error(
    step: Any, exc: BaseException, elapsed: float, deadline: Any
) -> TimeoutError:
    """The error for a TimeoutError out of a step attempt: the step deadline
    (our ``asyncio.timeout`` expired) or an inner timeout, with its cause."""
    if deadline is not None and deadline.expired():
        return TimeoutError(f"step {step.id!r} exceeded timeout {step.timeout}")
    cause = str(exc) or "an operation inside the step timed out"
    limit = f"; the {step.timeout} step deadline was not reached" if step.timeout else ""
    err = TimeoutError(
        f"step {step.id!r} failed after {elapsed:.1f}s: {cause} (inner timeout{limit})"
    )
    err.__cause__ = exc
    return err


class WorkflowCompiler:
    """Compiles workflow DSL definitions into executable LangGraph graphs."""

    def __init__(
        self,
        context_resolver: ContextResolver,
        checkpointer: Any = None,
        *,
        cache_size: int | None = None,
        **services: Any,
    ) -> None:
        self._ctx = context_resolver
        self._checkpointer = checkpointer or MemorySaver()
        self._services = services
        # Bounded LRU: (workflow_id, version, content hash) → CompiledWorkflow.
        # Every edit makes a new key, so an unbounded dict grew for the life of
        # a long-lived worker with every distinct workflow version it ran.
        self._cache: OrderedDict[str, CompiledWorkflow] = OrderedDict()
        self._cache_size = max(1, cache_size or _default_cache_size())

    def bind_services(self, **services: Any) -> None:
        """Add/replace step services after construction and drop compiled graphs.

        The runner is built FROM the compiler, so it can only be handed to step
        nodes (``sub_workflow``) afterwards. Step nodes capture services at
        compile time, hence the cache clear.
        """
        self._services.update({k: v for k, v in services.items() if v is not None})
        self._cache.clear()

    @property
    def services(self) -> dict[str, Any]:
        return dict(self._services)

    def compile(self, definition: WorkflowDefinition) -> CompiledWorkflow:
        """Compile a WorkflowDefinition to a runnable graph. Uses cache."""
        cache_key = self._cache_key(definition)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            return cached

        compiled = self._compile_uncached(definition)
        self._cache[cache_key] = compiled
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)  # evict the least recently used
        return compiled

    def invalidate(self, workflow_id: str) -> None:
        """Remove all cached versions for a workflow (called on re-publish)."""
        keys_to_del = [k for k in self._cache if k.startswith(f"{workflow_id}:")]
        for k in keys_to_del:
            del self._cache[k]

    @staticmethod
    def _cache_key(definition: WorkflowDefinition) -> str:
        """Cache key: workflow id + declared version + a content hash.

        ``id:version`` alone is not a reliable invalidation signal in practice:
        API/visual-builder-created workflows are mirrored into
        ``workflow_definitions`` with a hardcoded version ("1.0.0" — see
        ``_WorkflowStore._bridge_upsert_definition``) that is never bumped on
        update/republish, and nothing in the app calls ``WorkflowCompiler
        .invalidate()`` when a workflow is edited or published. Keying on
        ``id:version`` alone would mean the FIRST compiled graph for a workflow
        is served forever (for the life of this process) — an operator fixing a
        bug in a live workflow's steps/branches/routing would have that fix
        silently ignored by every subsequent run, including brand-new ones
        triggered well after the fix, until the process restarts. The content
        hash guarantees a definition change always produces a fresh compile
        regardless of whether ``version`` (or ``invalidate()``) is used.
        """
        payload = _json.dumps(definition.to_json(), sort_keys=True, default=str)
        digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
        return f"{definition.id}:{definition.version}:{digest}"

    def _compile_uncached(self, definition: WorkflowDefinition) -> CompiledWorkflow:
        graph = StateGraph(WorkflowState)

        # 1. Register each step as a graph node
        barriers = self._approval_barriers(definition)
        by_id = {s.id: s for s in definition.steps}
        for step in definition.steps:
            node_fn = self._build_node_fn(step, barriers.get(step.id), by_id)
            graph.add_node(step.id, node_fn)

        # 2. Find entry steps (no depends_on or all deps are parallel branches)
        entry_steps = [s for s in definition.steps if not s.depends_on]
        terminal_steps = self._find_terminal_steps(definition)

        for s in entry_steps:
            graph.add_edge(START, s.id)

        # 3. Wire edges
        for step in definition.steps:
            if step.type == "conditional":
                # Fan-out: one conditional edge per branch
                branch_map = {b.next: b.next for b in step.branches if b.next}

                def make_router(s: Any = step) -> Any:
                    async def router(state: WorkflowState) -> str:
                        out = (state.get("step_outputs") or {}).get(s.id, {})
                        return str(out.get("chosen_branch", ""))

                    return router

                graph.add_conditional_edges(step.id, make_router(), branch_map)  # type: ignore[arg-type]

            elif step.type == "hitl":
                # An approval step is a HARD BARRIER. It never gets plain edges
                # to its dependents (those fired while the approval was still
                # pending, and "reject" fell through to the same steps). The
                # router holds the run at END while WAITING_HITL; after the
                # decision it routes by it: approve → the action's ``next`` or the
                # step's dependents; reject → the declared reject branch, else END
                # (the step node marks the run FAILED); anything else → END.
                downstream = self._find_downstream(step.id, definition)
                targets = sorted({*downstream, *(a.next for a in step.actions if a.next)})

                def make_hitl_router(s: Any = step, ds: list[str] = downstream) -> Any:
                    async def router(state: WorkflowState) -> Any:
                        if state.get("status") in _HALTED_STATUSES or state.get("paused_by"):
                            return END
                        out = (state.get("step_outputs") or {}).get(s.id) or {}
                        if "action" not in out:
                            return END
                        decision = classify_hitl_decision(s, out.get("action"))
                        if decision.kind == "stop":
                            return END
                        if decision.next:
                            return decision.next
                        return list(ds) if ds else END

                    return router

                graph.add_conditional_edges(
                    step.id,
                    make_hitl_router(),
                    [*targets, END],
                )
            else:
                # Standard edges: step → all steps that depend on it
                downstream = self._find_downstream(step.id, definition)
                for ds in downstream:
                    graph.add_edge(step.id, ds)

        hitl_ids = {s.id for s in definition.steps if s.type == "hitl"}
        for terminal_id in terminal_steps:
            if terminal_id in hitl_ids:
                continue  # the approval router already routes to END
            graph.add_edge(terminal_id, END)

        compiled_graph = graph.compile(checkpointer=self._checkpointer)
        _log.info(
            "workflow_compiled",
            workflow_id=definition.id,
            version=definition.version,
            steps=len(definition.steps),
        )
        return CompiledWorkflow(compiled_graph, definition)

    @staticmethod
    def _approval_barriers(definition: WorkflowDefinition) -> dict[str, _Barrier]:
        """Per step: (direct approval deps, indirect approval ancestors).

        Only ``depends_on`` (all-of) edges carry the barrier; a
        ``depends_on_any`` step is released by the approval router's routing.
        """
        by_id = {s.id: s for s in definition.steps}
        hitl_ids = {s.id for s in definition.steps if s.type == "hitl"}
        memo: dict[str, frozenset[str]] = {}

        def ancestors(step_id: str, seen: frozenset[str]) -> frozenset[str]:
            if step_id in memo:
                return memo[step_id]
            step = by_id.get(step_id)
            if step is None or step.depends_on_any or step_id in seen:
                return frozenset()
            acc: set[str] = set()
            for dep in step.depends_on:
                if dep in hitl_ids:
                    acc.add(dep)
                acc |= ancestors(dep, seen | {step_id})
            memo[step_id] = frozenset(acc)
            return memo[step_id]

        out: dict[str, _Barrier] = {}
        for step in definition.steps:
            if step.depends_on_any:
                continue
            all_anc = ancestors(step.id, frozenset())
            if all_anc:
                direct = frozenset(d for d in step.depends_on if d in hitl_ids)
                out[step.id] = (direct, all_anc - direct)
        return out

    @staticmethod
    def _barrier_blocks(
        step_id: str, barrier: _Barrier, state: WorkflowState, by_id: dict[str, Any]
    ) -> bool:
        """True when an upstream approval has not released ``step_id``.

        A join step can be triggered by a non-approval sibling's edge while the
        approval is still pending; this guard keeps it (and everything after it)
        from running until the approval is decided and routes here.
        """
        direct, indirect = barrier
        outputs = state.get("step_outputs") or {}
        for hitl_id in direct | indirect:
            out = outputs.get(hitl_id) or {}
            if "action" not in out:
                return True  # still pending
            decision = classify_hitl_decision(by_id[hitl_id], out.get("action"))
            if decision.kind == "stop":
                return True
            if hitl_id in direct and decision.next and decision.next != step_id:
                return True  # the decision routed to a different branch
        return False

    def _build_node_fn(
        self,
        step: Any,
        barrier: _Barrier | None = None,
        by_id: dict[str, Any] | None = None,
    ) -> Any:
        """Build the async node function for a step."""
        node_class = StepTypeRegistry.get(step.type)
        node = node_class(step, self._ctx, **self._services)
        run_store = self._services.get("run_store")
        step_defs = by_id or {}

        async def node_fn(state: WorkflowState) -> dict[str, Any]:
            # Approval barrier: never run downstream of an undecided/rejected gate.
            if barrier and self._barrier_blocks(step.id, barrier, state, step_defs):
                return {}
            # Check operator pause before each step
            if state.get("paused_by"):
                # A durable timer wait suspended the run: skip without touching
                # the status, so it stays WAITING_TIMER (not PAUSED) and the
                # beat wakes it; this step runs when the run is re-dispatched.
                if str(state.get("paused_by")).startswith("wait_timer:"):
                    return {}
                return {"status": WorkflowRunStatus.PAUSED}

            # ── Cooperative run control (operator stop/pause/resume via the API) ──
            # Checked at every step boundary against the persisted run status.
            _rid = state.get("run_id")
            _tid = state.get("tenant_id")
            attempt_number = 1
            if run_store is not None and _rid and _tid and not state.get("is_test_run"):
                # RESUME: a step already completed in a prior (paused) attempt of
                # this run is not re-executed — return its persisted output so the
                # run continues from where it stopped without redoing work.
                if hasattr(run_store, "get_step_result"):
                    _prior = None
                    with contextlib.suppress(Exception):
                        _prior = await run_store.get_step_result(_tid, _rid, step.id)
                    # Any COMPLETE step is skipped — also one whose output is
                    # not a JSON object or is empty — so resuming never replays
                    # an earlier step's side effect.
                    if _prior and _prior.get("status") == StepStatus.COMPLETE.value:
                        # Replay the step's recorded state (vars, cost, ...) too —
                        # outputs alone left later steps reading defaults.
                        replay = _state_delta(_prior.get("state_delta"))
                        if _prior.get("output") is None:
                            return replay
                        return {**replay, "step_outputs": {step.id: _prior["output"]}}
                    if _prior and _prior.get("status") == StepStatus.RUNNING.value:
                        # WF-14: the step was in flight when its worker died (the
                        # run's lease keeps a live worker from being swept). Run
                        # it again as a NEW attempt; side-effecting steps reuse
                        # their deterministic idempotency key, so the receiver
                        # can recognise the repeat.
                        attempt_number = int(_prior.get("attempt_number") or 1) + 1
                        _log.warning(
                            "workflow_step_inflight_replayed",
                            run_id=_rid,
                            step_id=step.id,
                            attempt=attempt_number,
                        )
                # STOP / PAUSE: honor an operator control status set via the API.
                # Raising halts the whole run (propagates out of ainvoke) so no
                # further steps execute — the runner maps the signal to the
                # terminal/paused run status.
                if hasattr(run_store, "get_status"):
                    _cur = None
                    with contextlib.suppress(Exception):
                        _cur = await run_store.get_status(_tid, _rid)
                    if _cur == WorkflowRunStatus.CANCELLED.value:
                        raise WorkflowCancelled(str(_rid))
                    if _cur == WorkflowRunStatus.PAUSED.value:
                        raise WorkflowPaused(str(_rid))

            # Persist step-result rows when a run store is wired (skip test runs,
            # which have no persisted run row to attach to).
            persist = bool(
                run_store is not None
                and state.get("run_id")
                and state.get("tenant_id")
                and not state.get("is_test_run")
            )
            if persist:
                await self._record_step_start(
                    run_store, state, step, attempt_number=attempt_number
                )

            # DSL enforcement (2.W-7): retry with backoff, per-step deadline,
            # and on_failure routing on exhaustion.
            max_attempts = max(1, getattr(step.retry, "max_attempts", 1))
            timeout_s = self._parse_step_timeout(step.timeout)
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                deadline: Any = None
                started = time.monotonic()
                try:
                    if timeout_s and timeout_s > 0:
                        async with asyncio.timeout(timeout_s) as deadline:
                            result = await node.execute(state)  # type: ignore[arg-type]
                    else:
                        result = await node.execute(state)  # type: ignore[arg-type]
                except TimeoutError as exc:
                    # Only OUR deadline is the step timeout. A TimeoutError raised
                    # inside the step (the provider's generation timeout, an HTTP
                    # timeout) used to be reported as "exceeded timeout 180s"
                    # after 61 s; keep its real cause and the real elapsed time.
                    last_exc = _step_timeout_error(
                        step, exc, time.monotonic() - started, deadline
                    )
                except WorkflowConfigurationError as exc:
                    # A wiring problem: record the failed step and fail the run.
                    if persist:
                        await self._record_step_finish(
                            run_store, state, step, StepStatus.FAILED, None, str(exc)
                        )
                    with contextlib.suppress(Exception):
                        exc.workflow_step_id = step.id  # type: ignore[attr-defined]
                    raise
                except Exception as exc:  # routed per step.on_failure below
                    last_exc = exc
                    if not self._should_retry(step.retry, exc):
                        break
                else:
                    if persist:
                        await self._record_step_finish(
                            run_store,
                            state,
                            step,
                            self._step_status_for(result.get("status")),
                            (result.get("step_outputs") or {}).get(step.id),
                            result.get("error"),
                            state_delta=_state_delta(result),
                        )
                    return result
                if attempt < max_attempts:
                    delay = self._retry_delay(step.retry, attempt)
                    if delay > 0:
                        await asyncio.sleep(delay)

            return await self._handle_step_failure(
                run_store, state, step, last_exc, persist
            )

        node_fn.__name__ = f"step_{step.id}"
        return node_fn

    @staticmethod
    def _step_input_payload(step: Any) -> dict[str, Any]:
        """Collect the step's declared input-bearing fields (still templated).

        Different step types carry their input in different fields; gather the
        ones that are populated so the run viewer can show what each step
        consumed. Resolution of ``{{...}}`` templates happens in the caller.
        """
        payload: dict[str, Any] = {}
        generic = getattr(step, "input", None)
        if isinstance(generic, dict) and generic:
            payload.update(generic)
        # Type-specific input fields (only when set).
        if getattr(step, "prompt", ""):
            payload["prompt"] = step.prompt
        if getattr(step, "url", ""):
            payload["url"] = step.url
        if getattr(step, "request_body", None):
            payload["request_body"] = step.request_body
        if getattr(step, "var_value", ""):
            payload["var_value"] = step.var_value
        if getattr(step, "iterate_over", ""):
            payload["iterate_over"] = step.iterate_over
        return payload

    async def _record_step_start(
        self, run_store: Any, state: WorkflowState, step: Any, *, attempt_number: int = 1
    ) -> None:
        resolved_input: dict[str, Any] | None = None
        try:
            raw = self._step_input_payload(step)
            if raw:
                resolved = self._ctx.resolve_all(raw, state)
                if isinstance(resolved, dict) and resolved:
                    resolved_input = resolved
        except Exception:  # input capture is best-effort, never break the run
            resolved_input = None
        try:
            await run_store.record_step_start(
                run_id=state["run_id"],
                tenant_id=state["tenant_id"],
                step_id=step.id,
                step_type=step.type,
                step_name=getattr(step, "name", None) or None,
                resolved_input=resolved_input,
                **({"attempt_number": attempt_number} if attempt_number > 1 else {}),
            )
        except Exception as exc:  # persistence must never break execution
            _log.warning("step_start_persist_failed", step_id=step.id, error=str(exc))

    @staticmethod
    def _step_status_for(run_status: Any) -> Any:
        """The step-result status for a node that returned ``run_status``.

        A node's ``status`` key is the *run* status it asks for (``running``
        after an approval decision, ``waiting_hitl`` when it suspends, ...). A
        node that returned normally has finished its own work, so ``running``
        (or no status) means the STEP is ``complete``. Persisting ``running``
        made a decided approval look unfinished: the next resume re-entered it
        as a new suspend and created a duplicate approval, so a workflow with
        two approval gates never finished. Halting statuses are kept as-is.
        """
        if not run_status or run_status in (
            WorkflowRunStatus.RUNNING,
            WorkflowRunStatus.COMPLETE,
        ):
            return StepStatus.COMPLETE
        return run_status

    @staticmethod
    async def _record_step_finish(
        run_store: Any,
        state: WorkflowState,
        step: Any,
        status: Any,
        output: Any,
        error: str | None,
        *,
        state_delta: dict[str, Any] | None = None,
    ) -> None:
        extra: dict[str, Any] = {}
        if state_delta:
            extra["state_delta"] = state_delta
            if state_delta.get("cost_usd"):
                extra["cost_usd"] = state_delta["cost_usd"]
        try:
            await run_store.record_step_finish(
                run_id=state["run_id"],
                tenant_id=state["tenant_id"],
                step_id=step.id,
                status=status,
                # Any JSON value (an HTTP step may return an array): dropping a
                # non-object output made the step look output-less on resume.
                output=output,
                error=error,
                **extra,
            )
        except Exception as exc:
            _log.warning("step_finish_persist_failed", step_id=step.id, error=str(exc))

    # ── DSL enforcement helpers (2.W-7) ──────────────────────────────────────

    @staticmethod
    def _parse_step_timeout(timeout_str: str) -> float:
        """Parse '30s' / '5m' / '2h' / bare seconds → float seconds (0 = none)."""
        if not timeout_str:
            return 0.0
        s = timeout_str.strip()
        try:
            if s.endswith("ms"):
                return float(s[:-2]) / 1000.0
            if s.endswith("s"):
                return float(s[:-1])
            if s.endswith("m"):
                return float(s[:-1]) * 60
            if s.endswith("h"):
                return float(s[:-1]) * 3600
            return float(s)
        except ValueError:
            return 0.0

    @staticmethod
    def _should_retry(retry: Any, exc: BaseException) -> bool:
        """Honour RetryConfig.fail_on / retry_on exception-name filters."""
        name = type(exc).__name__
        fail_on = getattr(retry, "fail_on", None) or []
        if name in fail_on:
            return False
        retry_on = getattr(retry, "retry_on", None) or []
        return not (retry_on and name not in retry_on)

    @staticmethod
    def _retry_delay(retry: Any, attempt: int) -> float:
        """Backoff delay in seconds before the next attempt (1-based)."""
        base = max(0, getattr(retry, "base_delay_ms", 0)) / 1000.0
        backoff = getattr(retry, "backoff", "exponential")
        if backoff == "fixed":
            return base
        if backoff == "linear":
            return base * attempt
        return base * (2 ** (attempt - 1))

    async def _handle_step_failure(
        self,
        run_store: Any,
        state: WorkflowState,
        step: Any,
        exc: BaseException | None,
        persist: bool,
    ) -> dict[str, Any]:
        """Route a step that has exhausted its retries per step.on_failure."""
        policy = getattr(step, "on_failure", "pause")
        err = str(exc) if exc is not None else "step failed"
        outputs = state.get("step_outputs") or {}

        if policy == "skip":
            if persist:
                await self._record_step_finish(
                    run_store, state, step, StepStatus.SKIPPED, None, err
                )
            return {"step_outputs": {**outputs, step.id: {"_skipped": True, "error": err}}}

        if policy == "use_default":
            default = getattr(step, "on_failure_default", None)
            out = default if isinstance(default, dict) else {"result": default}
            if persist:
                await self._record_step_finish(
                    run_store, state, step, StepStatus.COMPLETE, out, err
                )
            return {"step_outputs": {**outputs, step.id: out}}

        # Both "pause" and "abort" record the step as failed.
        if persist:
            await self._record_step_finish(
                run_store, state, step, StepStatus.FAILED, None, err
            )

        if policy == "abort":
            failure = exc if exc is not None else RuntimeError(err)
            # Tell the runner which step failed so the run row records it.
            with contextlib.suppress(Exception):
                failure.workflow_step_id = step.id  # type: ignore[attr-defined]
            raise failure

        # Default "pause": halt the run for operator intervention. Downstream
        # nodes short-circuit on ``paused_by`` (see node_fn guard).
        return {
            "status": WorkflowRunStatus.PAUSED,
            "paused_by": f"step_failure:{step.id}",
            "error": err,
            "error_step_id": step.id,
        }

    @staticmethod
    def _find_downstream(step_id: str, definition: WorkflowDefinition) -> list[str]:
        """Find all steps that directly depend on step_id."""
        return [
            s.id
            for s in definition.steps
            if (step_id in s.depends_on and not s.depends_on_any)
            or (s.depends_on_any and step_id in s.depends_on)
        ]

    @staticmethod
    def _find_terminal_steps(definition: WorkflowDefinition) -> list[str]:
        """Steps that nothing else depends on are terminal → connect to END."""
        all_ids = {s.id for s in definition.steps}
        depended_on = {dep for s in definition.steps for dep in s.depends_on}
        # Steps that are conditional/hitl targets are also "depended on"
        branch_targets = set()
        for s in definition.steps:
            for b in s.branches:
                branch_targets.add(b.next)
            for a in s.actions:
                if a.next:
                    branch_targets.add(a.next)
        depended_on.update(branch_targets)
        return list(all_ids - depended_on)
