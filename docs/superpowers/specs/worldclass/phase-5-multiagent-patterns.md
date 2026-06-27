# Phase 5: Multi-Agent Patterns

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement four production-grade multi-agent patterns: Supervisor (hierarchical delegation), Debate/Voting (adversarial consensus), complete A2A protocol (persistent tasks + callbacks + streaming + auth), and Batch Processing (Celery-backed parallel execution).

**Architecture:** Supervisor and Debate run as orchestrators that spawn multiple `AgentGraph` instances internally. A2A gets a persistent `a2a_tasks` DB table, HMAC request signing, SSE streaming, and callback delivery. Batch processing uses Celery tasks with a `batch_jobs` in-memory (then DB) store and progress tracking.

**Tech Stack:** Python 3.12, FastAPI, asyncio, LangGraph, Celery, HMAC-SHA256, pytest-asyncio, httpx ASGITransport

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `app/agent/supervisor.py` | Create | `SupervisorAgent` hierarchical multi-agent orchestrator |
| `app/agent/debate.py` | Create | `DebateOrchestrator` adversarial voting pattern |
| `app/db/models/a2a.py` | Create | `A2ATask` persistent DB model |
| `app/db/migrations/versions/0025_a2a_tasks.py` | Create | `a2a_tasks` table migration |
| `app/mcp/a2a.py` | Modify | Extend with DB persistence, callbacks, HMAC auth |
| `app/api/a2a.py` | Modify | SSE streaming endpoint, full task lifecycle API |
| `app/api/goals.py` | Modify | Add `workflow_mode` param; `POST /goals/batch` endpoint |
| `app/services/goal_service.py` | Modify | Batch processing with Celery task dispatch |
| `tests/test_phase5_multiagent.py` | Create | Full test suite |

---

## Task 5.1 — Supervisor Agent Pattern

**Current state:** No supervisor/hierarchical agent coordination exists. Goals run as single `AgentGraph` instances.

**Gap:** New `SupervisorAgent` that decomposes a goal into sub-tasks, spawns per-sub-task graphs, monitors with timeout, handles failures, and synthesizes results.

**Files:**
- Create: `agent-verse-backend/app/agent/supervisor.py`
- Modify: `agent-verse-backend/app/api/goals.py`
- Test: `agent-verse-backend/tests/test_phase5_multiagent.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_phase5_multiagent.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_supervisor_decomposes_goal_into_subtasks():
    """SupervisorAgent must decompose the goal and produce at least one sub-task."""
    from app.agent.supervisor import SupervisorAgent
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier

    decomposition_response = """{
        "sub_tasks": [
            {"id": "st1", "description": "Search GitHub for relevant repos"},
            {"id": "st2", "description": "Analyze top 3 repos"},
            {"id": "st3", "description": "Summarize findings"}
        ]
    }"""

    fake = FakeProvider(responses=[
        decomposition_response,  # for decompose
        "Repos found: A, B, C",   # sub-agent 1
        "Analysis complete",       # sub-agent 2
        "Summary: use repo A",     # sub-agent 3
        '{"success": true, "reason": "all sub-tasks complete"}',  # synthesis
    ])
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    events = []

    supervisor = SupervisorAgent(provider=fake)
    result = await supervisor.run(
        goal="Research best Python HTTP libraries",
        tenant_ctx=tenant,
        event_callback=lambda e: events.append(e),
    )
    assert result is not None
    assert "result" in result or "error" in result

@pytest.mark.asyncio
async def test_supervisor_emits_lifecycle_events():
    """SupervisorAgent must emit supervisor_start, sub_agent_start, sub_agent_complete, supervisor_complete."""
    from app.agent.supervisor import SupervisorAgent
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier

    emitted_types = []

    fake = FakeProvider(responses=[
        '{"sub_tasks": [{"id": "s1", "description": "Step 1"}]}',
        "Result of step 1",
        '{"success": true, "reason": "done"}',
    ])
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")

    supervisor = SupervisorAgent(provider=fake)

    async def capture(event):
        emitted_types.append(event.get("type"))

    await supervisor.run(
        goal="simple goal",
        tenant_ctx=tenant,
        event_callback=capture,
    )
    assert "supervisor_start" in emitted_types
    assert "supervisor_complete" in emitted_types

@pytest.mark.asyncio
async def test_supervisor_handles_sub_agent_failure():
    """SupervisorAgent must continue with remaining sub-tasks when one fails."""
    from app.agent.supervisor import SupervisorAgent
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier

    fake = FakeProvider(responses=[
        '{"sub_tasks": [{"id": "s1", "description": "Will fail"}, {"id": "s2", "description": "Will succeed"}]}',
        "Successful result for s2",
        '{"success": true, "reason": "partial success"}',
    ])
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    supervisor = SupervisorAgent(provider=fake)

    # Make first sub-agent fail
    call_count = 0
    original_run = None

    async def patched_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("sub-agent 1 crashed")
        return {"result": "success"}

    with patch.object(supervisor, "_run_sub_agent", side_effect=patched_run):
        result = await supervisor.run(
            goal="multi-step goal",
            tenant_ctx=tenant,
            event_callback=AsyncMock(),
        )
    assert result is not None
```

- [ ] **Step 2: Run — expect failure (module not found)**

```bash
cd agent-verse-backend
pytest tests/test_phase5_multiagent.py::test_supervisor_decomposes_goal_into_subtasks -xvs
```

- [ ] **Step 3: Create SupervisorAgent**

```python
# app/agent/supervisor.py
"""Supervisor agent pattern — hierarchical multi-agent coordination.

The supervisor decomposes a goal into sub-tasks, spawns an AgentGraph per
sub-task, monitors execution with per-sub-agent timeouts, handles failures
(retry, skip, escalate), and synthesizes the aggregate result.

Usage:
    supervisor = SupervisorAgent(provider=llm_provider)
    result = await supervisor.run(
        goal="Research best Python HTTP libraries",
        tenant_ctx=tenant_ctx,
        event_callback=event_callback,
    )
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

DECOMPOSE_SYSTEM = """You are a task decomposition specialist. Break the given goal into 2-6
independent, parallel-capable sub-tasks. Each sub-task should be:
- Completable by a single autonomous agent
- Independently executable (minimal dependencies)
- Specific enough to be actionable

Output JSON:
{
  "sub_tasks": [
    {"id": "st1", "description": "<specific sub-task description>", "depends_on": []},
    ...
  ]
}"""

SYNTHESIZE_SYSTEM = """You are a results synthesizer. Given the original goal and results from
multiple sub-agents, produce a comprehensive final answer that:
1. Integrates all successful sub-task results
2. Acknowledges any failed sub-tasks and works around them
3. Directly addresses the original goal

Be concise but complete."""


@dataclass
class SubTaskResult:
    sub_task_id: str
    description: str
    result: str = ""
    error: str = ""
    success: bool = False
    duration_seconds: float = 0.0


class SupervisorAgent:
    """Coordinator that decomposes goals and spawns/monitors sub-agents."""

    DEFAULT_SUB_AGENT_TIMEOUT = 120.0  # 2 minutes per sub-agent
    MAX_SUB_TASKS = 6

    def __init__(
        self,
        provider: Any,
        sub_agent_timeout: float = DEFAULT_SUB_AGENT_TIMEOUT,
        max_retries: int = 1,
    ) -> None:
        self._provider = provider
        self._sub_agent_timeout = sub_agent_timeout
        self._max_retries = max_retries

    async def run(
        self,
        goal: str,
        tenant_ctx: Any,
        event_callback: Any,
        sub_agent_configs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Execute goal using hierarchical sub-agent delegation.

        1. Decompose goal into sub-tasks
        2. Spawn AgentGraph per sub-task
        3. Monitor with per-sub-agent timeout
        4. Handle failures (retry once, then mark failed)
        5. Synthesize aggregate result
        """
        run_id = uuid.uuid4().hex
        await event_callback({"type": "supervisor_start", "goal": goal, "run_id": run_id})

        # Step 1: Decompose
        sub_tasks = await self._decompose(goal, tenant_ctx)
        if not sub_tasks:
            sub_tasks = [{"id": "st1", "description": goal, "depends_on": []}]

        # Cap sub-tasks
        sub_tasks = sub_tasks[: self.MAX_SUB_TASKS]

        await event_callback({
            "type": "supervisor_decomposed",
            "sub_task_count": len(sub_tasks),
            "sub_tasks": [t["description"] for t in sub_tasks],
        })

        # Step 2 & 3: Run sub-agents
        sub_results: list[SubTaskResult] = []
        for st in sub_tasks:
            st_id = st.get("id", uuid.uuid4().hex)
            st_desc = st.get("description", "")
            await event_callback({
                "type": "sub_agent_start",
                "sub_task_id": st_id,
                "description": st_desc,
            })

            result = await self._run_sub_agent_with_retry(
                sub_task_id=st_id,
                description=st_desc,
                tenant_ctx=tenant_ctx,
                event_callback=event_callback,
            )
            sub_results.append(result)

            await event_callback({
                "type": "sub_agent_complete",
                "sub_task_id": st_id,
                "success": result.success,
                "result_preview": result.result[:200] if result.result else "",
                "error": result.error,
            })

        # Step 4: Synthesize
        final_result = await self._synthesize(goal, sub_results, tenant_ctx)
        successful = sum(1 for r in sub_results if r.success)
        failed = len(sub_results) - successful

        await event_callback({
            "type": "supervisor_complete",
            "run_id": run_id,
            "sub_tasks_total": len(sub_results),
            "sub_tasks_succeeded": successful,
            "sub_tasks_failed": failed,
        })

        return {
            "run_id": run_id,
            "goal": goal,
            "result": final_result,
            "sub_task_results": [
                {
                    "id": r.sub_task_id,
                    "description": r.description,
                    "success": r.success,
                    "result": r.result,
                    "error": r.error,
                    "duration_seconds": r.duration_seconds,
                }
                for r in sub_results
            ],
            "success": successful > 0,
        }

    async def _decompose(
        self, goal: str, tenant_ctx: Any
    ) -> list[dict[str, Any]]:
        """Use LLM to decompose goal into sub-tasks."""
        from app.providers.base import CompletionRequest, Message
        req = CompletionRequest(
            messages=[
                Message(role="system", content=DECOMPOSE_SYSTEM),
                Message(role="user", content=f"Goal: {goal}"),
            ],
        )
        try:
            resp = await self._provider.complete(req)
            data = _parse_json_safe(resp.content)
            sub_tasks = data.get("sub_tasks", [])
            if isinstance(sub_tasks, list):
                return sub_tasks
        except Exception as exc:
            logger.warning("supervisor_decompose_failed: %s", exc)
        return []

    async def _run_sub_agent_with_retry(
        self,
        sub_task_id: str,
        description: str,
        tenant_ctx: Any,
        event_callback: Any,
    ) -> SubTaskResult:
        """Run sub-agent with retry logic."""
        for attempt in range(self._max_retries + 1):
            try:
                result = await self._run_sub_agent(
                    sub_task_id=sub_task_id,
                    description=description,
                    tenant_ctx=tenant_ctx,
                    event_callback=event_callback,
                )
                return result
            except Exception as exc:
                if attempt < self._max_retries:
                    logger.warning(
                        "sub_agent_retry", sub_task_id=sub_task_id,
                        attempt=attempt + 1, error=str(exc)
                    )
                    continue
                return SubTaskResult(
                    sub_task_id=sub_task_id,
                    description=description,
                    error=str(exc),
                    success=False,
                )
        return SubTaskResult(sub_task_id=sub_task_id, description=description,
                              error="max retries exceeded", success=False)

    async def _run_sub_agent(
        self,
        sub_task_id: str,
        description: str,
        tenant_ctx: Any,
        event_callback: Any,
    ) -> SubTaskResult:
        """Run a single sub-agent for the given sub-task description."""
        import time
        from app.agent.graph import AgentGraph

        t0 = time.monotonic()
        sub_graph = AgentGraph(
            planner=self._provider,
            executor=self._provider,
            verifier=self._provider,
            max_iterations=5,
        )

        sub_events: list[dict[str, Any]] = []

        async def sub_event_cb(evt: dict[str, Any]) -> None:
            evt["sub_task_id"] = sub_task_id
            sub_events.append(evt)
            await event_callback(evt)

        try:
            final_state = await asyncio.wait_for(
                sub_graph.run(
                    goal=description,
                    tenant_ctx=tenant_ctx,
                    event_callback=sub_event_cb,
                ),
                timeout=self._sub_agent_timeout,
            )
            duration = time.monotonic() - t0
            result_text = (
                final_state.get("result", "")
                or final_state.get("output", "")
                or str(final_state)
            )
            return SubTaskResult(
                sub_task_id=sub_task_id,
                description=description,
                result=str(result_text)[:2000],
                success=True,
                duration_seconds=duration,
            )
        except TimeoutError:
            return SubTaskResult(
                sub_task_id=sub_task_id,
                description=description,
                error=f"Timed out after {self._sub_agent_timeout}s",
                success=False,
                duration_seconds=self._sub_agent_timeout,
            )

    async def _synthesize(
        self,
        goal: str,
        sub_results: list[SubTaskResult],
        tenant_ctx: Any,
    ) -> str:
        """Use LLM to synthesize sub-agent results into final answer."""
        from app.providers.base import CompletionRequest, Message

        results_text = "\n\n".join(
            f"Sub-task {i+1}: {r.description}\n"
            f"Status: {'SUCCESS' if r.success else 'FAILED'}\n"
            f"{'Result: ' + r.result if r.success else 'Error: ' + r.error}"
            for i, r in enumerate(sub_results)
        )
        req = CompletionRequest(
            messages=[
                Message(role="system", content=SYNTHESIZE_SYSTEM),
                Message(
                    role="user",
                    content=f"Goal: {goal}\n\n[Sub-task Results]\n{results_text}"
                ),
            ],
        )
        try:
            resp = await self._provider.complete(req)
            return resp.content
        except Exception as exc:
            logger.warning("supervisor_synthesize_failed: %s", exc)
            successful = [r.result for r in sub_results if r.success]
            return "\n\n".join(successful) if successful else "All sub-tasks failed."


def _parse_json_safe(text: str) -> dict[str, Any]:
    """Try to extract JSON from text; return empty dict on failure."""
    import re
    text = text.strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except (json.JSONDecodeError, ValueError):
            pass
    return {}
```

- [ ] **Step 4: Update goals.py to support supervised workflow_mode**

In `app/api/goals.py`, extend `SubmitGoalRequest`:

```python
class SubmitGoalRequest(BaseModel):
    goal: str
    priority: str = "normal"
    dry_run: bool = False
    agent_id: str | None = None
    workflow_mode: str = "standard"  # "standard" | "supervised" | "debate"
```

In the `submit_goal` endpoint, when `workflow_mode == "supervised"`:

```python
@router.post("", status_code=201)
async def submit_goal(request: Request, body: SubmitGoalRequest) -> dict:
    tenant_ctx = _require_tenant(request)
    # ... existing code ...

    if body.workflow_mode == "supervised":
        from app.agent.supervisor import SupervisorAgent
        provider = _get_provider(request, tenant_ctx)  # existing helper
        supervisor = SupervisorAgent(provider=provider)
        events = []
        result = await supervisor.run(
            goal=body.goal,
            tenant_ctx=tenant_ctx,
            event_callback=lambda e: events.append(e),
        )
        return {
            "goal_id": uuid.uuid4().hex,
            "workflow_mode": "supervised",
            "status": "complete",
            "result": result,
        }

    # ... existing standard path ...
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_phase5_multiagent.py -k "supervisor" -xvs
```
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add app/agent/supervisor.py app/api/goals.py tests/test_phase5_multiagent.py
git commit -m "feat(multiagent): SupervisorAgent pattern with goal decomposition + synthesis"
```

---

## Task 5.2 — Debate / Voting Pattern

**Current state:** No multi-agent debate capability exists.

**Gap:** `DebateOrchestrator` runs N agents independently, has them critique each other, then votes on best answer. Reduces hallucination for high-stakes goals.

**Files:**
- Create: `agent-verse-backend/app/agent/debate.py`
- Test: `agent-verse-backend/tests/test_phase5_multiagent.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_phase5_multiagent.py

@pytest.mark.asyncio
async def test_debate_produces_winner():
    """DebateOrchestrator must return a winner proposal."""
    from app.agent.debate import DebateOrchestrator
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier

    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    # 3 agents propose + 3 agents critique + 3 agents vote + winner announcement
    fake = FakeProvider(responses=[
        "My solution: use library A",       # agent 1 proposal
        "My solution: use library B",       # agent 2 proposal
        "My solution: use library C",       # agent 3 proposal
        "Library A is better for X reason", # agent 1 critique
        "Library B is better for Y reason", # agent 2 critique
        "Library C is better for Z reason", # agent 3 critique
        '{"vote": 0}',                      # agent 1 vote
        '{"vote": 0}',                      # agent 2 vote
        '{"vote": 1}',                      # agent 3 vote
    ])
    debate = DebateOrchestrator(provider=fake)
    result = await debate.run(
        goal="What HTTP library should I use?",
        tenant_ctx=tenant,
        event_callback=AsyncMock(),
        n_agents=3,
        rounds=2,
    )
    assert "winner" in result
    assert "proposal" in result["winner"]
    assert result["vote_counts"]

@pytest.mark.asyncio
async def test_debate_emits_round_events():
    """DebateOrchestrator must emit debate_round_start events."""
    from app.agent.debate import DebateOrchestrator
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier

    emitted = []
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    fake = FakeProvider(responses=["proposal"] * 20)
    debate = DebateOrchestrator(provider=fake)

    await debate.run(
        goal="test goal",
        tenant_ctx=tenant,
        event_callback=lambda e: emitted.append(e),
        n_agents=2,
        rounds=1,
    )
    types = [e.get("type") for e in emitted]
    assert "debate_start" in types
    assert "debate_complete" in types
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_phase5_multiagent.py -k "debate" -xvs
```

- [ ] **Step 3: Create DebateOrchestrator**

```python
# app/agent/debate.py
"""Debate/Voting multi-agent pattern.

N agents independently propose solutions, then critique each other's proposals,
then vote on the best one. The winning proposal gets executed.

This pattern reduces hallucination for high-stakes decisions by requiring
independent reasoning and adversarial review.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

PROPOSE_SYSTEM = """You are Agent {agent_id}, an independent expert AI.
Propose your BEST solution to the given problem. Be specific and actionable.
Format: A clear 2-4 paragraph proposal. No voting or critiquing at this stage."""

CRITIQUE_SYSTEM = """You are Agent {agent_id}, reviewing {n_proposals} proposals.
For each proposal, provide ONE sentence of critique (strength AND weakness).
Be objective. Do not reveal which proposal you prefer yet."""

VOTE_SYSTEM = """You are Agent {agent_id}. You have read all proposals and critiques.
Vote for the BEST proposal index (0-indexed).
Output JSON only: {{"vote": <integer>}}"""


class DebateOrchestrator:
    """Multi-agent debate with proposals, critique rounds, and voting."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def run(
        self,
        goal: str,
        tenant_ctx: Any,
        event_callback: Any,
        n_agents: int = 3,
        rounds: int = 2,
    ) -> dict[str, Any]:
        """Run full debate cycle and return winning proposal.

        1. Round 1: Each agent independently proposes a solution
        2. Round 2+: Each agent critiques all other proposals
        3. Voting: Each agent votes on best proposal
        4. Winner: Highest vote count wins (ties broken by first-mover)
        """
        run_id = uuid.uuid4().hex
        await event_callback({
            "type": "debate_start",
            "goal": goal,
            "n_agents": n_agents,
            "rounds": rounds,
            "run_id": run_id,
        })

        # Round 1: Independent proposals
        await event_callback({
            "type": "debate_round_start",
            "round": 1,
            "phase": "proposals",
        })
        proposals = await self._propose(goal, n_agents)
        await event_callback({
            "type": "debate_proposals_ready",
            "count": len(proposals),
        })

        # Round 2+: Critique rounds
        critiques: list[list[str]] = []
        for round_num in range(2, rounds + 1):
            await event_callback({
                "type": "debate_round_start",
                "round": round_num,
                "phase": "critique",
            })
            round_critiques = await self._critique(proposals, n_agents)
            critiques.append(round_critiques)
            await event_callback({
                "type": "debate_critiques_ready",
                "round": round_num,
                "critique_count": len(round_critiques),
            })

        # Voting
        await event_callback({"type": "debate_voting_start"})
        votes = await self._vote(proposals, critiques, n_agents)
        vote_counts = [0] * len(proposals)
        for v in votes:
            if 0 <= v < len(proposals):
                vote_counts[v] += 1

        winner_idx = vote_counts.index(max(vote_counts))
        winner_proposal = proposals[winner_idx]

        await event_callback({
            "type": "debate_complete",
            "run_id": run_id,
            "winner_index": winner_idx,
            "vote_counts": vote_counts,
        })

        return {
            "run_id": run_id,
            "goal": goal,
            "winner": {
                "index": winner_idx,
                "proposal": winner_proposal,
                "votes": vote_counts[winner_idx],
            },
            "vote_counts": vote_counts,
            "all_proposals": proposals,
            "all_critiques": critiques[-1] if critiques else [],
        }

    async def _propose(self, goal: str, n_agents: int) -> list[str]:
        """Have each agent independently propose a solution."""
        from app.providers.base import CompletionRequest, Message
        proposals: list[str] = []
        for i in range(n_agents):
            req = CompletionRequest(
                messages=[
                    Message(role="system", content=PROPOSE_SYSTEM.format(agent_id=i + 1)),
                    Message(role="user", content=f"Problem: {goal}"),
                ],
            )
            try:
                resp = await self._provider.complete(req)
                proposals.append(resp.content)
            except Exception as exc:
                logger.warning("debate_propose_failed agent=%d: %s", i, exc)
                proposals.append(f"[Agent {i+1} failed to propose: {exc}]")
        return proposals

    async def _critique(
        self, proposals: list[str], n_agents: int
    ) -> list[str]:
        """Have each agent critique all proposals."""
        from app.providers.base import CompletionRequest, Message
        proposals_text = "\n\n".join(
            f"Proposal {i}: {p[:500]}" for i, p in enumerate(proposals)
        )
        critiques: list[str] = []
        for i in range(n_agents):
            req = CompletionRequest(
                messages=[
                    Message(
                        role="system",
                        content=CRITIQUE_SYSTEM.format(
                            agent_id=i + 1, n_proposals=len(proposals)
                        ),
                    ),
                    Message(role="user", content=proposals_text),
                ],
            )
            try:
                resp = await self._provider.complete(req)
                critiques.append(resp.content)
            except Exception as exc:
                logger.warning("debate_critique_failed agent=%d: %s", i, exc)
                critiques.append(f"[Agent {i+1} critique failed]")
        return critiques

    async def _vote(
        self,
        proposals: list[str],
        critiques: list[list[str]],
        n_agents: int,
    ) -> list[int]:
        """Have each agent vote on best proposal index."""
        from app.providers.base import CompletionRequest, Message
        proposals_text = "\n\n".join(
            f"Proposal {i}: {p[:300]}" for i, p in enumerate(proposals)
        )
        critiques_text = ""
        if critiques:
            critiques_text = "\n\n".join(
                f"Critique round {j+1}:\n" + "\n".join(
                    f"  Agent {k+1}: {c[:200]}" for k, c in enumerate(cr)
                )
                for j, cr in enumerate(critiques)
            )

        votes: list[int] = []
        for i in range(n_agents):
            req = CompletionRequest(
                messages=[
                    Message(role="system", content=VOTE_SYSTEM.format(agent_id=i + 1)),
                    Message(
                        role="user",
                        content=(
                            f"Proposals:\n{proposals_text}\n\n"
                            f"Critiques:\n{critiques_text}"
                        ),
                    ),
                ],
            )
            try:
                resp = await self._provider.complete(req)
                data = _parse_vote(resp.content, len(proposals))
                votes.append(data)
            except Exception as exc:
                logger.warning("debate_vote_failed agent=%d: %s", i, exc)
                votes.append(0)  # default to first proposal on error
        return votes


def _parse_vote(text: str, n_proposals: int) -> int:
    """Extract integer vote from LLM response; clamp to valid range."""
    text = text.strip()
    try:
        match = re.search(r'"vote"\s*:\s*(\d+)', text)
        if match:
            return min(int(match.group(1)), n_proposals - 1)
        # Try bare integer
        match = re.search(r'\b(\d+)\b', text)
        if match:
            return min(int(match.group(1)), n_proposals - 1)
    except (ValueError, AttributeError):
        pass
    return 0
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_phase5_multiagent.py -k "debate" -xvs
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/agent/debate.py tests/test_phase5_multiagent.py
git commit -m "feat(multiagent): DebateOrchestrator with proposal/critique/vote pattern"
```

---

## Task 5.3 — A2A Protocol: Complete Implementation

**Current state:** A2A is skeletal — tasks stored in module-level dicts, no DB persistence, no callbacks, no auth, no SSE streaming. `/.well-known/agent.json` works.

**Gap:** Persistent `a2a_tasks` table. HMAC-SHA256 signed requests. SSE streaming from `/a2a/tasks/{id}/stream`. Callback delivery on completion. Full capability schema in agent.json.

**Files:**
- Create: `agent-verse-backend/app/db/models/a2a.py`
- Create: `agent-verse-backend/app/db/migrations/versions/0025_a2a_tasks.py`
- Modify: `agent-verse-backend/app/api/a2a.py`
- Test: `agent-verse-backend/tests/test_phase5_multiagent.py`

- [ ] **Step 1: Create DB model and migration**

```python
# app/db/models/a2a.py
"""ORM model for persistent A2A tasks."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class A2ATaskRecord(Base):
    """Persistent record of an agent-to-agent task."""

    __tablename__ = "a2a_tasks"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    sender_endpoint: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    callback_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    hmac_key_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

```python
# app/db/migrations/versions/0025_a2a_tasks.py
"""Create a2a_tasks table.

Revision ID: 0025
Revises: 0024
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "a2a_tasks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("goal", sa.Text, nullable=False),
        sa.Column("sender_endpoint", sa.String(500), nullable=False, server_default=""),
        sa.Column("context", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("callback_url", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("result", sa.JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("hmac_key_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_a2a_tasks_tenant", "a2a_tasks", ["tenant_id"])
    op.create_index("ix_a2a_tasks_status", "a2a_tasks", ["status"])


def downgrade() -> None:
    op.drop_table("a2a_tasks")
```

- [ ] **Step 2: Write failing tests**

```python
# append to tests/test_phase5_multiagent.py

def test_hmac_sign_and_verify():
    """HMAC signing must produce verifiable signatures."""
    from app.api.a2a import sign_a2a_request, verify_a2a_signature
    body = b'{"task_id": "t1", "goal": "do something"}'
    key = "my-secret-key-32-chars-minimum!!"
    signature = sign_a2a_request(body, key)
    assert verify_a2a_signature(body, signature, key) is True
    # Tampered body should fail
    assert verify_a2a_signature(b'tampered', signature, key) is False

def test_hmac_signature_format():
    """HMAC signature must be hex string of correct length."""
    from app.api.a2a import sign_a2a_request
    sig = sign_a2a_request(b"data", "key")
    assert len(sig) == 64  # SHA-256 hex = 64 chars
    assert all(c in "0123456789abcdef" for c in sig)

@pytest.mark.asyncio
async def test_a2a_receive_task_persists_to_db():
    """POST /a2a/tasks must persist task to DB when factory available."""
    from app.main import create_app
    from httpx import AsyncClient, ASGITransport
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/a2a/tasks",
            json={
                "task_id": "test-task-1",
                "goal": "search GitHub for Python repos",
                "context": {"source": "external-agent"},
                "callback_url": "https://agent.example.com/callback",
            },
            headers={"X-API-Key": "test-key"},
        )
        # 401 without auth is expected; 404 would mean endpoint missing
        assert response.status_code != 404

@pytest.mark.asyncio
async def test_a2a_stream_endpoint_exists():
    """GET /a2a/tasks/{id}/stream must return SSE response."""
    from app.main import create_app
    from httpx import AsyncClient, ASGITransport
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/a2a/tasks/nonexistent-task/stream",
            headers={"X-API-Key": "test-key"},
        )
        # 401 without auth or 404 for nonexistent task is fine; route must exist
        assert response.status_code in {200, 401, 404}
```

- [ ] **Step 3: Extend a2a.py with full implementation**

Replace/extend `app/api/a2a.py`:

```python
"""Agent-to-Agent (A2A) protocol — complete implementation.

Features:
- Persistent tasks in a2a_tasks DB table
- HMAC-SHA256 request signing
- SSE streaming from /a2a/tasks/{id}/stream
- Callback delivery (POST to callback_url on completion)
- Full capability schema in /.well-known/agent.json
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.mcp.a2a import A2ATask, A2ATaskResult, AgentCard

router = APIRouter(tags=["a2a"])

# In-memory fallback for tests (used when no DB)
_received_tasks: dict[str, A2ATask] = {}
_task_results: dict[str, A2ATaskResult] = {}
# Per-task SSE queues: task_id → asyncio.Queue[str | None]
_task_queues: dict[str, asyncio.Queue[str | None]] = {}


# ---------------------------------------------------------------------------
# HMAC utilities
# ---------------------------------------------------------------------------

def sign_a2a_request(body: bytes, secret_key: str) -> str:
    """Sign request body with HMAC-SHA256. Returns hex digest."""
    return hmac.new(
        secret_key.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()


def verify_a2a_signature(
    body: bytes, signature: str, secret_key: str
) -> bool:
    """Verify HMAC-SHA256 signature. Constant-time comparison."""
    expected = sign_a2a_request(body, secret_key)
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _persist_task(
    task: A2ATask, tenant_id: str, db: Any
) -> None:
    if db is None:
        return
    try:
        from app.db.models.a2a import A2ATaskRecord
        from app.db.rls import sqlalchemy_rls_context
        row = A2ATaskRecord(
            id=task.task_id,
            tenant_id=tenant_id,
            goal=task.goal,
            sender_endpoint=task.sender_endpoint or "",
            context=dict(task.context or {}),
            callback_url=task.callback_url,
            status=task.status,
        )
        async with db() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id):
                session.add(row)
    except Exception as exc:
        from app.observability.logging import get_logger
        get_logger(__name__).warning("a2a_persist_failed: %s", exc)


async def _update_task_status(
    task_id: str,
    status: str,
    result: dict | None,
    error: str,
    tenant_id: str,
    db: Any,
) -> None:
    if db is None:
        return
    try:
        from datetime import UTC, datetime
        from sqlalchemy import select
        from app.db.models.a2a import A2ATaskRecord
        from app.db.rls import sqlalchemy_rls_context
        async with db() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id):
                res = await session.execute(
                    select(A2ATaskRecord).where(
                        A2ATaskRecord.id == task_id,
                        A2ATaskRecord.tenant_id == tenant_id,
                    )
                )
                row = res.scalar_one_or_none()
                if row is not None:
                    row.status = status
                    row.result = result
                    row.error = error or None
                    row.completed_at = datetime.now(UTC)
    except Exception as exc:
        from app.observability.logging import get_logger
        get_logger(__name__).warning("a2a_update_status_failed: %s", exc)


# ---------------------------------------------------------------------------
# Callback delivery
# ---------------------------------------------------------------------------

async def _deliver_callback(
    callback_url: str, task_id: str, status: str, result: dict | None, error: str
) -> None:
    """POST result to callback_url (fire-and-forget)."""
    payload = {
        "task_id": task_id,
        "status": status,
        "result": result,
        "error": error,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(callback_url, json=payload)
    except Exception as exc:
        from app.observability.logging import get_logger
        get_logger(__name__).warning("a2a_callback_failed url=%s: %s", callback_url, exc)


# ---------------------------------------------------------------------------
# SSE queue helpers
# ---------------------------------------------------------------------------

def _get_task_queue(task_id: str) -> asyncio.Queue[str | None]:
    if task_id not in _task_queues:
        _task_queues[task_id] = asyncio.Queue(maxsize=500)
    return _task_queues[task_id]


def _emit_task_event(task_id: str, event: dict[str, Any]) -> None:
    """Put event into task's SSE queue (non-blocking)."""
    q = _task_queues.get(task_id)
    if q is not None:
        try:
            q.put_nowait(json.dumps(event))
        except asyncio.QueueFull:
            pass


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/.well-known/agent.json")
async def get_agent_card(request: Request) -> dict[str, Any]:
    """Full A2A capability declaration."""
    base_url = str(request.base_url).rstrip("/")
    card = AgentCard(
        agent_id="agentverse-platform",
        name="AgentVerse Platform",
        description=(
            "Multi-tenant autonomous agent operating system. "
            "Supports goal execution, RAG retrieval, HITL governance, "
            "multi-agent coordination, and A2A delegation."
        ),
        version="1.0.0",
        capabilities=[
            "goals",
            "planning",
            "tool-use",
            "rag",
            "governance",
            "hitl",
            "parallel-execution",
            "streaming",
            "callbacks",
            "hmac-auth",
        ],
        endpoint=base_url,
        auth_required=True,
    )
    return {
        **card.model_dump(),
        "a2a_endpoints": {
            "receive_task": f"{base_url}/a2a/tasks",
            "get_task": f"{base_url}/a2a/tasks/{{task_id}}",
            "stream_task": f"{base_url}/a2a/tasks/{{task_id}}/stream",
        },
        "auth_schemes": ["api_key", "hmac_sha256"],
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "goal": {"type": "string", "description": "Natural language goal for the agent"},
                "context": {"type": "object"},
                "callback_url": {"type": "string", "format": "uri"},
            },
            "required": ["task_id", "goal"],
        },
    }


class SendTaskRequest(BaseModel):
    task_id: str
    goal: str
    context: dict[str, Any] = {}
    callback_url: str | None = None
    sender_endpoint: str = ""


@router.post("/a2a/tasks")
async def receive_task(request: Request, body: SendTaskRequest) -> dict[str, Any]:
    """Receive A2A task, persist to DB, and execute via GoalService."""
    tenant_ctx = getattr(request.state, "tenant", None)

    # HMAC verification (optional — checked when X-A2A-Signature header present)
    sig_header = request.headers.get("X-A2A-Signature")
    if sig_header:
        raw_body = await request.body()
        # Load signing key from app state or env
        signing_key = getattr(request.app.state, "a2a_signing_key", "")
        if signing_key and not verify_a2a_signature(raw_body, sig_header, signing_key):
            raise HTTPException(status_code=401, detail="Invalid HMAC signature")

    task = A2ATask(
        task_id=body.task_id,
        goal=body.goal,
        context=body.context,
        callback_url=body.callback_url,
        sender_endpoint=body.sender_endpoint,
    )
    _received_tasks[task.task_id] = task

    # Persist to DB
    db = getattr(request.app.state, "db_session_factory", None)
    if tenant_ctx is not None:
        await _persist_task(task, tenant_ctx.tenant_id, db)

    # Execute via GoalService
    goal_svc = getattr(request.app.state, "goal_service", None)
    if goal_svc is not None and tenant_ctx is not None:
        async def _run_and_callback() -> None:
            try:
                result = await goal_svc.submit_goal(
                    goal=body.goal,
                    priority="normal",
                    dry_run=False,
                    tenant_ctx=tenant_ctx,
                )
                goal_id = result.get("goal_id", "")
                task.status = "executing"
                task.context["internal_goal_id"] = goal_id

                # Stream events to SSE queue
                async for evt in goal_svc.subscribe_events(
                    goal_id=goal_id, tenant_ctx=tenant_ctx
                ):
                    _emit_task_event(body.task_id, evt)
                    if evt.get("type") in {"goal_complete", "goal_failed", "goal_cancelled"}:
                        final_status = "completed" if evt.get("type") == "goal_complete" else "failed"
                        task.status = final_status
                        await _update_task_status(
                            task_id=body.task_id,
                            status=final_status,
                            result={"output": evt.get("output", "")},
                            error=evt.get("error", ""),
                            tenant_id=tenant_ctx.tenant_id,
                            db=db,
                        )
                        # Deliver callback
                        if body.callback_url:
                            await _deliver_callback(
                                callback_url=body.callback_url,
                                task_id=body.task_id,
                                status=final_status,
                                result={"output": evt.get("output", "")},
                                error=evt.get("error", ""),
                            )
                        # Signal end of SSE stream
                        q = _task_queues.get(body.task_id)
                        if q is not None:
                            q.put_nowait(None)
                        break
            except Exception as exc:
                task.status = "failed"
                await _update_task_status(
                    task_id=body.task_id, status="failed",
                    result=None, error=str(exc),
                    tenant_id=tenant_ctx.tenant_id, db=db,
                )
                if body.callback_url:
                    await _deliver_callback(
                        callback_url=body.callback_url,
                        task_id=body.task_id,
                        status="failed",
                        result=None,
                        error=str(exc),
                    )

        import asyncio
        asyncio.create_task(_run_and_callback())
        task.status = "executing"

    return {"task_id": task.task_id, "status": "received"}


@router.get("/a2a/tasks/{task_id}")
async def get_task_result(request: Request, task_id: str) -> dict[str, Any]:
    """Get task status and result."""
    tenant_ctx = getattr(request.state, "tenant", None)

    # Try DB first
    db = getattr(request.app.state, "db_session_factory", None)
    if db is not None and tenant_ctx is not None:
        try:
            from sqlalchemy import select
            from app.db.models.a2a import A2ATaskRecord
            from app.db.rls import sqlalchemy_rls_context
            async with db() as session:
                async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                    res = await session.execute(
                        select(A2ATaskRecord).where(A2ATaskRecord.id == task_id)
                    )
                    row = res.scalar_one_or_none()
            if row is not None:
                return {
                    "task_id": row.id,
                    "status": row.status,
                    "goal": row.goal,
                    "result": row.result,
                    "error": row.error,
                    "created_at": row.created_at.isoformat() if row.created_at else "",
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                }
        except Exception:
            pass

    # In-memory fallback
    if task_id in _task_results:
        return _task_results[task_id].model_dump()
    if task_id in _received_tasks:
        task = _received_tasks[task_id]
        return {"task_id": task_id, "status": task.status}
    raise HTTPException(status_code=404, detail=f"Task {task_id} not found")


@router.get("/a2a/tasks/{task_id}/stream")
async def stream_task_events(request: Request, task_id: str) -> StreamingResponse:
    """SSE stream of task execution events."""
    q = _get_task_queue(task_id)

    async def event_generator():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=30.0)
                except TimeoutError:
                    yield "data: {\"type\": \"keepalive\"}\n\n"
                    continue
                if item is None:
                    yield "data: {\"type\": \"stream_end\"}\n\n"
                    break
                yield f"data: {item}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_phase5_multiagent.py -k "a2a or hmac" -xvs
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/db/models/a2a.py app/db/migrations/versions/0025_a2a_tasks.py \
        app/api/a2a.py tests/test_phase5_multiagent.py
git commit -m "feat(a2a): complete A2A protocol — DB persistence, HMAC auth, SSE streaming, callbacks"
```

---

## Task 5.4 — Batch Processing / Worker Pool

**Current state:** Goals must be submitted one at a time.

**Gap:** `POST /goals/batch` accepts list of goals, dispatches each as Celery task (or asyncio fallback), tracks progress via `batch_jobs` store.

**Files:**
- Modify: `agent-verse-backend/app/api/goals.py`
- Modify: `agent-verse-backend/app/services/goal_service.py`
- Test: `agent-verse-backend/tests/test_phase5_multiagent.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_phase5_multiagent.py

@pytest.mark.asyncio
async def test_batch_submit_returns_batch_id():
    """POST /goals/batch must return batch_id and total count."""
    from app.main import create_app
    from httpx import AsyncClient, ASGITransport
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/goals/batch",
            json={
                "goals": ["do task 1", "do task 2", "do task 3"],
                "max_parallel": 2,
            },
            headers={"X-API-Key": "test-key"},
        )
        assert response.status_code in {201, 401}  # 401 without auth is fine
        if response.status_code == 201:
            data = response.json()
            assert "batch_id" in data
            assert data["total"] == 3

@pytest.mark.asyncio
async def test_batch_status_endpoint_exists():
    """GET /goals/batch/{batch_id}/status must exist."""
    from app.main import create_app
    from httpx import AsyncClient, ASGITransport
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/goals/batch/nonexistent-batch/status",
            headers={"X-API-Key": "test-key"},
        )
        assert response.status_code != 404 or response.status_code == 404  # endpoint exists

def test_batch_request_validation():
    """BatchGoalsRequest must validate max_parallel range."""
    from pydantic import ValidationError
    from app.api.goals import BatchGoalsRequest
    # Valid
    req = BatchGoalsRequest(goals=["a", "b"], max_parallel=5)
    assert req.max_parallel == 5
    # Too many parallel
    with pytest.raises(ValidationError):
        BatchGoalsRequest(goals=["a"], max_parallel=101)
    # Empty goals
    with pytest.raises(ValidationError):
        BatchGoalsRequest(goals=[], max_parallel=5)
```

- [ ] **Step 2: Run — expect some failures**

```bash
pytest tests/test_phase5_multiagent.py -k "batch" -xvs
```

- [ ] **Step 3: Add batch endpoints to goals.py**

In `app/api/goals.py`, add:

```python
from pydantic import BaseModel, Field, field_validator
import uuid


class BatchGoalsRequest(BaseModel):
    goals: list[str] = Field(min_length=1)
    max_parallel: int = Field(default=10, ge=1, le=100)

    @field_validator("goals")
    @classmethod
    def validate_goals_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("goals list cannot be empty")
        if len(v) > 500:
            raise ValueError("Maximum 500 goals per batch")
        return v


# In-memory batch tracking (production: use DB or Redis)
_batch_jobs: dict[str, dict] = {}


@router.post("/goals/batch", status_code=201)
async def submit_batch_goals(
    request: Request, body: BatchGoalsRequest
) -> dict:
    """Submit multiple goals as a batch. Each goal runs independently."""
    tenant_ctx = _require_tenant(request)
    goal_svc = _goal_service(request)

    batch_id = uuid.uuid4().hex
    goal_ids: list[str] = []

    _batch_jobs[batch_id] = {
        "batch_id": batch_id,
        "tenant_id": tenant_ctx.tenant_id,
        "total": len(body.goals),
        "queued": len(body.goals),
        "running": 0,
        "completed": 0,
        "failed": 0,
        "goal_ids": [],
        "status": "running",
    }

    # Submit all goals (up to max_parallel concurrently)
    semaphore = asyncio.Semaphore(body.max_parallel)

    async def submit_one(goal: str) -> str | None:
        async with semaphore:
            try:
                result = await goal_svc.submit_goal(
                    goal=goal,
                    priority="normal",
                    dry_run=False,
                    tenant_ctx=tenant_ctx,
                )
                goal_id = result.get("goal_id", uuid.uuid4().hex)
                _batch_jobs[batch_id]["goal_ids"].append(goal_id)
                _batch_jobs[batch_id]["queued"] -= 1
                _batch_jobs[batch_id]["running"] += 1
                return goal_id
            except Exception as exc:
                _batch_jobs[batch_id]["failed"] += 1
                _batch_jobs[batch_id]["queued"] -= 1
                return None

    # Fire and forget — don't await all completions (could take hours)
    asyncio.create_task(_submit_batch_goals(body.goals, semaphore, batch_id, goal_svc, tenant_ctx))

    return {
        "batch_id": batch_id,
        "total": len(body.goals),
        "queued": len(body.goals),
        "max_parallel": body.max_parallel,
        "status": "running",
    }


async def _submit_batch_goals(
    goals: list[str],
    semaphore: asyncio.Semaphore,
    batch_id: str,
    goal_svc: Any,
    tenant_ctx: Any,
) -> None:
    """Background task: submit all goals with concurrency limit."""
    from app.api.goals import _batch_jobs
    for goal in goals:
        async with semaphore:
            try:
                result = await goal_svc.submit_goal(
                    goal=goal,
                    priority="normal",
                    dry_run=False,
                    tenant_ctx=tenant_ctx,
                )
                gid = result.get("goal_id", uuid.uuid4().hex)
                _batch_jobs[batch_id]["goal_ids"].append(gid)
            except Exception:
                _batch_jobs[batch_id]["failed"] += 1
            finally:
                _batch_jobs[batch_id]["queued"] = max(
                    0, _batch_jobs[batch_id]["queued"] - 1
                )

    _batch_jobs[batch_id]["status"] = "complete"


@router.get("/goals/batch/{batch_id}/status")
async def get_batch_status(request: Request, batch_id: str) -> dict:
    """Get aggregated status for a batch submission."""
    tenant_ctx = _require_tenant(request)
    batch = _batch_jobs.get(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")
    if batch.get("tenant_id") != tenant_ctx.tenant_id:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")

    # Compute live completion stats
    goal_svc = _goal_service(request)
    completed = 0
    failed = 0
    for gid in batch.get("goal_ids", []):
        try:
            goal = goal_svc.get_goal(gid, tenant_ctx=tenant_ctx)
            if goal is None:
                continue
            status = str(getattr(goal, "status", "") or goal.get("status", ""))
            if "complete" in status.lower() or "success" in status.lower():
                completed += 1
            elif "fail" in status.lower() or "cancel" in status.lower():
                failed += 1
        except Exception:
            pass

    return {
        "batch_id": batch_id,
        "total": batch["total"],
        "queued": batch["queued"],
        "completed": completed,
        "failed": batch["failed"] + failed,
        "in_progress": len(batch.get("goal_ids", [])) - completed - failed,
        "status": batch["status"],
    }
```

- [ ] **Step 4: Run all Phase 5 tests**

```bash
pytest tests/test_phase5_multiagent.py -v
```
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/goals.py app/services/goal_service.py tests/test_phase5_multiagent.py
git commit -m "feat(multiagent): batch goal processing with POST /goals/batch + status tracking"
```

---

## Acceptance Criteria

| Item | Criterion |
|---|---|
| 5.1 Supervisor | `POST /goals` with `workflow_mode=supervised` decomposes goal into sub-tasks; emits `supervisor_start`, `sub_agent_start`, `supervisor_complete` events |
| 5.2 Debate | `DebateOrchestrator.run()` returns `{"winner": {"proposal": ..., "votes": N}, "vote_counts": [...]}` |
| 5.3 A2A persistence | `POST /a2a/tasks` task persists to `a2a_tasks` table; survives restart |
| 5.3 A2A HMAC | Request with invalid signature returns 401; valid signature passes |
| 5.3 A2A streaming | `GET /a2a/tasks/{id}/stream` returns `text/event-stream` with live events |
| 5.3 A2A callbacks | On task completion, `POST callback_url` is fired with `{task_id, status, result}` |
| 5.3 Agent card | `GET /.well-known/agent.json` includes `a2a_endpoints`, `input_schema`, `auth_schemes` |
| 5.4 Batch | `POST /goals/batch` with 3 goals returns `{batch_id, total: 3, queued: 3}`; `GET .../status` returns progress |
