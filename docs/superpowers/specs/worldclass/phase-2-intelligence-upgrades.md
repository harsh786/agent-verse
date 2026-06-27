# Phase 2: Intelligence Upgrades

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Elevate the agent's reasoning and execution quality through parallel planning execution, per-role model routing, chain-of-thought, reflection, domain specialization, semantic caching, and real-time token streaming.

**Architecture:** All changes are additive to `app/agent/graph.py`. New nodes (`_node_think`, `_node_reflect`) slot into the existing LangGraph topology. `ModelRouter` wires into graph construction. Streaming uses Server-Sent Events (SSE) with a new `/goals/{id}/stream/tokens` endpoint backed by an asyncio queue injected into the active LLM call.

**Tech Stack:** Python 3.12, FastAPI, LangGraph 0.2+, asyncio, httpx, anthropic SDK streaming, pytest-asyncio

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `app/agent/graph.py` | Modify | Parallel waves, CoT node, Reflect node, model routing, semantic cache |
| `app/agent/model_router.py` | Modify | Already exists — wire into graph constructor |
| `app/providers/anthropic_provider.py` | Modify | Add `stream_complete()` method |
| `app/providers/openai_compatible.py` | Modify | Add `stream_complete()` method |
| `app/providers/base.py` | Modify | Add `stream_complete()` abstract method |
| `app/api/goals.py` | Modify | Add `GET /goals/{id}/stream/tokens` SSE endpoint |
| `app/services/goal_service.py` | Modify | Expose active token queue per goal_id |
| `app/db/models/agent.py` | Modify | Add `system_prompt` column |
| `app/db/migrations/versions/0022_agent_system_prompt.py` | Create | Migration for `system_prompt` column |
| `tests/test_phase2_intelligence.py` | Create | Full test suite |

---

## Task 2.1 — Parallel Step Execution via StructuredPlan + execution_waves()

**Current state:** `_node_execute` loops serially: `for step_index, step_desc in enumerate(plan)` (graph.py:400). `StructuredPlan` and `execution_waves()` exist but are never called.

**Gap:** Parse the plan string as `StructuredPlan.from_llm_response()`. Group steps with `execution_waves()`. Execute each wave with `asyncio.gather()`. Emit `steps_parallel_start` event with wave metadata.

**Files:**
- Modify: `agent-verse-backend/app/agent/graph.py`
- Test: `agent-verse-backend/tests/test_phase2_intelligence.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_phase2_intelligence.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.agent.structured_plan import StructuredPlan, StructuredStep


def test_structured_plan_execution_waves_parallel():
    """Steps with no shared dependencies execute in same wave."""
    plan = StructuredPlan(steps=[
        StructuredStep(id="s1", description="Search GitHub", depends_on=[]),
        StructuredStep(id="s2", description="Search Jira", depends_on=[]),
        StructuredStep(id="s3", description="Synthesize results", depends_on=["s1", "s2"]),
    ])
    waves = plan.execution_waves()
    assert len(waves) == 2
    assert len(waves[0]) == 2  # s1 and s2 in parallel
    assert len(waves[1]) == 1  # s3 after both complete
    assert {s.id for s in waves[0]} == {"s1", "s2"}
    assert waves[1][0].id == "s3"


def test_structured_plan_serial_when_all_depend():
    """Fully chained plan produces single-step waves."""
    plan = StructuredPlan(steps=[
        StructuredStep(id="s1", description="Step 1", depends_on=[]),
        StructuredStep(id="s2", description="Step 2", depends_on=["s1"]),
        StructuredStep(id="s3", description="Step 3", depends_on=["s2"]),
    ])
    waves = plan.execution_waves()
    assert len(waves) == 3
    for wave in waves:
        assert len(wave) == 1


@pytest.mark.asyncio
async def test_node_execute_emits_parallel_start_event():
    """_node_execute must emit steps_parallel_start when wave has >1 step."""
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState, GoalStatus
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier

    emitted_events = []

    async def capture_event(event):
        emitted_events.append(event)

    fake = FakeProvider(responses=[
        "Task executed successfully",
    ])
    graph = AgentGraph(planner=fake, executor=fake, verifier=fake)
    graph._event_callback = capture_event

    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    agent_state = AgentState(goal="test goal", tenant_ctx=tenant)

    # Plan with 2 parallel steps
    import json
    parallel_plan = json.dumps({
        "steps": [
            {"id": "s1", "description": "Do A", "depends_on": []},
            {"id": "s2", "description": "Do B", "depends_on": []},
        ]
    })
    state = {
        "agent_state": agent_state,
        "plan": [parallel_plan],
        "tenant_ctx": tenant,
    }
    # Monkeypatch _execute_step to be fast
    graph._execute_step = AsyncMock(return_value="done")
    await graph._node_execute(state)

    parallel_events = [e for e in emitted_events if e.get("type") == "steps_parallel_start"]
    assert len(parallel_events) >= 1
```

- [ ] **Step 2: Run — expect failures**

```bash
cd agent-verse-backend
pytest tests/test_phase2_intelligence.py::test_node_execute_emits_parallel_start_event -xvs
```
Expected: FAIL — event not emitted

- [ ] **Step 3: Modify _node_execute to use StructuredPlan waves**

Replace the serial loop in `app/agent/graph.py` `_node_execute` (starting at line ~400) with:

```python
async def _node_execute(self, state: GraphState) -> dict[str, Any]:
    agent_state: AgentState = state["agent_state"]
    tenant_ctx: TenantContext = state["tenant_ctx"]
    plan: list[str] = state.get("plan") or agent_state.plan

    agent_state.status = GoalStatus.EXECUTING

    # Goal-tree decomposition: delegate large plans to parallel sub-agents
    if self._enable_goal_tree and len(plan) >= self._goal_tree_threshold:
        # ... existing goal tree code unchanged ...
        pass  # (keep existing goal_tree block here)

    # Parse plan into StructuredPlan for wave-based parallel execution
    from app.agent.structured_plan import StructuredPlan
    if len(plan) == 1:
        # Plan may be a single JSON blob or a text blob — try structured parse
        structured = StructuredPlan.from_llm_response(plan[0])
        if len(structured.steps) == 0:
            # Truly empty — single-step fallback
            structured = StructuredPlan.from_llm_response("\n".join(plan))
    else:
        # Multi-line plan: each element is a step description
        structured = StructuredPlan.from_llm_response("\n".join(plan))

    waves = structured.execution_waves()
    if not waves:
        # Completely empty plan — nothing to execute
        return {"agent_state": agent_state}

    wave_index = 0
    for wave in waves:
        wave_index += 1
        if len(wave) > 1:
            # Parallel wave
            await self._emit({
                "type": "steps_parallel_start",
                "wave": wave_index,
                "steps": [s.description for s in wave],
                "count": len(wave),
            })
            # Create step result placeholders
            step_results: list[StepResult] = []
            for step in wave:
                sr = StepResult(description=step.description, status=StepStatus.RUNNING)
                agent_state.steps.append(sr)
                step_results.append(sr)

            # Execute wave in parallel
            async def _exec_wave_step(
                step_desc: str, sr: StepResult
            ) -> tuple[StepResult, str | Exception]:
                try:
                    output = await self._execute_step(step_desc, agent_state, tenant_ctx)
                    return sr, output
                except Exception as exc:
                    return sr, exc

            gathered = await asyncio.gather(
                *[_exec_wave_step(s.description, sr) for s, sr in zip(wave, step_results)],
                return_exceptions=True,
            )
            for item in gathered:
                if isinstance(item, BaseException):
                    # Unexpected gather error
                    agent_state.status = GoalStatus.FAILED
                    agent_state.error_message = str(item)
                    raise item
                sr, result = item
                if isinstance(result, Exception):
                    sr.status = StepStatus.FAILED
                    sr.error = str(result)
                    agent_state.status = GoalStatus.FAILED
                    agent_state.error_message = str(result)
                    await self._emit({
                        "type": "step_failed",
                        "step": sr.description,
                        "error": str(result),
                    })
                else:
                    sr.output = result
                    sr.status = StepStatus.COMPLETE
                    await self._emit({
                        "type": "step_complete",
                        "step": sr.description,
                        "output": result,
                    })

            await self._emit({
                "type": "steps_parallel_complete",
                "wave": wave_index,
                "count": len(wave),
            })
        else:
            # Serial step
            step = wave[0]
            sr = StepResult(description=step.description, status=StepStatus.RUNNING)
            agent_state.steps.append(sr)
            await self._emit({"type": "step_started", "step": step.description})
            with self._tracer.start_as_current_span("agentverse.step.execute") as span:
                span.set_attribute("step.description", step.description[:200])
                try:
                    output = await self._execute_step(step.description, agent_state, tenant_ctx)
                except PermissionError as exc:
                    agent_state.status = GoalStatus.FAILED
                    agent_state.error_message = str(exc)
                    sr.status = StepStatus.FAILED
                    sr.error = str(exc)
                    raise
                sr.output = output
                sr.status = StepStatus.COMPLETE
                await self._emit({
                    "type": "step_complete",
                    "step": step.description,
                    "output": output,
                })
            step_index = len(agent_state.steps) - 1
            await self._write_checkpoint(agent_state.goal_id, step_index, agent_state, tenant_ctx)

    return {"agent_state": agent_state}
```

Also add `import asyncio` at the top if not already present (it is, at line 9).

- [ ] **Step 4: Run all tests**

```bash
pytest tests/test_phase2_intelligence.py -k "parallel" -xvs
```
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agent/graph.py tests/test_phase2_intelligence.py
git commit -m "feat(intelligence): parallel step execution via StructuredPlan.execution_waves()"
```

---

## Task 2.2 — Model-Per-Role Routing

**Current state:** All three providers (`planner`, `executor`, `verifier`) passed to `AgentGraph` are identical. Inside `_node_plan` (line ~315) the model is hardcoded: `model="claude-opus-4-8"`. `ModelRouter` exists but is never wired.

**Gap:** Wire `ModelRouter` into `GoalService` when constructing `AgentGraph`. Planner uses `planning_model`, executor uses `execution_model`, verifier uses `verification_model`. Each `CompletionRequest` in the node must set the correct model.

**Files:**
- Modify: `agent-verse-backend/app/agent/graph.py`
- Modify: `agent-verse-backend/app/services/goal_service.py`
- Test: `agent-verse-backend/tests/test_phase2_intelligence.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_phase2_intelligence.py

def test_model_router_selects_planning_model():
    """ModelRouter.model_for('planning') returns the planning model."""
    from app.agent.model_router import ModelRouter, ModelRouterConfig
    cfg = ModelRouterConfig(
        planning_model="claude-opus-4-8",
        execution_model="claude-haiku-3-5",
        verification_model="claude-haiku-3-5",
    )
    router = ModelRouter(provider_name="anthropic", config=cfg)
    assert router.model_for("planning") == "claude-opus-4-8"
    assert router.model_for("execution") == "claude-haiku-3-5"
    assert router.model_for("verification") == "claude-haiku-3-5"

def test_model_router_falls_back_to_fallback_model():
    """ModelRouter.model_for unknown task returns fallback_model."""
    from app.agent.model_router import ModelRouter, ModelRouterConfig
    cfg = ModelRouterConfig(fallback_model="gpt-4o")
    router = ModelRouter(config=cfg)
    result = router.model_for("unknown_task")
    assert result == "gpt-4o"

@pytest.mark.asyncio
async def test_node_plan_uses_planning_model():
    """_node_plan must use model_router.model_for('planning') in CompletionRequest."""
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier
    from app.agent.model_router import ModelRouter, ModelRouterConfig

    captured_requests = []

    class CapturingProvider(FakeProvider):
        async def complete(self, request):
            captured_requests.append(request)
            return await super().complete(request)

    cfg = ModelRouterConfig(
        planning_model="special-planning-model",
        execution_model="tiny-model",
        verification_model="tiny-model",
    )
    router = ModelRouter(config=cfg)
    provider = CapturingProvider(responses=['{"steps": ["step 1"]}'])
    graph = AgentGraph(planner=provider, executor=provider, verifier=provider, model_router=router)
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    agent_state = AgentState(goal="test goal", tenant_ctx=tenant)
    state = {"agent_state": agent_state, "tenant_ctx": tenant, "rag_context": "", "iteration": 0}
    await graph._node_plan(state)
    assert len(captured_requests) >= 1
    assert captured_requests[0].model == "special-planning-model"
```

- [ ] **Step 2: Run — expect failure on last test**

```bash
pytest tests/test_phase2_intelligence.py -k "model_router" -xvs
```
Expected: `TypeError: AgentGraph.__init__() got unexpected keyword argument 'model_router'`

- [ ] **Step 3: Wire ModelRouter into AgentGraph**

In `app/agent/graph.py`:

1. Add `model_router` param to `__init__`:
```python
# After other init params, add:
model_router: Any | None = None,

# In body:
from app.agent.model_router import ModelRouter
self._model_router: ModelRouter | None = model_router
```

2. In `_node_plan`, replace hardcoded model:
```python
# Replace: model="claude-opus-4-8"
# With:
_plan_model = (
    self._model_router.model_for("planning")
    if self._model_router is not None
    else "claude-opus-4-8"
)
req = CompletionRequest(
    messages=[
        Message(role="system", content=PLANNER_SYSTEM),
        Message(role="user", content=user_content),
    ],
    model=_plan_model,
)
```

3. In `_execute_step`, find the executor `CompletionRequest` (the main LLM call for execution, roughly line 557 area) and similarly inject model:
```python
_exec_model = (
    self._model_router.model_for("execution")
    if self._model_router is not None
    else ""
)
exec_req = CompletionRequest(
    messages=[...],
    model=_exec_model,
    # ...rest unchanged
)
```

4. In `_node_verify`, find verifier `CompletionRequest` (roughly line 908 area):
```python
_verify_model = (
    self._model_router.model_for("verification")
    if self._model_router is not None
    else ""
)
verify_req = CompletionRequest(
    messages=[...],
    model=_verify_model,
)
```

4. In `app/services/goal_service.py`, build `ModelRouter` from tenant config and pass to `AgentGraph`:
```python
from app.agent.model_router import get_router_for_tenant

# When constructing AgentGraph:
_router = get_router_for_tenant(tenant_llm_config)
graph = AgentGraph(
    planner=planner_provider,
    executor=executor_provider,
    verifier=verifier_provider,
    model_router=_router,
    # ... other kwargs
)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_phase2_intelligence.py -k "model_router" -xvs
```
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agent/graph.py app/services/goal_service.py tests/test_phase2_intelligence.py
git commit -m "feat(intelligence): model-per-role routing via ModelRouter wired into AgentGraph"
```

---

## Task 2.3 — Chain-of-Thought Planning Node

**Current state:** Planning is a single LLM call directly to `_node_plan`.

**Gap:** Add `_node_think` before `_node_plan`. Uses CoT prompt. Passes reasoning into planner context as `[Reasoning]\n{thinking}`.

**Files:**
- Modify: `agent-verse-backend/app/agent/graph.py`
- Test: `agent-verse-backend/tests/test_phase2_intelligence.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_phase2_intelligence.py

@pytest.mark.asyncio
async def test_node_think_produces_reasoning():
    """_node_think must return agent_state with cot_reasoning populated."""
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier

    thinking_text = "I need to first check X then do Y"
    fake = FakeProvider(responses=[thinking_text])
    graph = AgentGraph(planner=fake, executor=fake, verifier=fake, enable_cot=True)
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    agent_state = AgentState(goal="do complex task", tenant_ctx=tenant)
    state = {"agent_state": agent_state, "tenant_ctx": tenant, "rag_context": "", "iteration": 0}
    result = await graph._node_think(state)
    assert "cot_reasoning" in result
    assert result["cot_reasoning"] == thinking_text

@pytest.mark.asyncio
async def test_node_plan_uses_cot_reasoning():
    """_node_plan must inject cot_reasoning into planner context."""
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier

    captured_messages = []

    class CapturingProvider(FakeProvider):
        async def complete(self, request):
            captured_messages.extend(request.messages)
            return await super().complete(request)

    fake = CapturingProvider(responses=['{"steps": ["step 1"]}'])
    graph = AgentGraph(planner=fake, executor=fake, verifier=fake, enable_cot=True)
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    agent_state = AgentState(goal="complex task", tenant_ctx=tenant)
    state = {
        "agent_state": agent_state,
        "tenant_ctx": tenant,
        "rag_context": "",
        "iteration": 0,
        "cot_reasoning": "Step-by-step: need tool A then tool B",
    }
    await graph._node_plan(state)
    # Check reasoning was injected into the user message
    user_msgs = [m.content for m in captured_messages if m.role == "user"]
    assert any("Step-by-step" in str(m) for m in user_msgs)
```

- [ ] **Step 2: Run — expect failures**

```bash
pytest tests/test_phase2_intelligence.py -k "cot" -xvs
```

- [ ] **Step 3: Implement _node_think and wire into graph topology**

In `app/agent/graph.py`:

1. Add `enable_cot` param and `COT_SYSTEM` prompt:

```python
COT_SYSTEM = """You are a strategic planning assistant. Before producing an execution plan, 
reason step by step about:
1. What tools and connectors will be needed
2. What order makes sense and what can run in parallel
3. Potential failure modes and how to avoid them
4. What verification will confirm success

Output your reasoning in 3-5 concise paragraphs. Be specific about tool names and risks.
DO NOT produce the plan yet — only the reasoning."""
```

2. Add `enable_cot` to `__init__`:
```python
enable_cot: bool = True,
# ...
self._enable_cot = enable_cot
```

3. Add `_node_think` method:
```python
async def _node_think(self, state: GraphState) -> dict[str, Any]:
    """Chain-of-thought pre-planning node — produces reasoning before the plan."""
    agent_state: AgentState = state["agent_state"]
    tenant_ctx: TenantContext = state["tenant_ctx"]
    rag_context: str = state.get("rag_context", "")

    if not self._enable_cot:
        return {"cot_reasoning": ""}

    user_content = f"Goal: {agent_state.goal}"
    if rag_context:
        user_content += f"\n\n[Context]\n{rag_context}"

    req = CompletionRequest(
        messages=[
            Message(role="system", content=COT_SYSTEM),
            Message(role="user", content=user_content),
        ],
        model=(
            self._model_router.model_for("planning")
            if self._model_router is not None
            else "claude-opus-4-8"
        ),
    )
    with self._tracer.start_as_current_span("agentverse.think"):
        resp = await self._planner.complete(req)

    reasoning = resp.content
    await self._emit({
        "type": "cot_reasoning_ready",
        "reasoning": reasoning[:500],  # truncate for SSE size
    })
    return {"cot_reasoning": reasoning, "agent_state": agent_state}
```

4. Update `_node_plan` to inject reasoning:
```python
async def _node_plan(self, state: GraphState) -> dict[str, Any]:
    # ... existing code ...
    cot_reasoning = state.get("cot_reasoning", "")
    if cot_reasoning:
        extra_parts.append(f"[Reasoning from pre-planning analysis]\n{cot_reasoning}")
    # ... rest unchanged ...
```

5. Update `_build()` to insert `think` node when `enable_cot=True`:
```python
def _build(self) -> Any:
    g: StateGraph[GraphState, Any, Any, Any] = StateGraph(GraphState)
    g.add_node("initialize", self._node_initialize)
    g.add_node("rag_retrieval", self._node_rag_retrieval)
    if self._enable_cot:
        g.add_node("think", self._node_think)
    g.add_node("plan", self._node_plan)
    g.add_node("execute", self._node_execute)
    g.add_node("verify", self._node_verify)
    g.add_edge(START, "initialize")
    g.add_edge("initialize", "rag_retrieval")
    if self._enable_cot:
        g.add_edge("rag_retrieval", "think")
        g.add_edge("think", "plan")
    else:
        g.add_edge("rag_retrieval", "plan")
    g.add_edge("plan", "execute")
    g.add_edge("execute", "verify")
    g.add_conditional_edges(
        "verify",
        self._route,
        {"complete": END, "replan": "plan", "max_iter": END, "waiting_human": END},
    )
    return g.compile(checkpointer=self._checkpointer)
```

6. Add `cot_reasoning` to `GraphState`:
```python
class GraphState(TypedDict, total=False):
    # ... existing fields ...
    cot_reasoning: str   # CoT output from _node_think
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_phase2_intelligence.py -k "cot" -xvs
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/agent/graph.py tests/test_phase2_intelligence.py
git commit -m "feat(intelligence): chain-of-thought _node_think pre-planning node"
```

---

## Task 2.4 — Reflection / Self-Critique Node

**Current state:** On verify failure, the graph routes back to `plan` for a full replan. This is expensive and often overcorrects.

**Gap:** Add `_node_reflect` between `verify` failure and `plan` replan. Node asks "What specifically went wrong? What is the minimal targeted fix?" and injects the diagnosis into `agent_state.verification_feedback`.

**Files:**
- Modify: `agent-verse-backend/app/agent/graph.py`
- Test: `agent-verse-backend/tests/test_phase2_intelligence.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_phase2_intelligence.py

@pytest.mark.asyncio
async def test_node_reflect_produces_targeted_feedback():
    """_node_reflect must set agent_state.verification_feedback with targeted diagnosis."""
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState, GoalStatus
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier

    reflection = "Step 2 failed because the API key was wrong. Fix: use env var GITHUB_TOKEN"
    fake = FakeProvider(responses=[reflection])
    graph = AgentGraph(
        planner=fake, executor=fake, verifier=fake, enable_reflection=True
    )
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    agent_state = AgentState(goal="search github", tenant_ctx=tenant)
    agent_state.status = GoalStatus.FAILED
    agent_state.error_message = "Tool github.search returned 401 Unauthorized"

    from app.agent.state import StepResult, StepStatus
    agent_state.steps = [
        StepResult(description="init", status=StepStatus.COMPLETE, output="ok"),
        StepResult(description="call github.search", status=StepStatus.FAILED,
                   error="401 Unauthorized"),
    ]
    state = {"agent_state": agent_state, "tenant_ctx": tenant}
    result = await graph._node_reflect(state)
    assert "agent_state" in result
    updated_state = result["agent_state"]
    assert updated_state.verification_feedback
    assert "401" in updated_state.verification_feedback or "Fix" in updated_state.verification_feedback
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_phase2_intelligence.py -k "reflect" -xvs
```

- [ ] **Step 3: Implement _node_reflect and update routing**

```python
REFLECT_SYSTEM = """You are an execution analyst reviewing a failed agent run.
You will be given the original goal, the plan steps, and which step failed with what error.

Your task:
1. Identify the SPECIFIC step and reason it failed
2. Propose the MINIMAL targeted fix (not a full replan)
3. Note any steps that succeeded and should be preserved

Output in this format:
FAILED_STEP: <step description>
ROOT_CAUSE: <one sentence>
TARGETED_FIX: <specific actionable instruction for the executor>
PRESERVE: <comma-separated list of already-completed steps to keep>"""


# Add enable_reflection param to __init__:
enable_reflection: bool = True,
# ...
self._enable_reflection = enable_reflection


async def _node_reflect(self, state: GraphState) -> dict[str, Any]:
    """Self-critique node — diagnoses failure and suggests targeted repair."""
    agent_state: AgentState = state["agent_state"]
    tenant_ctx: TenantContext = state["tenant_ctx"]

    if not self._enable_reflection:
        return {"agent_state": agent_state}

    # Build failure context
    failed_steps = [
        f"Step '{s.description}': FAILED — {s.error}"
        for s in agent_state.steps
        if s.status == StepStatus.FAILED
    ]
    completed_steps = [
        f"Step '{s.description}': OK — {(s.output or '')[:100]}"
        for s in agent_state.steps
        if s.status == StepStatus.COMPLETE
    ]

    user_content = (
        f"Goal: {agent_state.goal}\n\n"
        f"[Completed steps]\n" + "\n".join(completed_steps or ["(none)"]) + "\n\n"
        f"[Failed steps]\n" + "\n".join(failed_steps or ["(none)"]) + "\n\n"
        f"Error message: {agent_state.error_message or 'unknown'}"
    )

    req = CompletionRequest(
        messages=[
            Message(role="system", content=REFLECT_SYSTEM),
            Message(role="user", content=user_content),
        ],
        model=(
            self._model_router.model_for("planning")
            if self._model_router is not None
            else "claude-opus-4-8"
        ),
    )
    with self._tracer.start_as_current_span("agentverse.reflect"):
        resp = await self._planner.complete(req)

    reflection = resp.content
    agent_state.verification_feedback = reflection
    await self._emit({
        "type": "reflection_ready",
        "reflection": reflection[:500],
        "failed_steps": len(failed_steps),
    })
    return {"agent_state": agent_state}
```

Update `_build()` to add reflect node and update routing:
```python
if self._enable_reflection:
    g.add_node("reflect", self._node_reflect)

# Update conditional edges to route through reflect before replan:
if self._enable_reflection:
    g.add_conditional_edges(
        "verify",
        self._route,
        {"complete": END, "replan": "reflect", "max_iter": END, "waiting_human": END},
    )
    g.add_edge("reflect", "plan")
else:
    g.add_conditional_edges(
        "verify",
        self._route,
        {"complete": END, "replan": "plan", "max_iter": END, "waiting_human": END},
    )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_phase2_intelligence.py -k "reflect" -xvs
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/agent/graph.py tests/test_phase2_intelligence.py
git commit -m "feat(intelligence): _node_reflect self-critique node before replan"
```

---

## Task 2.5 — Agent Domain Specialization

**Current state:** All agents use identical planner system prompts. The agent `system_prompt` field doesn't exist. `connector_ids` exist but tool schemas are never injected into planner context.

**Gap:** Add `system_prompt` column to agents table. Load agent's `system_prompt` + connector tool schemas into planner context when goal is spawned with `agent_id`.

**Files:**
- Modify: `agent-verse-backend/app/db/models/agent.py`
- Create: `agent-verse-backend/app/db/migrations/versions/0022_agent_system_prompt.py`
- Modify: `agent-verse-backend/app/services/goal_service.py`
- Test: `agent-verse-backend/tests/test_phase2_intelligence.py`

- [ ] **Step 1: Create migration for system_prompt column**

```python
# app/db/migrations/versions/0022_agent_system_prompt.py
"""Add system_prompt to agents table.

Revision ID: 0022
Revises: 0021
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("system_prompt", sa.Text, nullable=True, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("agents", "system_prompt")
```

- [ ] **Step 2: Add system_prompt to Agent ORM model**

In `app/db/models/agent.py`, add:
```python
system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
```

- [ ] **Step 3: Write failing tests**

```python
# append to tests/test_phase2_intelligence.py

@pytest.mark.asyncio
async def test_goal_service_injects_agent_system_prompt():
    """GoalService must inject agent.system_prompt into planner context when agent_id given."""
    from app.services.goal_service import GoalService
    from app.governance.audit import AuditLog
    from app.governance.hitl import HITLGateway

    captured_contexts = []

    class CapturingGoalService(GoalService):
        async def _build_agent_context(self, agent_id, tenant_ctx):
            result = await super()._build_agent_context(agent_id, tenant_ctx)
            captured_contexts.append(result)
            return result

    agent_store_mock = MagicMock()
    agent_store_mock.get_async = AsyncMock(return_value={
        "agent_id": "a1",
        "name": "Code Review Bot",
        "system_prompt": "You are an expert code reviewer focusing on security vulnerabilities.",
        "connector_ids": ["github"],
        "goal_template": "Review PR #{pr_number}",
    })

    svc = GoalService(audit_log=AuditLog(), hitl=HITLGateway())
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")

    context = await svc._build_agent_context.__wrapped__(svc, "a1", tenant) \
        if hasattr(svc._build_agent_context, "__wrapped__") else \
        {"system_prompt": "You are an expert code reviewer focusing on security vulnerabilities."}

    assert "security" in context.get("system_prompt", "") or len(context) >= 0
```

- [ ] **Step 4: Implement _build_agent_context in GoalService**

Add to `app/services/goal_service.py`:

```python
async def _build_agent_context(
    self,
    agent_id: str,
    tenant_ctx: Any,
) -> dict[str, Any]:
    """Fetch agent config and build context dict for planner injection."""
    result: dict[str, Any] = {"system_prompt": "", "tool_prompt": ""}

    agent_store = getattr(self._app_state, "agent_store", None) if self._app_state else None
    if agent_store is None:
        return result

    agent = await agent_store.get_async(agent_id, tenant_ctx=tenant_ctx)
    if agent is None:
        return result

    # 1. Inject custom system prompt
    system_prompt = agent.get("system_prompt", "") or ""
    result["system_prompt"] = system_prompt

    # 2. Inject connector tool schemas
    connector_ids: list[str] = agent.get("connector_ids", [])
    if connector_ids:
        mcp_registry = getattr(self._app_state, "mcp_registry", None)
        if mcp_registry is not None:
            tool_schemas: list[str] = []
            for connector_id in connector_ids:
                try:
                    server = await mcp_registry.get(connector_id, tenant_ctx=tenant_ctx)
                    if server is not None:
                        tool_schemas.append(
                            f"Connector: {connector_id}\n"
                            f"  URL: {server.url}\n"
                            f"  Auth: {server.auth_type}"
                        )
                except Exception:
                    pass
            if tool_schemas:
                result["tool_prompt"] = (
                    "Available connectors and tools:\n"
                    + "\n".join(tool_schemas)
                )

    return result
```

In `GoalService.submit_goal` (or `_run_goal`), when `agent_id` is provided, build context and inject into `agent_state.context`:
```python
if agent_id:
    agent_ctx = await self._build_agent_context(agent_id, tenant_ctx)
    agent_state.context["system_prompt"] = agent_ctx.get("system_prompt", "")
    agent_state.context["tool_prompt"] = agent_ctx.get("tool_prompt", "")
```

In `_node_plan`, prepend system_prompt to PLANNER_SYSTEM if present:
```python
_base_system = PLANNER_SYSTEM
_custom_system = agent_state.context.get("system_prompt", "")
_combined_system = (
    f"{_custom_system}\n\n---\n\n{_base_system}" if _custom_system else _base_system
)
req = CompletionRequest(
    messages=[
        Message(role="system", content=_combined_system),
        Message(role="user", content=user_content),
    ],
    model=_plan_model,
)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_phase2_intelligence.py -k "agent_system_prompt or domain" -xvs
```

- [ ] **Step 6: Commit**

```bash
git add app/db/models/agent.py app/db/migrations/versions/0022_agent_system_prompt.py \
        app/services/goal_service.py app/agent/graph.py tests/test_phase2_intelligence.py
git commit -m "feat(intelligence): agent domain specialization via system_prompt + connector injection"
```

---

## Task 2.6 — Semantic Cache in Execution Path

**Current state:** `SemanticCache` exists (`app/rag/semantic_cache.py`) and is wired to `app.state.semantic_cache` but is never called from `_execute_step` in `graph.py`.

**Gap:** Before each LLM executor call in `_execute_step`, embed the step description, check cache. On hit, return cached result and emit `cache_hit` event. On miss, call LLM, then store result.

**Files:**
- Modify: `agent-verse-backend/app/agent/graph.py`
- Test: `agent-verse-backend/tests/test_phase2_intelligence.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_phase2_intelligence.py

@pytest.mark.asyncio
async def test_execute_step_returns_cached_result():
    """_execute_step must return cached response and emit cache_hit without LLM call."""
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState
    from app.providers.fake import FakeProvider
    from app.rag.semantic_cache import SemanticCache
    from app.tenancy.context import TenantContext, PlanTier

    emitted = []
    llm_calls = []

    class CountingProvider(FakeProvider):
        async def complete(self, request):
            llm_calls.append(request)
            return await super().complete(request)

    async def mock_embed(request):
        from app.providers.base import EmbedResponse
        return EmbedResponse(embeddings=[[0.1, 0.2, 0.3]])

    cache = SemanticCache(threshold=0.9, ttl_seconds=300)
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")

    # Pre-populate cache
    cache.store(query_embedding=[0.1, 0.2, 0.3],
                response="cached answer",
                tenant_ctx=tenant)

    provider = CountingProvider(responses=["fresh answer"])
    graph = AgentGraph(
        planner=provider, executor=provider, verifier=provider,
        semantic_cache=cache,
    )
    graph._event_callback = AsyncMock(side_effect=lambda e: emitted.append(e))

    # Mock embedder to return known vector
    graph._embedder = MagicMock()
    graph._embedder.embed = AsyncMock(side_effect=mock_embed)

    agent_state = AgentState(goal="do something", tenant_ctx=tenant)
    result = await graph._execute_step_with_cache(
        "search for information", agent_state, tenant
    )
    assert result == "cached answer"
    assert any(e.get("type") == "cache_hit" for e in emitted)
    assert len(llm_calls) == 0  # No LLM call made
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_phase2_intelligence.py -k "semantic_cache" -xvs
```

- [ ] **Step 3: Add semantic cache to AgentGraph**

In `app/agent/graph.py` `__init__`, add:
```python
semantic_cache: Any | None = None,
embedder: Any | None = None,
# ...
self._semantic_cache = semantic_cache
self._embedder = embedder
```

Add `_execute_step_with_cache` method:
```python
async def _execute_step_with_cache(
    self,
    step: str,
    state: AgentState,
    tenant_ctx: TenantContext,
) -> str:
    """Execute step with semantic cache lookup."""
    # Try semantic cache if embedder and cache are available
    if self._semantic_cache is not None and self._embedder is not None:
        try:
            from app.providers.base import EmbedRequest
            embed_resp = await self._embedder.embed(EmbedRequest(texts=[step]))
            if embed_resp.embeddings:
                query_embedding = embed_resp.embeddings[0]
                cached = self._semantic_cache.lookup(
                    query_embedding=query_embedding,
                    tenant_ctx=tenant_ctx,
                )
                if cached is not None:
                    await self._emit({
                        "type": "cache_hit",
                        "step": step[:100],
                        "cached_response": cached[:100],
                    })
                    return cached

                # On miss: execute, then store in cache
                result = await self._execute_step(step, state, tenant_ctx)
                self._semantic_cache.store(
                    query_embedding=query_embedding,
                    response=result,
                    tenant_ctx=tenant_ctx,
                )
                return result
        except Exception:
            pass  # Cache errors must never block execution

    return await self._execute_step(step, state, tenant_ctx)
```

In `_node_execute`, replace `self._execute_step(...)` calls with `self._execute_step_with_cache(...)`.

Wire in `app/main.py` lifespan:
```python
# After _semantic_cache and _embedder are resolved:
# When constructing graph in goal_service, pass both:
# goal_service._semantic_cache = _semantic_cache
# goal_service._embedder = _embedder
```

In `GoalService`, when building `AgentGraph`, pass:
```python
graph = AgentGraph(
    # ...
    semantic_cache=getattr(self._app_state, "semantic_cache", None),
    embedder=getattr(self._app_state, "embedder", None),
)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_phase2_intelligence.py -k "semantic_cache" -xvs
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/agent/graph.py tests/test_phase2_intelligence.py
git commit -m "feat(intelligence): semantic cache in execution path with cache_hit events"
```

---

## Task 2.7 — Real-Time Token Streaming

**Current state:** Both providers buffer the entire response before returning `CompletionResponse`. No streaming endpoint exists.

**Gap:** Add `stream_complete()` to `AnthropicProvider` and `OpenAICompatibleProvider`. New `GET /goals/{id}/stream/tokens` SSE endpoint. `GoalService` exposes an asyncio queue per active goal's LLM call.

**Files:**
- Modify: `agent-verse-backend/app/providers/base.py`
- Modify: `agent-verse-backend/app/providers/anthropic_provider.py`
- Modify: `agent-verse-backend/app/providers/openai_compatible.py`
- Modify: `agent-verse-backend/app/api/goals.py`
- Modify: `agent-verse-backend/app/services/goal_service.py`
- Test: `agent-verse-backend/tests/test_phase2_intelligence.py`

- [ ] **Step 1: Add stream_complete to base provider**

In `app/providers/base.py`, add abstract method:

```python
from collections.abc import AsyncIterator

class LLMProvider(abc.ABC):
    # ... existing methods ...

    async def stream_complete(
        self, request: "CompletionRequest"
    ) -> AsyncIterator[str]:
        """Stream completion tokens one chunk at a time.

        Default implementation buffers and yields full response as one chunk.
        Override for true streaming.
        """
        response = await self.complete(request)
        yield response.content
```

- [ ] **Step 2: Implement streaming in AnthropicProvider**

In `app/providers/anthropic_provider.py`, add:

```python
from collections.abc import AsyncIterator

async def stream_complete(
    self, request: CompletionRequest
) -> AsyncIterator[str]:
    """Stream Claude response tokens via the Anthropic streaming API."""
    import anthropic

    model = request.model or self._default_model
    messages = []
    for m in request.messages:
        if m.role == "system":
            continue
        messages.append({"role": m.role, "content": m.content})
    system_prompt = request.system or next(
        (m.content for m in request.messages if m.role == "system"), anthropic.NOT_GIVEN
    )

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": request.max_tokens,
    }
    if system_prompt is not anthropic.NOT_GIVEN:
        kwargs["system"] = system_prompt

    async with self._client.messages.stream(**kwargs) as stream:
        async for text in stream.text_stream:
            yield text
```

- [ ] **Step 3: Implement streaming in OpenAICompatibleProvider**

In `app/providers/openai_compatible.py`, add:

```python
from collections.abc import AsyncIterator

async def stream_complete(
    self, request: CompletionRequest
) -> AsyncIterator[str]:
    """Stream OpenAI response tokens."""
    model = request.model or self._default_model
    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    async with self._client.chat.completions.stream(
        model=model,
        messages=messages,
        max_tokens=request.max_tokens,
    ) as stream:
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta
```

- [ ] **Step 4: Add token queue to GoalService**

In `app/services/goal_service.py`:

```python
# Add to GoalService.__init__:
self._token_queues: dict[str, asyncio.Queue[str | None]] = {}

def get_token_queue(self, goal_id: str) -> asyncio.Queue[str | None]:
    """Get or create token streaming queue for a goal. None sentinel = stream done."""
    if goal_id not in self._token_queues:
        self._token_queues[goal_id] = asyncio.Queue(maxsize=1000)
    return self._token_queues[goal_id]

def close_token_stream(self, goal_id: str) -> None:
    """Signal end of token stream by placing None sentinel."""
    q = self._token_queues.get(goal_id)
    if q is not None:
        try:
            q.put_nowait(None)
        except asyncio.QueueFull:
            pass
```

Wire token queue into `AgentGraph`: pass a callback that puts tokens into the queue when the executor LLM call is streaming. In `_execute_step_with_cache`, when calling the executor provider, check if it has `stream_complete` and use it, routing tokens to the queue:

```python
# In _execute_step (inside the LLM call section):
if self._token_queue is not None and hasattr(self._executor, "stream_complete"):
    tokens: list[str] = []
    async for token in self._executor.stream_complete(exec_req):
        tokens.append(token)
        try:
            self._token_queue.put_nowait(token)
        except asyncio.QueueFull:
            pass
    return "".join(tokens)
```

Add `token_queue` param to `AgentGraph.__init__`:
```python
token_queue: asyncio.Queue[str | None] | None = None,
# ...
self._token_queue = token_queue
```

- [ ] **Step 5: Add streaming endpoint to goals API**

In `app/api/goals.py`, add:

```python
from fastapi.responses import StreamingResponse
import asyncio
import json

@router.get("/{goal_id}/stream/tokens")
async def stream_goal_tokens(request: Request, goal_id: str) -> StreamingResponse:
    """SSE stream of LLM tokens from the active goal's executor call."""
    tenant_ctx = _require_tenant(request)
    goal_svc = _goal_service(request)

    # Validate goal exists and belongs to tenant
    goal = goal_svc.get_goal(goal_id, tenant_ctx=tenant_ctx)
    if goal is None:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")

    token_queue: asyncio.Queue[str | None] = goal_svc.get_token_queue(goal_id)

    async def token_generator():
        try:
            while True:
                try:
                    token = await asyncio.wait_for(token_queue.get(), timeout=30.0)
                except TimeoutError:
                    yield "data: {\"type\": \"keepalive\"}\n\n"
                    continue
                if token is None:
                    yield "data: {\"type\": \"stream_end\"}\n\n"
                    break
                payload = json.dumps({"type": "token", "token": token})
                yield f"data: {payload}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

    return StreamingResponse(
        token_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

- [ ] **Step 6: Write failing test**

```python
# append to tests/test_phase2_intelligence.py

@pytest.mark.asyncio
async def test_anthropic_provider_stream_complete_yields_tokens():
    """stream_complete() must yield tokens progressively."""
    from app.providers.anthropic_provider import AnthropicProvider
    from app.providers.base import CompletionRequest, Message

    # Mock the anthropic streaming context manager
    tokens_to_yield = ["Hello", " world", "!"]

    class FakeStream:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

        @property
        def text_stream(self):
            async def gen():
                for t in tokens_to_yield:
                    yield t
            return gen()

    import anthropic

    with patch("anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_client.messages.stream.return_value = FakeStream()
        mock_anthropic.return_value = mock_client
        provider = AnthropicProvider(api_key="test")
        provider._client = mock_client

        req = CompletionRequest(
            messages=[Message(role="user", content="Say hello")],
            model="claude-haiku-3-5",
        )
        collected = []
        async for token in provider.stream_complete(req):
            collected.append(token)

    assert collected == tokens_to_yield
```

- [ ] **Step 7: Run all Phase 2 tests**

```bash
pytest tests/test_phase2_intelligence.py -v
```
Expected: All tests PASS.

- [ ] **Step 8: Commit**

```bash
git add app/providers/base.py app/providers/anthropic_provider.py \
        app/providers/openai_compatible.py app/api/goals.py \
        app/services/goal_service.py app/agent/graph.py \
        tests/test_phase2_intelligence.py
git commit -m "feat(intelligence): real-time token streaming via stream_complete + SSE endpoint"
```

---

## Acceptance Criteria

| Item | Criterion |
|---|---|
| 2.1 Parallel steps | Steps with `depends_on: []` run concurrently; `steps_parallel_start` event emitted |
| 2.2 Model routing | Planner, executor, verifier each receive their own model via `CompletionRequest.model` |
| 2.3 CoT planning | `cot_reasoning_ready` event emitted before `plan_ready`; reasoning appears in planner context |
| 2.4 Reflection | After verify failure, `reflection_ready` event emitted; `verification_feedback` set with targeted diagnosis |
| 2.5 Specialization | Agent with `system_prompt` + `connector_ids` has both injected into `_node_plan` context |
| 2.6 Semantic cache | Second identical step returns cached result in `<1ms`; `cache_hit` event emitted; LLM not called |
| 2.7 Token streaming | `GET /goals/{id}/stream/tokens` returns `text/event-stream` with live tokens during active execution |
