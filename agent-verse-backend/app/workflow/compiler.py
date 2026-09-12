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
    WorkflowPaused,
    WorkflowRunStatus,
    WorkflowState,
)

_log = get_logger(__name__)


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


class WorkflowCompiler:
    """Compiles workflow DSL definitions into executable LangGraph graphs."""

    def __init__(
        self,
        context_resolver: ContextResolver,
        checkpointer: Any = None,
        **services: Any,
    ) -> None:
        self._ctx = context_resolver
        self._checkpointer = checkpointer or MemorySaver()
        self._services = services
        # Cache: (workflow_id, version) → CompiledWorkflow
        self._cache: dict[str, CompiledWorkflow] = {}

    def compile(self, definition: WorkflowDefinition) -> CompiledWorkflow:
        """Compile a WorkflowDefinition to a runnable graph. Uses cache."""
        cache_key = f"{definition.id}:{definition.version}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        compiled = self._compile_uncached(definition)
        self._cache[cache_key] = compiled
        return compiled

    def invalidate(self, workflow_id: str) -> None:
        """Remove all cached versions for a workflow (called on re-publish)."""
        keys_to_del = [k for k in self._cache if k.startswith(f"{workflow_id}:")]
        for k in keys_to_del:
            del self._cache[k]

    def _compile_uncached(self, definition: WorkflowDefinition) -> CompiledWorkflow:
        graph = StateGraph(WorkflowState)

        # 1. Register each step as a graph node
        for step in definition.steps:
            node_fn = self._build_node_fn(step)
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

            elif step.type == "hitl" and any(a.next for a in step.actions):
                # After the reviewer decides, route by the ACTION they took. The
                # router returns the action *id* (step_outputs[gate].action), so the
                # path map must be keyed by action id → target step — NOT by
                # ``a.next`` (that mismatched the router's return value and made
                # LangGraph KeyError, so per-action `next` never worked). An action
                # with no explicit `next` falls through to the step's normal
                # downstream; a fallback bucket keeps a missing/empty action from
                # KeyError-ing.
                downstream = self._find_downstream(step.id, definition)
                default_next: Any = downstream[0] if downstream else END
                action_targets: dict[str, Any] = {
                    a.id: (a.next or default_next) for a in step.actions
                }
                action_targets["__end__"] = END  # target for the WAITING_HITL pause
                action_targets["__default__"] = default_next
                valid_ids = {a.id for a in step.actions}

                def make_hitl_router(s: Any = step, valid: set[str] = valid_ids) -> Any:
                    async def router(state: WorkflowState) -> str:
                        if state.get("status") == WorkflowRunStatus.WAITING_HITL:
                            return "__end__"
                        out = (state.get("step_outputs") or {}).get(s.id, {})
                        action = str(out.get("action", ""))
                        return action if action in valid else "__default__"

                    return router

                graph.add_conditional_edges(step.id, make_hitl_router(), action_targets)  # type: ignore[arg-type]
            elif step.type == "hitl":
                # No per-action routing — just route to downstream steps.
                downstream = self._find_downstream(step.id, definition)
                for ds in downstream:
                    graph.add_edge(step.id, ds)
            else:
                # Standard edges: step → all steps that depend on it
                downstream = self._find_downstream(step.id, definition)
                for ds in downstream:
                    graph.add_edge(step.id, ds)

        for terminal_id in terminal_steps:
            graph.add_edge(terminal_id, END)

        compiled_graph = graph.compile(checkpointer=self._checkpointer)
        _log.info(
            "workflow_compiled",
            workflow_id=definition.id,
            version=definition.version,
            steps=len(definition.steps),
        )
        return CompiledWorkflow(compiled_graph, definition)

    def _build_node_fn(self, step: Any) -> Any:
        """Build the async node function for a step."""
        node_class = StepTypeRegistry.get(step.type)
        node = node_class(step, self._ctx, **self._services)
        run_store = self._services.get("run_store")

        async def node_fn(state: WorkflowState) -> dict[str, Any]:
            # Check operator pause before each step
            if state.get("paused_by"):
                return {"status": WorkflowRunStatus.PAUSED}

            # ── Cooperative run control (operator stop/pause/resume via the API) ──
            # Checked at every step boundary against the persisted run status.
            _rid = state.get("run_id")
            _tid = state.get("tenant_id")
            if run_store is not None and _rid and _tid and not state.get("is_test_run"):
                # RESUME: a step already completed in a prior (paused) attempt of
                # this run is not re-executed — return its persisted output so the
                # run continues from where it stopped without redoing work.
                if hasattr(run_store, "get_step_result"):
                    _prior = None
                    with contextlib.suppress(Exception):
                        _prior = await run_store.get_step_result(_tid, _rid, step.id)
                    if (
                        _prior
                        and _prior.get("status") == StepStatus.COMPLETE.value
                        and _prior.get("output") is not None
                    ):
                        return {"step_outputs": {step.id: _prior["output"]}}
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
                await self._record_step_start(run_store, state, step)

            # DSL enforcement (2.W-7): retry with backoff, per-step deadline,
            # and on_failure routing on exhaustion.
            max_attempts = max(1, getattr(step.retry, "max_attempts", 1))
            timeout_s = self._parse_step_timeout(step.timeout)
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    if timeout_s and timeout_s > 0:
                        result = await asyncio.wait_for(node.execute(state), timeout_s)
                    else:
                        result = await node.execute(state)  # type: ignore[arg-type]
                except TimeoutError:
                    last_exc = TimeoutError(
                        f"step {step.id!r} exceeded timeout {step.timeout}"
                    )
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
                            result.get("status") or StepStatus.COMPLETE,
                            (result.get("step_outputs") or {}).get(step.id),
                            result.get("error"),
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
        self, run_store: Any, state: WorkflowState, step: Any
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
            )
        except Exception as exc:  # persistence must never break execution
            _log.warning("step_start_persist_failed", step_id=step.id, error=str(exc))

    @staticmethod
    async def _record_step_finish(
        run_store: Any,
        state: WorkflowState,
        step: Any,
        status: Any,
        output: Any,
        error: str | None,
    ) -> None:
        try:
            await run_store.record_step_finish(
                run_id=state["run_id"],
                tenant_id=state["tenant_id"],
                step_id=step.id,
                status=status,
                output=output if isinstance(output, dict) else None,
                error=error,
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
            raise exc if exc is not None else RuntimeError(err)

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
