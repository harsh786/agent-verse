"""A real ``Executor`` for :class:`StrategyRunner` that drives durable coordination adapters.

D-1 finding: ``StrategyRunner`` was constructed at app startup with the module-default
``_default_executor``, which unconditionally raises ``RuntimeError("strategy executor is not
configured")``. Nothing in ``app/`` ever called ``StrategyRunner.run()`` either, so the entire
DISTRIBUTED execution tier was inert.

``app/orchestration/strategy_adapters.py`` registers real, genuinely-implemented adapters for
every DISTRIBUTED-tier strategy (``supervisor``, ``goal_tree``, ``debate``, ``consensus``,
``autogpt``, ``babyagi``, ``camel``, ``group_chat``, ``magentic``, ``mixture_of_agents``,
``generative_agents``, ``decentralized_swarm``, ``market_auction``, ``voyager``). Each of those
adapters' ``execute()`` coroutines is real, durable, checkpointed logic — none of them are
shadow-loggers. But each one takes *domain-specific* callback parameters (``decompose`` /
``run_child`` / ``synthesize`` for the supervisor-shaped runtimes, ``propose`` / ``critique`` /
``vote`` for debate, ``verify`` / ``judge`` for consensus, plus sandboxes / policy runtimes /
coordination outboxes / memory repositories for the rest). Supplying a *generic*, non-fake
driver for those callbacks is only honest for the adapters where the callback contract can be
satisfied purely from a goal string and an LLM provider:

* ``supervisor`` / ``goal_tree`` — both backed by ``DurableSupervisorRuntime``: decompose the
  goal into sub-tasks with the LLM, execute each sub-task with the LLM, synthesize the final
  answer with the LLM.
* ``debate`` — independent LLM proposals, LLM cross-critique, LLM vote, majority decision.
* ``voyager`` — LLM curriculum, evidence-backed tasks, and a validated, immutable skill
  published into the tenant's persistent library (``PostgresVoyagerSkillStore``; refused
  without it). High-risk goals need a persisted human approval first.

The remaining DISTRIBUTED strategies genuinely need infrastructure this change does not wire
(production sandboxes, policy runtimes, coordination outboxes, memory repositories) — faking
those callbacks would produce a strategy that "succeeds" without doing anything real. Per the
finding's instructions, those strategies are denied at admission with a clear reason
(``strategy_execution_not_implemented``) rather than faked. See
``SUPPORTED_DISTRIBUTED_STRATEGIES``.
"""

from __future__ import annotations

import json
import uuid
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from app.coordination.patterns.common import InMemoryPatternCheckpointStore
from app.intelligence.cost_tracker import calculate_cost
from app.orchestration.execution_drivers import (
    COORDINATION_PATTERN_STRATEGIES,
    STRATEGY_RUNNER_STRATEGIES,
)
from app.orchestration.strategy_context_store import StrategyGoalContextStore
from app.orchestration.strategy_contracts import StrategyCheckpoint, StrategyExecutionRequest
from app.orchestration.strategy_runner import ExecutionMetrics, StrategyRunOutput
from app.providers.base import CompletionRequest, Message

# Distributed-tier strategies with a genuinely wired execution driver below. Every other
# DISTRIBUTED strategy registered in strategy_adapters.py has real adapter logic but requires
# runtime dependencies (sandboxes, policy runtimes, coordination outboxes, memory repositories)
# that are out of scope here — those are denied at admission, not faked.
SUPPORTED_DISTRIBUTED_STRATEGIES: frozenset[str] = STRATEGY_RUNNER_STRATEGIES

_DECOMPOSE_MAX_STEPS = 4


class UnsupportedStrategyError(RuntimeError):
    """A strategy has a real adapter but no wired execution driver yet (see module docstring)."""

    reason_code = "strategy_execution_not_implemented"


class CoordinationRuntimeUnavailableError(UnsupportedStrategyError):
    """A coordination-pattern goal reached an executor with no pattern runtime wired."""

    reason_code = "coordination_runtime_unavailable"


class BudgetExceededError(RuntimeError):
    """The cost controller denied a strategy LLM call's spend."""


class StrategyGateError(RuntimeError):
    """A strategy could not pass a required gate (skill library / human approval)."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(f"{reason_code}: {message}")
        self.reason_code = reason_code


_VOYAGER_MAX_TASKS = 4
_VOYAGER_CAPABILITY = "llm_reasoning"
_DEFAULT_APPROVAL_TIMEOUT_S = 3_600.0


def _resolve(dep: Any, probe: str) -> Any:
    """``dep`` itself, or ``dep()`` when it is a zero-arg getter (lifespan swaps)."""
    if dep is not None and not hasattr(dep, probe) and callable(dep):
        return dep()
    return dep


def _provider_model(provider: Any) -> str:
    """The provider's configured model id ('' → the provider uses its own default)."""
    for attr in ("default_model", "_default_model", "model"):
        value = getattr(provider, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


def default_distributed_admission(request: StrategyExecutionRequest) -> tuple[bool, str]:
    """Admission policy for the app-wired StrategyRunner.

    Only strategies with a genuine execution driver (``SUPPORTED_DISTRIBUTED_STRATEGIES``) are
    admitted. Everything else is denied with an explicit reason code instead of silently
    failing inside the executor or, worse, fabricating a success.
    """
    if request.strategy_id in SUPPORTED_DISTRIBUTED_STRATEGIES:
        return True, "admitted"
    return False, "strategy_execution_not_implemented"


class DistributedStrategyExecutor:
    """Executor callback wired into ``app.state.strategy_runner``.

    Resolves the live goal text + tenant LLM provider for a request from
    ``StrategyGoalContextStore`` (keyed by ``request.context_snapshot_ref``), then drives the
    adapter-created runtime with LLM-backed callback implementations.
    """

    # Per-(tenant, goal) in-process checkpoint stores kept at once (LRU).
    _MAX_CHECKPOINT_STORES = 256

    def __init__(
        self,
        *,
        context_store: StrategyGoalContextStore,
        cost_controller: Callable[[], Any] | Any = None,
        pattern_bridge: Any = None,
        skill_store: Callable[[], Any] | Any = None,
        hitl_gateway: Callable[[], Any] | Any = None,
        approval_timeout_seconds: float = _DEFAULT_APPROVAL_TIMEOUT_S,
        checkpoint_db: Callable[[], Any] | None = None,
    ) -> None:
        self._context_store = context_store
        # CORE-18: a zero-arg getter returning the DB session factory (None until
        # wired). With it, runs checkpoint into strategy_run_checkpoints and a
        # redelivered goal resumes; without it (tests, no DB) the bounded
        # in-process LRU below is used.
        from app.orchestration.strategy_checkpoint_store import postgres_store_factory

        self._durable_checkpoints = postgres_store_factory(checkpoint_db)
        # Runs magentic / MoA / CAMEL / generative / swarm / auction goals on a
        # coordination session (app.coordination.pattern_runs.goal_bridge).
        self._pattern_bridge = pattern_bridge
        # Voyager's persistent, tenant-scoped skill library (PostgresVoyagerSkillStore)
        # and the HITL gateway - objects or zero-arg getters (lifespan swaps).
        self._skill_store = skill_store
        self._hitl_gateway = hitl_gateway
        self._approval_timeout = approval_timeout_seconds
        # A cost controller (or a zero-arg getter, so the lifespan's Redis-backed
        # swap is picked up). Every LLM call is charged to the goal/tenant budget.
        self._cost_controller = cost_controller
        # Fallback when no DB is wired (tests, pre-lifespan): per-(tenant, goal) in-process
        # checkpoint stores so a retried execution within this process can resume
        # mid-flight. Bounded LRU; with a DB the durable store above is used instead.
        self._checkpoint_stores: OrderedDict[
            tuple[str, str], InMemoryPatternCheckpointStore
        ] = OrderedDict()
        self._answers: dict[str, str] = {}

    def _checkpoint_store_for(self, request: StrategyExecutionRequest) -> Any:
        durable = self._durable_checkpoints(request)
        if durable is not None:
            return durable
        key = (request.tenant_id, request.goal_id)
        store = self._checkpoint_stores.get(key)
        if store is None:
            store = InMemoryPatternCheckpointStore()
            self._checkpoint_stores[key] = store
            while len(self._checkpoint_stores) > self._MAX_CHECKPOINT_STORES:
                self._checkpoint_stores.popitem(last=False)
        else:
            self._checkpoint_stores.move_to_end(key)
        return store

    async def __call__(
        self,
        request: StrategyExecutionRequest,
        create_runtime: Any,
        cancelled: Any,
        checkpoint: StrategyCheckpoint | None,
    ) -> StrategyRunOutput:
        del checkpoint  # resumption goes through the adapter's own checkpoint store, not this
        strategy_id = request.strategy_id
        if strategy_id not in SUPPORTED_DISTRIBUTED_STRATEGIES:
            raise UnsupportedStrategyError(
                f"no execution driver wired for distributed strategy: {strategy_id}"
            )
        context = self._context_store.get(request.context_snapshot_ref)
        if context is None:
            raise RuntimeError(
                "no goal context registered for "
                f"context_snapshot_ref={request.context_snapshot_ref!r}"
            )

        if strategy_id in COORDINATION_PATTERN_STRATEGIES:
            if self._pattern_bridge is None:
                raise CoordinationRuntimeUnavailableError(
                    f"no coordination pattern runtime wired for: {strategy_id}"
                )
            output: StrategyRunOutput = await self._pattern_bridge.run(request, context, cancelled)
            return output

        calls = 0
        tokens = 0
        cost_usd = 0.0

        async def complete(prompt: str) -> str:
            nonlocal calls, tokens, cost_usd
            # The provider's REAL model: this used to send model='fake-model' to real
            # providers whenever a provider exposed ``_default_model`` (all of ours)
            # rather than ``default_model``. Empty lets the provider pick its default.
            model = _provider_model(context.provider)
            from app.providers.guarded_completion import (
                complete_decision,
                generation_timeout_seconds,
            )

            response = await complete_decision(
                context.provider,
                CompletionRequest(messages=[Message(role="user", content=prompt)], model=model),
                role="strategy",
                timeout_seconds=generation_timeout_seconds(),
            )
            calls += 1
            tokens += response.total_tokens
            served_model = str(getattr(response, "model", "") or model)
            call_cost = calculate_cost(
                served_model, response.input_tokens, response.output_tokens
            )
            cost_usd += call_cost
            await self._charge(request, context, call_cost)
            return response.content

        if strategy_id == "voyager":
            skill_store = _resolve(self._skill_store, "publish")
            if skill_store is None:
                # The in-process library would lose skills on restart and hide
                # them from other replicas: refuse rather than pretend.
                raise StrategyGateError(
                    "voyager_skill_store_unavailable",
                    "voyager needs the persistent skill library",
                )
            store = self._checkpoint_store_for(request)
            runtime = create_runtime(checkpoint_store=store, skill_store=skill_store)
            answer = await self._run_voyager(
                runtime, request, context, complete, cancelled, store=store
            )
            return StrategyRunOutput(
                answer=answer,
                metrics=ExecutionMetrics(calls=calls, tokens=tokens, cost_usd=round(cost_usd, 6)),
                safe_rationale_summary="voyager strategy executed via StrategyRunner.",
            )

        store = self._checkpoint_store_for(request)
        runtime = create_runtime(checkpoint_store=store)

        if strategy_id in {"supervisor", "goal_tree"}:
            answer = await self._run_supervisor_like(
                runtime, request, context, complete, cancelled, store=store
            )
        else:
            answer = await self._run_debate(runtime, request, context, complete, store=store)

        return StrategyRunOutput(
            answer=answer,
            metrics=ExecutionMetrics(calls=calls, tokens=tokens, cost_usd=round(cost_usd, 6)),
            safe_rationale_summary=f"{strategy_id} strategy executed via StrategyRunner.",
        )

    def _resolve_cost_controller(self) -> Any:
        cc = self._cost_controller
        if cc is not None and not hasattr(cc, "check_and_record") and callable(cc):
            cc = cc()
        return cc

    async def _charge(
        self, request: StrategyExecutionRequest, context: Any, cost_usd: float
    ) -> None:
        """Charge one strategy LLM call; a denied spend stops the strategy."""
        controller = self._resolve_cost_controller()
        tenant_ctx = getattr(context, "tenant_ctx", None)
        if controller is None or tenant_ctx is None or cost_usd <= 0.0:
            return
        from app.governance.cost import llm_spend

        ok = await llm_spend(
            controller.check_and_record(
                goal_id=request.goal_id, cost_usd=cost_usd, tenant_ctx=tenant_ctx
            )
        )
        if not ok:
            raise BudgetExceededError(
                f"budget_exceeded: strategy {request.strategy_id} stopped for goal "
                f"{request.goal_id}"
            )

    @staticmethod
    def _parse_steps(raw: str, *, fallback_summary: str) -> list[dict[str, str]]:
        try:
            data = json.loads(raw)
            raw_steps = data["steps"]
            parsed = [
                {
                    "id": str(step.get("id") or f"step-{index + 1}"),
                    "summary": str(step.get("summary") or fallback_summary),
                }
                for index, step in enumerate(raw_steps)
                if isinstance(step, dict)
            ][:_DECOMPOSE_MAX_STEPS]
            if not parsed:
                raise ValueError("empty decomposition")
        except Exception:
            return [{"id": "step-1", "summary": fallback_summary}]
        seen: set[str] = set()
        deduped: list[dict[str, str]] = []
        for step in parsed:
            step_id = step["id"]
            while step_id in seen:
                step_id = f"{step_id}-{uuid.uuid4().hex[:6]}"
            seen.add(step_id)
            deduped.append({"id": step_id, "summary": step["summary"]})
        return deduped

    async def _run_supervisor_like(
        self,
        runtime: Any,
        request: StrategyExecutionRequest,
        context: Any,
        complete: Any,
        cancelled: Any,
        *,
        store: Any = None,
    ) -> str:
        goal_text = context.goal_text
        goal_approved = await self._gate_high_risk(request, context, goal_text)
        gate_failure: list[StrategyGateError] = []

        async def decompose(goal: str) -> list[dict[str, Any]]:
            raw = await complete(
                "Break the following goal into 1-4 concise, independent sub-tasks.\n"
                f"Goal: {goal}\n"
                'Respond with strict JSON: {"steps": [{"id": "step-1", "summary": "..."}]}'
            )
            steps = self._parse_steps(raw, fallback_summary=goal)
            try:
                # High-risk sub-tasks need the approval before ANY child runs.
                # A sub-task that just restates the approved goal is already approved.
                await self._gate_high_risk(
                    request,
                    context,
                    *(
                        step["summary"]
                        for step in steps
                        if not (goal_approved and step["summary"].strip() == goal.strip())
                    ),
                    steps_only=True,
                )
            except StrategyGateError as exc:
                gate_failure.append(exc)
                raise
            return [
                {
                    "work_item_id": step["id"],
                    "safe_summary": step["summary"][:2000],
                    "dependencies": [],
                }
                for step in steps
            ]

        async def run_child(item: Any) -> dict[str, Any]:
            answer = await complete(
                f"Complete this sub-task and give a concise result.\nSub-task: {item.safe_summary}"
            )
            ref = f"strategy-run://{uuid.uuid4()}"
            await self._remember_answer(store, ref, answer)
            return {
                "child_goal_id": f"child-{uuid.uuid4()}",
                "result_reference": ref,
                "evidence_references": (),
            }

        async def synthesize(work_items: Any) -> str:
            refs = [item.result_reference for item in work_items if item.result_reference]
            parts: list[str] = []
            for ref in refs:
                # Peek, not pop: a synthesis that fails is retried with the same answers.
                part = await self._peek_answer(store, ref)
                if part is None:
                    # A resumed run must not synthesize from a subset of its results.
                    raise RuntimeError("sub-task result missing for a checkpointed work item")
                parts.append(part)
            joined = "\n".join(f"- {part}" for part in parts if part)
            if not joined:
                answer = "No sub-task results were produced."
            else:
                answer = await complete(
                    "Combine these sub-task results into one final answer for the goal "
                    f"'{goal_text}':\n{joined}"
                )
            for ref in refs:
                self._answers.pop(ref, None)
            return answer

        state, answer = await runtime.execute(
            session_id=request.tenant_id,
            execution_id=request.goal_id,
            goal=goal_text,
            decompose=decompose,
            run_child=run_child,
            synthesize=synthesize,
            cancelled=cancelled,
        )
        if gate_failure:
            raise gate_failure[0]
        if state.phase != "completed" or answer is None:
            raise RuntimeError(
                f"{request.strategy_id} did not complete: "
                f"phase={state.phase} reason={state.terminal_reason}"
            )
        return answer

    async def _gate_high_risk(
        self,
        request: StrategyExecutionRequest,
        context: Any,
        *texts: str,
        steps_only: bool = False,
    ) -> bool:
        """Require a persisted approval when any of *texts* is high-risk.

        Returns True when an approval was required and granted."""
        from app.agent.nodes._helpers import _is_high_risk_step

        risky = [t for t in texts if t and _is_high_risk_step(str(t))]
        if not risky:
            return False
        what = "high-risk sub-tasks" if steps_only else "high-risk goal"
        await self._require_approval(
            request, context, f"{request.strategy_id} {what}: {'; '.join(risky)[:300]}"
        )
        return True

    @staticmethod
    async def _emit(context: Any, event: dict[str, Any]) -> None:
        callback = getattr(context, "event_callback", None)
        if callback is None:
            return
        try:
            await callback(event)
        except Exception:  # an event sink failure never decides the gate
            return

    async def _require_approval(
        self, request: StrategyExecutionRequest, context: Any, action: str
    ) -> None:
        """Persisted human approval before a high-risk run (fail closed).

        Approved -> the run continues; rejected / timed out / no gateway ->
        StrategyGateError whose reason_code (approval_rejected, approval_timed_out,
        approval_unavailable) becomes the goal's failure reason."""
        from app.governance.hitl import ApprovalStatus

        gateway = _resolve(self._hitl_gateway, "request_approval_async")
        tenant_ctx = getattr(context, "tenant_ctx", None)
        if gateway is None or tenant_ctx is None:
            raise StrategyGateError(
                "approval_unavailable", "a high-risk strategy run needs an approval gateway"
            )
        try:
            request_id = await gateway.request_approval_async(
                goal_id=request.goal_id,
                action=action,
                step_description=action,
                risk_level="high",
                tenant_ctx=tenant_ctx,
                require_persisted=True,
            )
        except Exception as exc:
            raise StrategyGateError("approval_unavailable", str(exc)) from exc
        await self._emit(
            context,
            {
                "type": "waiting_approval",
                "request_id": request_id,
                "action": action,
                "strategy_id": request.strategy_id,
            },
        )
        status = await gateway.wait_for_approval(
            request_id, tenant_ctx=tenant_ctx, timeout=self._approval_timeout
        )
        if status != ApprovalStatus.APPROVED:
            await self._emit(
                context,
                {"type": "approval_denied", "request_id": request_id, "status": str(status)},
            )
            raise StrategyGateError(
                f"approval_{str(status).lower()}", f"strategy run approval {status}"
            )
        await self._emit(context, {"type": "approval_granted", "request_id": request_id})

    async def _run_voyager(
        self,
        runtime: Any,
        request: StrategyExecutionRequest,
        context: Any,
        complete: Any,
        cancelled: Any,
        *,
        store: Any = None,
    ) -> str:
        """Curriculum -> evidence-backed tasks -> governed skill publication.

        The LLM proposes up to four concrete sub-tasks (the curriculum); each is
        carried out and its result is the task's evidence; the learned procedure
        is published as an immutable, validated skill into the tenant's
        persistent library; the results are combined into the answer. High-risk
        goal text or tasks need a persisted human approval first.

        CORE-18: the curriculum and every task result are kept with the run's
        checkpoint, so a redelivered run continues on the SAME curriculum (the
        checkpoint's ``task_index`` points into it), re-runs no finished task and
        publishes no second skill. A checkpoint whose curriculum or evidence is
        gone fails closed rather than continuing on a fresh plan.
        """
        import hashlib

        goal_text = str(context.goal_text)
        curriculum_ref = f"voyager-curriculum://{request.tenant_id}/{request.goal_id}"
        stored = await self._peek_answer(store, curriculum_ref)
        if stored is not None:
            tasks = tuple(str(task) for task in json.loads(stored))
        else:
            if await self._has_checkpoint(store, request):
                raise RuntimeError(
                    "voyager checkpoint exists but its curriculum is missing; "
                    "refusing to resume on a new plan"
                )
            raw = await complete(
                f"List 1-{_VOYAGER_MAX_TASKS} concrete sub-tasks needed to accomplish this "
                f"goal.\nGoal: {goal_text}\n"
                'Respond with strict JSON: {"steps": [{"id": "step-1", "summary": "..."}]}'
            )
            steps = self._parse_steps(raw, fallback_summary=goal_text)
            tasks = tuple(dict.fromkeys(step["summary"][:500] for step in steps))
            await self._remember_answer(store, curriculum_ref, json.dumps(list(tasks)))
        await self._gate_high_risk(request, context, goal_text, *tasks)

        async def run_task(task: str) -> dict[str, str]:
            answer = await complete(
                f"Carry out this task for the goal '{goal_text}' and report the result.\n"
                f"Task: {task}"
            )
            if not answer.strip():
                return {"evidence_ref": ""}  # no result, no evidence: the run fails
            ref = f"strategy-run://{uuid.uuid4()}"
            await self._remember_answer(store, ref, answer)
            return {"evidence_ref": ref}

        goal_key = hashlib.sha256(goal_text.strip().lower().encode()).hexdigest()[:16]
        curriculum = sorted(set(tasks))[:_VOYAGER_MAX_TASKS]

        def synthesize_skill(
            ordered: tuple[str, ...], evidence: tuple[str, ...]
        ) -> dict[str, Any]:
            version = hashlib.sha256("\n".join(ordered).encode()).hexdigest()[:12]
            return {
                "procedure_id": f"voyager-{goal_key}",
                "skill_version": f"v-{version}",
                "tool_sequence": (),
                "required_capabilities": frozenset({_VOYAGER_CAPABILITY}),
                "tool_schema_versions": {},
                "connector_ids": frozenset(),
                "policy_fingerprint": request.policy_ref,
            }

        state = await runtime.execute(
            session_id=request.tenant_id,
            execution_id=request.goal_id,
            tenant_id=request.tenant_id,
            capability_gaps=tasks,
            run_task=run_task,
            synthesize_skill=synthesize_skill,
            maximum_tasks=_VOYAGER_MAX_TASKS,
            publication_context={
                "available_tools": {},
                "allowed_capabilities": frozenset({_VOYAGER_CAPABILITY}),
                "ready_connectors": frozenset(),
                "policy_fingerprint": request.policy_ref,
                "provenance": {"goal_id": request.goal_id, "steps": curriculum},
            },
            cancelled=cancelled,
        )
        if state.phase != "completed":
            raise RuntimeError(
                f"voyager did not complete: phase={state.phase} reason={state.terminal_reason}"
            )
        self._answers.pop(curriculum_ref, None)
        results: list[str] = []
        for ref in state.evidence_refs:
            result = await self._recall_answer(store, ref, None)
            if result is None:
                # A resumed run must not answer from a subset of its evidence.
                raise RuntimeError("voyager task result missing for a checkpointed task")
            results.append(result)
        joined = "\n".join(f"- {result}" for result in results)
        return str(
            await complete(
                "Combine these task results into one final answer for the goal "
                f"'{goal_text}':\n{joined}"
            )
        )

    async def _run_debate(
        self,
        runtime: Any,
        request: StrategyExecutionRequest,
        context: Any,
        complete: Any,
        *,
        store: Any = None,
    ) -> str:
        goal_text = context.goal_text
        await self._gate_high_risk(request, context, goal_text)
        participant_ids = ("proposer-a", "proposer-b", "proposer-c")

        async def propose(agent_id: str) -> dict[str, Any]:
            answer = await complete(
                f"As independent reasoner '{agent_id}', propose a concise answer to: {goal_text}"
            )
            ref = f"strategy-run://{uuid.uuid4()}"
            await self._remember_answer(store, ref, answer)
            return {"proposal_reference": ref, "safe_summary": answer[:2000] or "(no proposal)"}

        async def critique(agent_id: str, other_agent_id: str) -> str:
            return await complete(
                f"As '{agent_id}', critique the proposal of '{other_agent_id}' "
                f"for goal: {goal_text}"
            )

        async def vote(voter: str, proposals: Any) -> str:
            valid = {proposal.agent_id for proposal in proposals} - {voter}
            options = ", ".join(sorted(valid))
            raw = await complete(
                f"As '{voter}', vote for the strongest proposal among: {options}. "
                "Respond with only the agent id, nothing else."
            )
            candidate = raw.strip().splitlines()[0].strip() if raw.strip() else ""
            if candidate not in valid:
                candidate = sorted(valid)[0]
            return candidate

        state = await runtime.execute(
            session_id=request.tenant_id,
            execution_id=request.goal_id,
            participant_ids=participant_ids,
            propose=propose,
            critique=critique,
            vote=vote,
            quorum=2,
        )
        if state.phase != "completed" or state.winner_agent_id is None:
            raise RuntimeError(f"debate did not complete: phase={state.phase}")
        winner = next(p for p in state.proposals if p.agent_id == state.winner_agent_id)
        return await self._recall_answer(
            store, winner.proposal_reference, winner.safe_summary
        )

    async def _remember_answer(self, store: Any, ref: str, answer: str) -> None:
        """Keep a sub-task / proposal answer; durably too when the store can (CORE-18).

        The work item only stores ``ref``: without the answer in Postgres a resumed
        run would synthesize from nothing.
        """
        self._answers[ref] = answer
        put = getattr(store, "put_answer", None)
        if put is not None:
            await put(ref, answer)

    async def _recall_answer(self, store: Any, ref: str, default: Any) -> Any:
        if ref in self._answers:
            return self._answers.pop(ref)
        value = await self._peek_answer(store, ref)
        return value if value is not None else default

    async def _peek_answer(self, store: Any, ref: str) -> str | None:
        """The stored answer for ``ref`` (process first, then the durable store)."""
        if ref in self._answers:
            return self._answers[ref]
        get = getattr(store, "get_answer", None)
        if get is None:
            return None
        value = await get(ref)
        return str(value) if value is not None else None

    @staticmethod
    async def _has_checkpoint(store: Any, request: StrategyExecutionRequest) -> bool:
        load = getattr(store, "load", None)
        if load is None:
            return False
        return await load(request.tenant_id, request.goal_id) is not None


__all__ = [
    "SUPPORTED_DISTRIBUTED_STRATEGIES",
    "DistributedStrategyExecutor",
    "UnsupportedStrategyError",
    "default_distributed_admission",
]
