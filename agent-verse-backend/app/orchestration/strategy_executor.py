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
from typing import Any

from app.coordination.patterns.common import InMemoryPatternCheckpointStore
from app.intelligence.cost_tracker import calculate_cost
from app.orchestration.strategy_context_store import StrategyGoalContextStore
from app.orchestration.strategy_contracts import StrategyCheckpoint, StrategyExecutionRequest
from app.orchestration.strategy_runner import ExecutionMetrics, StrategyRunOutput
from app.providers.base import CompletionRequest, Message

# Distributed-tier strategies with a genuinely wired execution driver below. Every other
# DISTRIBUTED strategy registered in strategy_adapters.py has real adapter logic but requires
# runtime dependencies (sandboxes, policy runtimes, coordination outboxes, memory repositories)
# that are out of scope here — those are denied at admission, not faked.
SUPPORTED_DISTRIBUTED_STRATEGIES: frozenset[str] = frozenset({"supervisor", "goal_tree", "debate"})

_DECOMPOSE_MAX_STEPS = 4


class UnsupportedStrategyError(RuntimeError):
    """A strategy has a real adapter but no wired execution driver yet (see module docstring)."""


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

    def __init__(self, *, context_store: StrategyGoalContextStore) -> None:
        self._context_store = context_store
        # Per-(tenant, goal) checkpoint stores so a retried execution within this process can
        # resume mid-flight. Not durable across process restarts — see module docstring.
        self._checkpoint_stores: dict[tuple[str, str], InMemoryPatternCheckpointStore] = {}
        self._answers: dict[str, str] = {}

    def _checkpoint_store_for(
        self, request: StrategyExecutionRequest
    ) -> InMemoryPatternCheckpointStore:
        key = (request.tenant_id, request.goal_id)
        store = self._checkpoint_stores.get(key)
        if store is None:
            store = InMemoryPatternCheckpointStore()
            self._checkpoint_stores[key] = store
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

        calls = 0
        tokens = 0
        cost_usd = 0.0

        async def complete(prompt: str) -> str:
            nonlocal calls, tokens, cost_usd
            model = getattr(context.provider, "default_model", None) or "fake-model"
            response = await context.provider.complete(
                CompletionRequest(messages=[Message(role="user", content=prompt)], model=model)
            )
            calls += 1
            tokens += response.total_tokens
            cost_usd += calculate_cost(model, response.input_tokens, response.output_tokens)
            return response.content

        runtime = create_runtime(checkpoint_store=self._checkpoint_store_for(request))

        if strategy_id in {"supervisor", "goal_tree"}:
            answer = await self._run_supervisor_like(runtime, request, context, complete, cancelled)
        else:
            answer = await self._run_debate(runtime, request, context, complete)

        return StrategyRunOutput(
            answer=answer,
            metrics=ExecutionMetrics(calls=calls, tokens=tokens, cost_usd=round(cost_usd, 6)),
            safe_rationale_summary=f"{strategy_id} strategy executed via StrategyRunner.",
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
    ) -> str:
        goal_text = context.goal_text

        async def decompose(goal: str) -> list[dict[str, Any]]:
            raw = await complete(
                "Break the following goal into 1-4 concise, independent sub-tasks.\n"
                f"Goal: {goal}\n"
                'Respond with strict JSON: {"steps": [{"id": "step-1", "summary": "..."}]}'
            )
            steps = self._parse_steps(raw, fallback_summary=goal)
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
            self._answers[ref] = answer
            return {
                "child_goal_id": f"child-{uuid.uuid4()}",
                "result_reference": ref,
                "evidence_references": (),
            }

        async def synthesize(work_items: Any) -> str:
            parts = [
                self._answers.pop(item.result_reference, "")
                for item in work_items
                if item.result_reference
            ]
            joined = "\n".join(f"- {part}" for part in parts if part)
            if not joined:
                return "No sub-task results were produced."
            return await complete(
                "Combine these sub-task results into one final answer for the goal "
                f"'{goal_text}':\n{joined}"
            )

        state, answer = await runtime.execute(
            session_id=request.tenant_id,
            execution_id=request.goal_id,
            goal=goal_text,
            decompose=decompose,
            run_child=run_child,
            synthesize=synthesize,
            cancelled=cancelled,
        )
        if state.phase != "completed" or answer is None:
            raise RuntimeError(
                f"{request.strategy_id} did not complete: "
                f"phase={state.phase} reason={state.terminal_reason}"
            )
        return answer

    async def _run_debate(
        self,
        runtime: Any,
        request: StrategyExecutionRequest,
        context: Any,
        complete: Any,
    ) -> str:
        goal_text = context.goal_text
        participant_ids = ("proposer-a", "proposer-b", "proposer-c")

        async def propose(agent_id: str) -> dict[str, Any]:
            answer = await complete(
                f"As independent reasoner '{agent_id}', propose a concise answer to: {goal_text}"
            )
            ref = f"strategy-run://{uuid.uuid4()}"
            self._answers[ref] = answer
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
        return self._answers.pop(winner.proposal_reference, winner.safe_summary)


__all__ = [
    "SUPPORTED_DISTRIBUTED_STRATEGIES",
    "DistributedStrategyExecutor",
    "UnsupportedStrategyError",
    "default_distributed_admission",
]
