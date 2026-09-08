# Agent Graph Decomposition Plan
**Target**: Reduce `app/agent/graph.py` (4,760 lines, 49 functions) to a thin orchestrator (~300 lines) by extracting each LangGraph node into its own module under `app/agent/nodes/`.
**Constraint**: All existing tests must pass after each phase. No behaviour changes.

---

## Problem Statement

`app/agent/graph.py` is a god module. It mixes:
- Graph assembly (LangGraph topology)
- 9 LangGraph node implementations (initialize, rag, plan, execute, verify, reflect, routing, …)
- 6 reasoning variant nodes (CoT, self-consistency, tree-of-thoughts, debate, peer-review, supervisor)
- Execution helpers (`_execute_step`, `_execute_step_with_loop`, `_execute_step_with_cache`)
- Module-level parsing utilities (`_parse_json`, `_parse_verifier_response`, `_extract_tool_name`)
- Routing logic (`_route`, `_route_after_execute`)
- Checkpoint helpers, emit, stuck-loop detection

**Impact of the god module:**
- Every PR to any node touches this file → constant merge conflicts
- Unit-testing a single node requires constructing the full `AgentGraph`
- `self._planner`/`self._executor`/`self._verifier` are passed to 20+ methods; unclear which method uses which provider
- 26 cross-cutting imports at the top — any change to governance, memory, or RAG triggers graph.py re-imports

---

## Target Architecture

```
app/agent/
  graph.py          ← BEFORE: 4,760 lines   AFTER: ~350 lines (orchestrator only)
  loop.py           ← unchanged (re-exports AgentGraph as AgentLoop)
  state.py          ← unchanged
  nodes/
    __init__.py     ← exports all node callables
    _helpers.py     ← _parse_json, _parse_verifier_response, _extract_tool_name, _extract_scope_value
                       _build_verifier_summary, _is_high_risk_step, _is_ungrounded_status
    initialize.py   ← _node_initialize  (~74 lines)
    rag.py          ← _node_rag_retrieval, _node_rag_prime, _node_rag_remediate  (~380 lines)
    planner.py      ← _node_plan  (~400 lines)
    executor.py     ← _node_execute, _execute_step_with_loop, _execute_step,
                       _execute_step_with_cache  (~1,820 lines)
    verifier.py     ← _node_verify  (~665 lines)
    reasoning.py    ← _node_think, _node_reflect, _node_self_consistency,
                       _node_tree_of_thoughts, _node_peer_review,
                       _node_supervisor_check, _node_debate, _node_refine  (~350 lines)
    routing.py      ← _route, _route_after_execute, routing_map constants  (~170 lines)
```

Each node module exposes a **`NodeDeps` dataclass** and a **`build(deps) → callable`** factory so it can be instantiated and tested without constructing a full `AgentGraph`.

---

## Node Dependency Contracts

### Pattern: NodeDeps + build()

```python
# app/agent/nodes/planner.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from app.agent.state import GraphState
from app.providers.base import LLMProvider
from app.governance.audit import AuditLog
from app.governance.cost import CostController
from app.governance.policies import PolicyEngine


@dataclass(frozen=True)
class PlannerDeps:
    planner_llm: LLMProvider
    audit_log: AuditLog | None = None
    cost_controller: CostController | None = None
    policy_engine: PolicyEngine | None = None
    structured_planner: bool = False
    enable_cot: bool = False
    # ... only what _node_plan actually reads from self.*


def build(deps: PlannerDeps):
    """Return an async callable suitable for g.add_node('plan', ...)."""

    async def _node_plan(state: GraphState) -> dict[str, Any]:
        # --- formerly self._node_plan body ---
        ...

    return _node_plan
```

### Dependency matrix per node

| Node | Providers needed | Governance | Memory/RAG | Other |
|---|---|---|---|---|
| `initialize` | — | audit_log | exec_memory, long_term_memory | — |
| `rag_retrieval` | embedder | — | knowledge_store, retrieval_gateway | — |
| `plan` | planner | audit_log, cost_controller, policy_engine | exec_memory | model_router, cot_flag |
| `execute` | executor, mcp_client | audit_log, cost_controller, hitl_gateway, permission_matrix, policy_engine | exec_memory, rollback_engine, dedup_cache | circuit_breakers, result_processor |
| `verify` | verifier | audit_log, cost_controller | — | eval_runner, guardrail_checker |
| `reasoning/*` | planner | audit_log | — | enable_* flags |
| `routing` | — | — | — | max_iterations |

---

## Refactor Phases

### Phase 0 — Bootstrap (no behaviour change, all tests pass)
1. Create `app/agent/nodes/__init__.py` (empty)
2. Create `app/agent/nodes/_helpers.py` — move module-level helpers:
   - `_parse_json()`
   - `_parse_verifier_response()`
   - `_extract_tool_name()`
   - `_extract_scope_value()`
   - `_build_verifier_summary()`
   - `_is_high_risk_step()`
   - `_is_ungrounded_status()`
3. In `graph.py`: replace the 7 function bodies with `from app.agent.nodes._helpers import *`
4. Run tests — must be green

### Phase 1 — Extract routing (safest, purely functional)
1. Create `app/agent/nodes/routing.py`:
   - `RouteContext` dataclass (holds `max_iterations`, `enable_reflection`, `enable_cot`, thresholds)
   - `build_router(ctx: RouteContext) → Callable[[GraphState], str]`
   - `build_post_execute_router(ctx: RouteContext) → Callable[[GraphState], str]`
   - `ROUTING_MAP` constant
2. In `AgentGraph._build()`, replace `self._route` / `self._route_after_execute` with `build_router(ctx)` / `build_post_execute_router(ctx)`
3. `AgentGraph._route` and `_route_after_execute` methods become thin delegates that call the node functions
4. Run tests — must be green

### Phase 2 — Extract initialize node
1. Create `app/agent/nodes/initialize.py`:
   - `InitializeDeps` dataclass
   - `build(deps) → async Callable`
2. `AgentGraph._node_initialize` delegates to `nodes.initialize.build(self._make_init_deps())`
3. Add `_make_init_deps(self) → InitializeDeps` helper on `AgentGraph`
4. Run tests — must be green

### Phase 3 — Extract RAG nodes
1. Create `app/agent/nodes/rag.py`:
   - `RAGDeps` dataclass
   - `build_retrieval(deps)`, `build_prime(deps)`, `build_remediate(deps)`
2. Wire into `AgentGraph._build()` via `build_*(self._make_rag_deps())`
3. Run tests — must be green

### Phase 4 — Extract verifier node
1. Create `app/agent/nodes/verifier.py`:
   - `VerifierDeps` dataclass (verifier_llm, audit_log, cost_controller, eval_runner, guardrail_checker)
   - `build(deps) → async Callable`
2. Wire into `AgentGraph._build()`
3. Run tests — must be green

### Phase 5 — Extract reasoning nodes
1. Create `app/agent/nodes/reasoning.py`:
   - `ReasoningDeps` dataclass
   - `build_think(deps)`, `build_reflect(deps)`, `build_self_consistency(deps)`,
     `build_tree_of_thoughts(deps)`, `build_peer_review(deps)`,
     `build_supervisor_check(deps)`, `build_debate(deps)`, `build_refine(deps)`
2. Wire into `AgentGraph._build()` conditionally (same feature-flag logic)
3. Run tests — must be green

### Phase 6 — Extract planner node
1. Create `app/agent/nodes/planner.py`:
   - `PlannerDeps` dataclass
   - `build(deps) → async Callable`
2. Wire into `AgentGraph._build()`
3. Run tests — must be green

### Phase 7 — Extract executor cluster (largest, most risk)
1. Create `app/agent/nodes/executor.py`:
   - `ExecutorDeps` dataclass (executor_llm, mcp_client, audit_log, cost_controller, hitl_gateway, permission_matrix, policy_engine, circuit_breakers, rollback_engine, dedup_cache, result_processor, exec_memory)
   - `build(deps) → async Callable` (for `_node_execute`)
   - Internal functions: `_execute_step_with_loop`, `_execute_step`, `_execute_step_with_cache` (module-scope, taking `deps` as first arg)
2. Wire into `AgentGraph._build()`
3. Run tests — must be green (**highest risk: run full test suite, not just agent tests**)

### Phase 8 — Slim AgentGraph
After all 7 phases, `AgentGraph` retains only:
- `__init__` (same signature — no breaking change)
- `_build()` — assembles graph from node factories (~80 lines instead of current 90-line method plus 4,400 lines of node logic)
- `run()` — public entry point
- `_emit()`, `_write_checkpoint()`, `_load_checkpoint()`, `_check_stuck_loop()`, `_persist_decision_trace()`, `_trigger_self_optimization()`, `_validate_plan_tools()`

Target size after Phase 8: `graph.py` ≤ 400 lines.

### Phase 9 — Fix remaining gaps (separate PRs, not blocking Phase 8)

#### 9a — Decompose `app/main.py` (2,132 lines)
```
app/bootstrap/
  services.py     ← build_services() → dict of app services
  routers.py      ← register_routers(app, services)
  middleware.py   ← register_middleware(app, settings)
  lifespan.py     ← lifespan() context manager
```
`main.py` becomes `create_app()` calling these four functions (~60 lines).

#### 9b — Move hardcoded model names to config
```python
# app/optimization/model_optimizer.py — BEFORE
"verification": ("gpt-4o-mini", "openai", 0.0003, 500)

# AFTER — read from Settings
settings.default_verification_model  = "gpt-4o-mini"
settings.default_verification_provider = "openai"
```
Add to `app/core/config.py`:
```python
default_planner_model: str = "claude-3-5-sonnet-20241022"
default_executor_model: str = "claude-3-5-haiku-20241022"
default_verifier_model: str = "gpt-4o-mini"
default_classification_model: str = "gpt-4o-mini"
```

#### 9c — Replace vision parser if-chain with Provider Protocol
```python
# app/ingestion/parsers/vision_parser.py — BEFORE
if provider == "openai": ...
elif provider == "anthropic": ...

# AFTER — call through LLMProvider Protocol
response = await self._provider.complete(request)
```

#### 9d — Increase FastAPI Depends coverage
Routes that use `request.app.state.*` directly should use typed `Depends` functions:
```python
# app/api/_deps.py
def get_goal_service(request: Request) -> GoalService:
    return request.app.state.goal_service

def get_audit_log(request: Request) -> AuditLog:
    return request.app.state.audit_log
```
All routers import from `_deps.py`. This enables `dependency_overrides` in tests.

---

## Testing Strategy

### During each phase
```bash
# After each phase commit:
cd agent-verse-backend
uv run pytest tests/agent/ tests/api/ tests/e2e/ -q --no-cov --tb=short
```

### After Phase 7 (executor extraction)
```bash
uv run pytest tests/ --ignore=tests/real_e2e --ignore=tests/integration -q --no-cov
```

### New unit tests to add (alongside the refactor)
Each node module should have a corresponding test file:
```
tests/agent/nodes/
  test_helpers.py     ← _parse_json, _parse_verifier_response edge cases
  test_routing.py     ← route() permutations (complete/replan/max_iter/hitl)
  test_initialize.py  ← with/without existing agent_state
  test_planner.py     ← with FakeProvider, structured_planner flag on/off
  test_verifier.py    ← success/failure/partial outcomes
  test_executor.py    ← tool call parsing, HITL gate, rollback trigger
```
Each test instantiates the `NodeDeps` dataclass directly — no `AgentGraph` construction needed.

---

## Non-Goals
- No API changes (public `AgentGraph` constructor signature stays identical)
- No LangGraph topology changes (same graph edges and conditional routing)
- No behaviour changes (zero functional delta — pure structural refactor)
- `loop.py` keeps `AgentLoop = AgentGraph` alias — zero breaking change for callers

---

## Success Criteria
- [ ] `wc -l app/agent/graph.py` ≤ 400
- [ ] `app/agent/nodes/` contains 8 files, each ≤ 500 lines
- [ ] `uv run pytest tests/ --ignore=tests/real_e2e --ignore=tests/integration` passes
- [ ] `uv run mypy app/agent/` passes (no new mypy errors)
- [ ] Each node module is independently importable and testable without constructing `AgentGraph`

---

## Estimated Effort

| Phase | Risk | Effort | Dependencies |
|---|---|---|---|
| 0 — helpers | Low | 30 min | — |
| 1 — routing | Low | 45 min | Phase 0 |
| 2 — initialize | Low | 30 min | Phase 0 |
| 3 — RAG | Low | 45 min | Phase 0 |
| 4 — verifier | Low | 45 min | Phase 0 |
| 5 — reasoning | Low | 1h | Phase 0 |
| 6 — planner | Medium | 1.5h | Phase 0 |
| 7 — executor | High | 3h | Phases 0-6 |
| 8 — slim AgentGraph | Low | 30 min | Phases 0-7 |
| 9a — main.py | Medium | 1.5h | independent |
| 9b — config models | Low | 30 min | independent |
| 9c — vision parser | Low | 30 min | independent |
| 9d — DI coverage | Medium | 2h | independent |
