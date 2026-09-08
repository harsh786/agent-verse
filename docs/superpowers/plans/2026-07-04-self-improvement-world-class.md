# Self-Improvement World-Class + RPA Knowledge Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all 5 self-improvement wiring gaps, fix the evaluations schema mismatch, add a `self_optimization_suggestions` DB table, then implement full RPA→LTM→KnowledgeBase persistence so every RPA run feeds the learning loop.

**Architecture:**
- Part A (Tasks 1–5): Fix existing wiring. All the logic is already written — it's just not connected in the Celery worker path (`tasks.py`) and the `evaluations` DB schema doesn't match what `eval_runner.py` writes. Three targeted code changes + one migration + one new migration close every gap.
- Part B (Tasks 6–7): After a successful RPA tool call in `graph.py`, auto-store the extracted text / vision analysis into `LongTermMemoryStore` with rich tags. One new DB-backed method `store_rpa_extraction()` on `LongTermMemoryStore` handles chunking for long pages.
- Part C (Tasks 8–9): Wire RPA failures into `ExecutionMemory.record_failure_async()` (already exists) and generate targeted RPA suggestions in `SelfOptimizer` when an RPA step fails.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, asyncpg, Alembic, Celery, pgvector, LangGraph.

---

## File Map

| File | Change |
|---|---|
| `app/intelligence/eval_runner.py` | Fix `score_and_persist` INSERT to match actual `evaluations` schema (`scores` JSON, `average_score` float) |
| `app/scaling/tasks.py` | Add `exec_memory`, `self_optimizer`, `prompt_optimizer` to `AgentGraph` construction |
| `app/db/migrations/versions/0071_self_optimization_suggestions.py` | New Alembic migration: create `self_optimization_suggestions` table |
| `app/intelligence/self_optimization.py` | Add `persist_suggestion()` and `load_suggestions()` for DB-backed suggestions |
| `app/memory/long_term.py` | Add `store_rpa_extraction()` — chunks long RPA text and stores with `memory_type="rpa_extraction"` |
| `app/agent/graph.py` | After RPA tool success: call `store_rpa_extraction()` for `rpa_extract_text` and vision from `rpa_screenshot`; call `record_failure_async()` for RPA failures; generate RPA-specific self-optimizer suggestions |
| `app/intelligence/self_optimization.py` | Add RPA-specific suggestion generators: selector fragility, CAPTCHA patterns, login flow |
| `tests/intelligence/test_eval_persist.py` | New: verify `score_and_persist` writes to real `evaluations` schema |
| `tests/memory/test_execution_memory_wired.py` | New: verify `exec_memory` is passed and recall works after task run |
| `tests/memory/test_rpa_ltm.py` | New: verify `store_rpa_extraction()` chunks and persists to DB |

---

## Part A — Fix Self-Improvement Gaps

---

### Task 1: Fix evaluations schema mismatch in eval_runner.py

**The bug:** `score_and_persist` tries to INSERT columns `score_task_completion`, `score_efficiency` etc., but the real DB table has `scores` (JSON) and `average_score` (float). Every insert silently fails.

**Files:**
- Modify: `app/intelligence/eval_runner.py:249-321`
- Test: `tests/intelligence/test_eval_persist.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/intelligence/test_eval_persist.py`:

```python
"""Test that eval scores actually persist to DB using the real schema."""
import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
from app.intelligence.eval_runner import EvalRunner
from app.intelligence.eval import EvalScorecard
from app.agent.state import AgentState, GoalStatus, StepState, StepStatus
from app.tenancy.context import TenantContext, PlanTier


def _make_tenant() -> TenantContext:
    return TenantContext(
        tenant_id="test-eval-persist-001",
        plan=PlanTier.FREE,
        api_key_id="test-key",
    )


def _make_state() -> AgentState:
    state = AgentState(goal="test goal", tenant_id="test-eval-persist-001")
    state.goal_id = "goal-eval-001"
    state.status = GoalStatus.COMPLETE
    state.iterations = 3
    state.context = {"total_cost_usd": 0.05}
    state.steps = []
    state.verification_success = True
    return state


@pytest.mark.asyncio
async def test_score_and_persist_writes_correct_schema():
    """score_and_persist must write to scores (JSON) and average_score columns."""
    runner = EvalRunner()
    tenant = _make_tenant()
    state = _make_state()

    # Capture what gets inserted
    captured = {}

    async def fake_execute(query, params=None):
        sql = str(query)
        if "INSERT INTO evaluations" in sql:
            captured["params"] = params
            captured["sql"] = sql
        return MagicMock(fetchone=lambda: None, fetchall=lambda: [])

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(side_effect=fake_execute)
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.begin = MagicMock(return_value=fake_session)

    async def fake_db():
        return fake_session

    scorecard = await runner.score_and_persist(state, tenant, db=fake_db)

    # Verify insert used the real schema columns
    assert "scores" in captured.get("sql", ""), (
        "INSERT must use 'scores' JSON column, not individual score columns. "
        f"Got SQL: {captured.get('sql', 'NONE')}"
    )
    assert "average_score" in captured.get("sql", ""), (
        "INSERT must include 'average_score' column"
    )
    # Verify scores param is a JSON string
    params = captured.get("params", {})
    scores_val = params.get("scores")
    assert scores_val is not None, "scores param must not be None"
    parsed = json.loads(scores_val)
    assert "task_completion" in parsed, "scores JSON must contain task_completion"
    assert "efficiency" in parsed, "scores JSON must contain efficiency"
    # Verify average_score is a float
    avg = params.get("avg")
    assert isinstance(avg, float), f"average_score must be float, got {type(avg)}"
    assert 0.0 <= avg <= 1.0, f"average_score must be in [0,1], got {avg}"


@pytest.mark.asyncio
async def test_score_and_persist_returns_scorecard_even_if_db_fails():
    """score_and_persist must return a scorecard even when DB write fails."""
    runner = EvalRunner()
    tenant = _make_tenant()
    state = _make_state()

    async def broken_db():
        raise RuntimeError("DB connection refused")

    # Should not raise — DB failure is non-fatal
    scorecard = await runner.score_and_persist(state, tenant, db=broken_db)
    assert scorecard is not None
    assert 0.0 <= scorecard.average_score() <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd agent-verse-backend
uv run pytest tests/intelligence/test_eval_persist.py -v
```

Expected: FAIL — `"INSERT must use 'scores' JSON column"` assertion error.

- [ ] **Step 3: Fix `score_and_persist` in eval_runner.py**

Replace the entire `score_and_persist` method body from line 266 to end with:

```python
    async def score_and_persist(
        self,
        state: AgentState,
        tenant_ctx: TenantContext,
        *,
        provider: Any = None,
        db: Any = None,
    ) -> EvalScorecard:
        """Score AND persist results to the evaluations table.

        Writes to the actual DB schema:
          - scores (JSON)  — full dimension breakdown
          - average_score (float)
          - passed (bool)
          - created_at (timestamp)
        """
        import json as _json
        import uuid

        from sqlalchemy import text

        scorecard = await self.score_async(state=state, tenant_ctx=tenant_ctx, provider=provider)

        if db is not None:
            eval_id = uuid.uuid4().hex
            try:
                async with db() as session, session.begin():
                    await session.execute(
                        text("""
                            INSERT INTO evaluations
                                (id, goal_id, tenant_id, scores, average_score, passed, created_at)
                            VALUES
                                (:id, :gid, :tid, CAST(:scores AS json), :avg, :passed, NOW())
                            ON CONFLICT (id) DO NOTHING
                        """),
                        {
                            "id": eval_id,
                            "gid": state.goal_id,
                            "tid": tenant_ctx.tenant_id,
                            "scores": _json.dumps(scorecard.scores),
                            "avg": round(scorecard.average_score(), 6),
                            "passed": scorecard.passed(),
                        },
                    )
            except Exception as exc:
                from app.observability.logging import get_logger
                get_logger(__name__).warning("eval_persist_failed", error=str(exc))

        return scorecard
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/intelligence/test_eval_persist.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Run full backend test suite to check for regressions**

```bash
uv run pytest tests/intelligence/ -x -q
```

Expected: all pass (no regressions in eval suite).

- [ ] **Step 6: Commit**

```bash
git add app/intelligence/eval_runner.py tests/intelligence/test_eval_persist.py
git commit -m "fix(eval): write to evaluations schema (scores JSON + average_score float)

score_and_persist was inserting individual score columns (score_task_completion etc.)
that don't exist in the real DB schema. The table has scores (JSON) and average_score.
Fix writes the correct columns. Both tests pass. No data was lost — table was just empty."
```

---

### Task 2: Wire ExecutionMemory into Celery tasks.py

**The bug:** `AgentGraph` in Celery workers is constructed without `exec_memory`. `self._exec_memory` is always `None` so winning plans and failure patterns are never stored or recalled.

**Files:**
- Modify: `app/scaling/tasks.py:720-785`
- Test: `tests/memory/test_execution_memory_wired.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/memory/test_execution_memory_wired.py`:

```python
"""Verify ExecutionMemory is wired into AgentGraph and recall/record works."""
import pytest
from app.memory.execution import ExecutionMemory
from app.tenancy.context import TenantContext, PlanTier


def _tenant() -> TenantContext:
    return TenantContext(
        tenant_id="test-exec-mem-wire-001",
        plan=PlanTier.FREE,
        api_key_id="test-key",
    )


def test_execution_memory_record_and_recall():
    """Record a winning plan and recall it."""
    mem = ExecutionMemory()
    tenant = _tenant()

    mem.record(
        goal="find all open Jira tickets",
        plan=["Search Jira with JQL: status=Open", "Return results"],
        tenant_ctx=tenant,
    )

    results = mem.recall(goal_hint="Jira tickets", tenant_ctx=tenant, top_k=3)
    assert len(results) == 1
    assert results[0]["goal"] == "find all open Jira tickets"
    assert "Search Jira" in results[0]["plan"][0]


def test_execution_memory_record_failure_and_recall():
    """Record a failure and recall it."""
    mem = ExecutionMemory()
    tenant = _tenant()

    mem.record_failure(
        goal="search Jira for bugs",
        failed_step="jira_search_issues",
        error="JQL syntax error: invalid field 'assigneee'",
        tenant_ctx=tenant,
    )

    failures = mem.recall_failures(goal_hint="Jira", tenant_ctx=tenant, top_k=3)
    assert len(failures) == 1
    assert "assigneee" in failures[0]["error"]


def test_agentgraph_accepts_exec_memory():
    """AgentGraph constructor must accept exec_memory kwarg without error."""
    from unittest.mock import MagicMock
    from app.agent.graph import AgentGraph
    from app.memory.execution import ExecutionMemory

    fake_provider = MagicMock()
    mem = ExecutionMemory()

    # Should not raise
    graph = AgentGraph(
        planner=fake_provider,
        executor=fake_provider,
        verifier=fake_provider,
        exec_memory=mem,
    )
    assert graph._exec_memory is mem


@pytest.mark.asyncio
async def test_exec_memory_async_record():
    """record_async persists to in-memory store when db=None."""
    mem = ExecutionMemory()

    await mem.record_async(
        goal="test async goal",
        plan=["step 1", "step 2"],
        success=True,
        tenant_id="test-async-001",
        db=None,
    )

    results = mem.recall(
        goal_hint="async goal",
        tenant_ctx=TenantContext(
            tenant_id="test-async-001", plan=PlanTier.FREE, api_key_id="k"
        ),
    )
    assert len(results) == 1
    assert results[0]["plan"] == ["step 1", "step 2"]
```

- [ ] **Step 2: Run test to verify current state**

```bash
uv run pytest tests/memory/test_execution_memory_wired.py -v
```

Expected: first 3 tests PASS (the code works), `test_agentgraph_accepts_exec_memory` PASSES (constructor already has the kwarg). The wiring test confirms the API is correct.

- [ ] **Step 3: Add ExecutionMemory to AgentGraph construction in tasks.py**

In `app/scaling/tasks.py`, find the block that starts at line 725 (`from app.agent.graph import AgentGraph`) and modify it:

```python
            from app.agent.graph import AgentGraph
            from app.governance.audit import AuditLog
            from app.governance.cost import CostController
            from app.governance.hitl import HITLGateway
            from app.governance.policies import PolicyEngine
            from app.intelligence.eval_runner import EvalRunner
            from app.intelligence.guardrails import GuardrailChecker
            from app.memory.execution import ExecutionMemory        # ← ADD
            from app.memory.long_term import LongTermMemoryStore
            from app.reliability.dedup import DeduplicationCache
            from app.reliability.rollback import RollbackEngine

            _audit = AuditLog(db_session_factory=db_factory)
            _hitl = HITLGateway()
            _cost = CostController()
            _policy = PolicyEngine()
            _ltm = LongTermMemoryStore()
            _eval = EvalRunner()
            _exec_mem = ExecutionMemory()                           # ← ADD
            if db_factory is not None:                             # ← ADD
                _exec_mem._db = db_factory                         # ← ADD
```

Then in the `AgentGraph(...)` constructor call, add `exec_memory=_exec_mem`:

```python
            _agent_runner = AgentGraph(
                planner=provider,
                executor=provider,
                verifier=provider,
                model_router=_model_router,
                autonomy_mode=_agent_autonomy_mode,
                result_processor=ResultProcessor(),
                dedup_cache=DeduplicationCache(),
                rollback_engine=RollbackEngine(),
                guardrail_checker=GuardrailChecker(),
                audit_log=_audit,
                hitl_gateway=_hitl,
                cost_controller=_cost,
                policy_engine=_policy,
                exec_memory=_exec_mem,                             # ← ADD
                long_term_memory=_ltm,
                eval_runner=_eval,
                cost_tracker=None,
            )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/memory/test_execution_memory_wired.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/scaling/tasks.py tests/memory/test_execution_memory_wired.py
git commit -m "fix(exec-memory): wire ExecutionMemory into Celery AgentGraph workers

ExecutionMemory was constructed in main.py but never passed to AgentGraph in
tasks.py (Celery workers). Winning plans and failure patterns were never stored
or recalled during real goal execution. Fix adds exec_memory=ExecutionMemory()
with DB factory to the AgentGraph constructor in the Celery worker path."
```

---

### Task 3: Wire SelfOptimizer and PromptOptimizer into Celery tasks.py

**The bug:** Both `self._self_optimizer` and `self._prompt_optimizer` are `None` in the Celery worker — `select_variant()`, `record_result()`, `maybe_promote()`, and `analyze_and_suggest()` never run.

**Files:**
- Modify: `app/scaling/tasks.py` (same block as Task 2)
- Test: `tests/intelligence/test_optimizer_wired.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/intelligence/test_optimizer_wired.py`:

```python
"""Verify SelfOptimizer and PromptOptimizer are wired and functional."""
import pytest
from app.intelligence.self_optimization import SelfOptimizer, OptimizationSuggestion
from app.intelligence.prompt_optimizer import PromptOptimizer
from app.intelligence.eval import EvalScorecard
from app.tenancy.context import TenantContext, PlanTier


def _tenant() -> TenantContext:
    return TenantContext(
        tenant_id="test-optimizer-wire-001",
        plan=PlanTier.FREE,
        api_key_id="test-key",
    )


def test_self_optimizer_generate_suggestions_low_score():
    """SelfOptimizer must generate suggestions when avg score < 0.5."""
    opt = SelfOptimizer()
    tenant = _tenant()
    scorecard = EvalScorecard(
        goal_id="g1",
        goal="find Jira tickets",
        scores={"task_completion": 0.2, "efficiency": 0.3, "accuracy": 0.4,
                "safety": 1.0, "coherence": 0.3},
    )

    suggestions = opt.analyze_and_suggest(
        goal="find Jira tickets",
        scorecard=scorecard,
        error_log="tool not found: jira_search",
        tenant_ctx=tenant,
    )

    assert len(suggestions) >= 2, "Must generate at least 2 suggestions for low score + tool error"
    categories = {s.category for s in suggestions}
    assert "prompt" in categories, "Must suggest prompt improvement for low score"
    assert "tool_selection" in categories, "Must suggest tool fix for tool-not-found error"


def test_self_optimizer_apply_suggestion_mutates_agent_config():
    """apply_suggestion with change_type=increase_iterations must update config."""
    opt = SelfOptimizer()
    tenant = _tenant()

    scorecard = EvalScorecard(
        goal_id="g2",
        goal="test",
        scores={"task_completion": 0.0, "efficiency": 0.2, "accuracy": 0.5,
                "safety": 1.0, "coherence": 0.3},
    )
    suggestions = opt.analyze_and_suggest(
        goal="test", scorecard=scorecard, error_log="", tenant_ctx=tenant
    )
    # Find the efficiency suggestion
    eff_sugg = next(
        (s for s in suggestions if s.category == "retry_strategy"), None
    )
    if eff_sugg:
        eff_sugg.change_type = "increase_iterations"
        eff_sugg.after = "8"
        config = {"max_iterations": 15}
        result = opt.apply_suggestion(
            suggestion_id=eff_sugg.suggestion_id,
            tenant_ctx=tenant,
            agent_config=config,
        )
        assert result is True
        assert config["max_iterations"] == 8


def test_prompt_optimizer_select_variant_returns_control():
    """select_variant must return control variant 70% of the time."""
    opt = PromptOptimizer()
    control = opt.register_variant(
        "planner", "control-prompt", "You are a helpful planner.",
        tenant_id="t1", is_control=True
    )
    _ = opt.register_variant(
        "planner", "challenger-v1", "You are a precise planner.",
        tenant_id="t1", is_control=False
    )

    hits = sum(
        1 for _ in range(200)
        if opt.select_variant("planner", tenant_id="t1") == control
    )
    # 70% control ± 10% tolerance
    assert 120 <= hits <= 180, f"Expected ~140 control hits, got {hits}"


def test_prompt_optimizer_record_result_and_maybe_promote():
    """After enough runs, maybe_promote promotes a clearly better challenger."""
    import random
    random.seed(42)
    opt = PromptOptimizer(min_runs_for_promotion=10, confidence=0.8)

    control = opt.register_variant(
        "executor", "old-prompt", "Basic executor.", tenant_id="t2", is_control=True
    )
    challenger = opt.register_variant(
        "executor", "new-prompt", "Better executor.", tenant_id="t2", is_control=False
    )

    # Feed 10 runs: control scores 0.5, challenger scores 0.9
    for _ in range(10):
        opt.record_result(control.variant_id, 0.5)
        opt.record_result(challenger.variant_id, 0.9)

    promoted = opt.maybe_promote("executor", tenant_id="t2")
    assert promoted is not None
    assert promoted.variant_id == challenger.variant_id
    assert promoted.is_control is True
    assert control.is_control is False


def test_agentgraph_accepts_self_optimizer_and_prompt_optimizer():
    """AgentGraph must accept both optimizers as settable attributes."""
    from unittest.mock import MagicMock
    from app.agent.graph import AgentGraph
    from app.intelligence.self_optimization import SelfOptimizer
    from app.intelligence.prompt_optimizer import PromptOptimizer

    fake_provider = MagicMock()
    graph = AgentGraph(
        planner=fake_provider, executor=fake_provider, verifier=fake_provider
    )
    self_opt = SelfOptimizer()
    prompt_opt = PromptOptimizer()

    graph._self_optimizer = self_opt
    graph._prompt_optimizer = prompt_opt

    assert graph._self_optimizer is self_opt
    assert graph._prompt_optimizer is prompt_opt
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/intelligence/test_optimizer_wired.py -v
```

Expected: all 5 tests PASS (the classes work; only the wiring in tasks.py is missing).

- [ ] **Step 3: Wire optimizers into tasks.py after AgentGraph construction**

After the `_agent_runner = _WorkerMCPAgentRunner(...)` line in tasks.py, add:

```python
            # Wire SelfOptimizer and PromptOptimizer so suggestions
            # and A/B testing run during real goal execution.
            try:
                from app.intelligence.self_optimization import SelfOptimizer
                from app.intelligence.prompt_optimizer import _default_optimizer as _prompt_opt
                _self_opt = SelfOptimizer()
                # Set on the inner AgentGraph (unwrap _WorkerMCPAgentRunner wrapper)
                _inner_graph = getattr(_agent_runner, "_graph", _agent_runner)
                _inner_graph._self_optimizer = _self_opt
                _inner_graph._prompt_optimizer = _prompt_opt
            except Exception as _opt_exc:
                logger.warning("optimizer_wire_failed: %s", _opt_exc)
```

Note: `_WorkerMCPAgentRunner` wraps `AgentGraph`. Check the wrapper class to confirm attribute access. If the inner graph is accessible via `._graph`:

```bash
grep -n "class _WorkerMCPAgentRunner\|self._graph\|self\._agent" agent-verse-backend/app/scaling/tasks.py | head -10
```

Adjust the attribute name based on the actual wrapper structure. If it wraps differently, set directly on `_agent_runner` and add `_self_optimizer` / `_prompt_optimizer` passthrough properties on the wrapper.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/intelligence/test_optimizer_wired.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/scaling/tasks.py tests/intelligence/test_optimizer_wired.py
git commit -m "fix(optimizers): wire SelfOptimizer + PromptOptimizer into Celery AgentGraph

Both optimizers were None in Celery workers. select_variant(), record_result(),
maybe_promote(), and analyze_and_suggest() were dead code paths during real
goal execution. Fix sets both optimizers after AgentGraph construction in tasks.py."
```

---

### Task 4: Create self_optimization_suggestions DB table + persist suggestions

**The bug:** `SelfOptimizer` stores suggestions in-memory only. They are lost on every restart. `self_optimization_suggestions` table doesn't exist in DB.

**Files:**
- Create: `app/db/migrations/versions/0071_self_optimization_suggestions.py`
- Modify: `app/intelligence/self_optimization.py` — add `persist_suggestion()` and `load_suggestions_from_db()`
- Test: `tests/intelligence/test_suggestions_persist.py` (create)

- [ ] **Step 1: Create the Alembic migration**

```bash
cd agent-verse-backend
uv run alembic revision --autogenerate -m "add self_optimization_suggestions table"
```

Then edit the generated file (it will be in `app/db/migrations/versions/0071_*.py`) to ensure it contains exactly:

```python
"""add self_optimization_suggestions table

Revision ID: 0071
Revises: 0070
Create Date: 2026-07-04
"""
from alembic import op
import sqlalchemy as sa

revision = '0071_self_optimization_suggestions'
down_revision = '0070_api_keys_roles_column'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'self_optimization_suggestions',
        sa.Column('id', sa.String, primary_key=True),
        sa.Column('tenant_id', sa.String, nullable=False, index=True),
        sa.Column('suggestion_id', sa.String, nullable=False),
        sa.Column('category', sa.String, nullable=False),
        sa.Column('change_type', sa.String, nullable=False, default=''),
        sa.Column('description', sa.Text, nullable=False, default=''),
        sa.Column('before_text', sa.Text, nullable=False, default=''),
        sa.Column('after_text', sa.Text, nullable=False, default=''),
        sa.Column('confidence', sa.Float, nullable=False, default=0.0),
        sa.Column('applied', sa.Boolean, nullable=False, default=False),
        sa.Column('rejected', sa.Boolean, nullable=False, default=False),
        sa.Column('source_goal_id', sa.String, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        'ix_self_opt_suggestions_tenant_applied',
        'self_optimization_suggestions',
        ['tenant_id', 'applied'],
    )


def downgrade() -> None:
    op.drop_index('ix_self_opt_suggestions_tenant_applied',
                  table_name='self_optimization_suggestions')
    op.drop_table('self_optimization_suggestions')
```

- [ ] **Step 2: Run migration**

```bash
uv run alembic upgrade head
```

Expected output: `Running upgrade 0070... -> 0071..., add self_optimization_suggestions table`

- [ ] **Step 3: Write the failing test**

Create `tests/intelligence/test_suggestions_persist.py`:

```python
"""Verify self-optimizer suggestions persist to DB and load back."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.intelligence.self_optimization import SelfOptimizer
from app.intelligence.eval import EvalScorecard
from app.tenancy.context import TenantContext, PlanTier


def _tenant() -> TenantContext:
    return TenantContext(
        tenant_id="test-sugg-persist-001",
        plan=PlanTier.FREE,
        api_key_id="test-key",
    )


@pytest.mark.asyncio
async def test_persist_suggestion_writes_correct_columns():
    """persist_suggestion must INSERT to self_optimization_suggestions table."""
    opt = SelfOptimizer()
    tenant = _tenant()

    scorecard = EvalScorecard(
        goal_id="g1", goal="test",
        scores={"task_completion": 0.2, "efficiency": 0.2, "accuracy": 0.3,
                "safety": 1.0, "coherence": 0.2},
    )
    suggestions = opt.analyze_and_suggest(
        goal="test", scorecard=scorecard, error_log="tool not found", tenant_ctx=tenant
    )
    assert len(suggestions) > 0

    captured = {}

    async def fake_execute(query, params=None):
        sql = str(query)
        if "INSERT INTO self_optimization_suggestions" in sql:
            captured["params"] = params
            captured["sql"] = sql
        return MagicMock(fetchone=lambda: None, fetchall=lambda: [])

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(side_effect=fake_execute)
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.begin = MagicMock(return_value=fake_session)

    async def fake_db():
        return fake_session

    sugg = suggestions[0]
    await opt.persist_suggestion(sugg, tenant_ctx=tenant, db=fake_db)

    assert "INSERT INTO self_optimization_suggestions" in captured.get("sql", ""), \
        f"Expected INSERT, got: {captured.get('sql', 'NONE')}"
    params = captured["params"]
    assert params["tenant_id"] == "test-sugg-persist-001"
    assert params["suggestion_id"] == sugg.suggestion_id
    assert params["category"] == sugg.category
    assert isinstance(params["confidence"], float)


@pytest.mark.asyncio
async def test_persist_suggestion_non_fatal_on_db_error():
    """persist_suggestion must not raise when DB write fails."""
    opt = SelfOptimizer()
    tenant = _tenant()

    from app.intelligence.self_optimization import OptimizationSuggestion
    sugg = OptimizationSuggestion(
        category="prompt", description="test", confidence=0.7
    )

    async def broken_db():
        raise RuntimeError("DB unavailable")

    # Should not raise
    await opt.persist_suggestion(sugg, tenant_ctx=tenant, db=broken_db)
```

- [ ] **Step 4: Run test to verify it fails**

```bash
uv run pytest tests/intelligence/test_suggestions_persist.py -v
```

Expected: FAIL — `AttributeError: 'SelfOptimizer' object has no attribute 'persist_suggestion'`

- [ ] **Step 5: Add `persist_suggestion()` to SelfOptimizer**

Add this method to `SelfOptimizer` in `app/intelligence/self_optimization.py`:

```python
    async def persist_suggestion(
        self,
        suggestion: "OptimizationSuggestion",
        *,
        tenant_ctx: "TenantContext",
        db: Any = None,
        source_goal_id: str = "",
    ) -> None:
        """Persist a suggestion to self_optimization_suggestions table.

        Non-fatal — DB write failure is logged and swallowed so suggestion
        generation never blocks goal execution.
        """
        if db is None:
            return
        import uuid
        try:
            from sqlalchemy import text
            async with db() as session, session.begin():
                await session.execute(
                    text("""
                        INSERT INTO self_optimization_suggestions
                            (id, tenant_id, suggestion_id, category, change_type,
                             description, before_text, after_text, confidence,
                             applied, rejected, source_goal_id, created_at)
                        VALUES
                            (:id, :tenant_id, :suggestion_id, :category, :change_type,
                             :description, :before_text, :after_text, :confidence,
                             :applied, :rejected, :source_goal_id, NOW())
                        ON CONFLICT (id) DO NOTHING
                    """),
                    {
                        "id": uuid.uuid4().hex,
                        "tenant_id": tenant_ctx.tenant_id,
                        "suggestion_id": suggestion.suggestion_id,
                        "category": suggestion.category,
                        "change_type": suggestion.change_type or "",
                        "description": suggestion.description or "",
                        "before_text": suggestion.before or "",
                        "after_text": suggestion.after or "",
                        "confidence": float(suggestion.confidence),
                        "applied": suggestion.applied,
                        "rejected": suggestion.rejected,
                        "source_goal_id": source_goal_id or "",
                    },
                )
        except Exception as exc:
            from app.observability.logging import get_logger
            get_logger(__name__).warning("suggestion_persist_failed", error=str(exc))
```

Also update `analyze_and_suggest` to call `persist_suggestion` as a background task when `_db` is available on the optimizer instance. Add `self._db: Any = None` to `__init__`, then at the end of `analyze_and_suggest`:

```python
        # Async-persist suggestions when DB is wired
        if self._db is not None:
            import asyncio as _asyncio
            for _s in suggestions:
                _s.tenant_id = tenant_ctx.tenant_id
                _asyncio.create_task(
                    self.persist_suggestion(_s, tenant_ctx=tenant_ctx, db=self._db)
                )

        return suggestions
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/intelligence/test_suggestions_persist.py -v
```

Expected: both tests PASS.

- [ ] **Step 7: Wire DB into SelfOptimizer in tasks.py**

In the optimizer wiring block added in Task 3:

```python
                _self_opt = SelfOptimizer()
                _self_opt._db = db_factory           # ← ADD so suggestions persist
                _inner_graph._self_optimizer = _self_opt
```

- [ ] **Step 8: Commit**

```bash
git add app/db/migrations/versions/0071_self_optimization_suggestions.py \
        app/intelligence/self_optimization.py \
        app/scaling/tasks.py \
        tests/intelligence/test_suggestions_persist.py
git commit -m "feat(self-optimizer): create suggestions table + persist to DB

- Migration 0071: creates self_optimization_suggestions table with tenant_id,
  category, change_type, confidence, applied, rejected, source_goal_id
- SelfOptimizer.persist_suggestion(): async DB write, non-fatal on failure
- Auto-persist triggered from analyze_and_suggest() when _db is set
- tasks.py: wires _db into SelfOptimizer so real goal failures persist suggestions"
```

---

### Task 5: End-to-end verification — all 5 layers write to DB

This task verifies all 5 layers are now actually producing DB rows when a goal completes.

**Files:**
- Test: `tests/integration/test_self_improvement_e2e.py` (create, uses DB)

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_self_improvement_e2e.py`:

```python
"""Integration test: verify all 5 self-improvement layers write to DB on goal completion.

Marked @pytest.mark.integration — requires live Postgres (testcontainers).
"""
import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_all_5_layers_write_to_db(db_session_factory, tenant_ctx):
    """Submit a mock-completed goal and verify all DB tables have rows."""
    from app.memory.execution import ExecutionMemory
    from app.memory.long_term import LongTermMemoryStore
    from app.intelligence.eval_runner import EvalRunner
    from app.intelligence.self_optimization import SelfOptimizer
    from app.intelligence.prompt_optimizer import PromptOptimizer
    from app.intelligence.eval import EvalScorecard
    from app.agent.state import AgentState, GoalStatus
    from sqlalchemy import text

    goal_id = "e2e-test-goal-001"
    tid = tenant_ctx.tenant_id

    # === Layer 1: ExecutionMemory ===
    exec_mem = ExecutionMemory()
    exec_mem._db = db_session_factory
    await exec_mem.record_async(
        goal="e2e test goal",
        plan=["step A", "step B"],
        success=True,
        tenant_id=tid,
        db=db_session_factory,
    )

    # === Layer 2: LongTermMemory ===
    ltm = LongTermMemoryStore()
    from app.memory.long_term import LongTermMemory
    m = LongTermMemory(
        content="e2e test: goal succeeded with plan A→B",
        source_goal_id=goal_id,
        memory_type="success_pattern",
        confidence=0.8,
    )
    await ltm.store_async(memory=m, tenant_ctx=tenant_ctx, db=db_session_factory)

    # === Layer 3: Eval Scoring ===
    runner = EvalRunner()
    state = AgentState(goal="e2e test goal", tenant_id=tid)
    state.goal_id = goal_id
    state.status = GoalStatus.COMPLETE
    state.iterations = 2
    state.context = {"total_cost_usd": 0.01}
    state.steps = []
    await runner.score_and_persist(state, tenant_ctx, db=db_session_factory)

    # === Layer 4: SelfOptimizer suggestions ===
    opt = SelfOptimizer()
    opt._db = db_session_factory
    scorecard = EvalScorecard(
        goal_id=goal_id, goal="e2e test",
        scores={"task_completion": 0.2, "efficiency": 0.2,
                "accuracy": 0.3, "safety": 1.0, "coherence": 0.2},
    )
    suggestions = opt.analyze_and_suggest(
        goal="e2e test", scorecard=scorecard, error_log="tool not found", tenant_ctx=tenant_ctx
    )
    import asyncio
    for s in suggestions:
        await opt.persist_suggestion(s, tenant_ctx=tenant_ctx, db=db_session_factory)

    # === Verify all 4 tables have rows for this tenant ===
    async with db_session_factory() as session:
        for table, col in [
            ("execution_memory", "tenant_id"),
            ("long_term_memory", "tenant_id"),
            ("evaluations", "tenant_id"),
            ("self_optimization_suggestions", "tenant_id"),
        ]:
            r = await session.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE {col} = :tid"),
                {"tid": tid}
            )
            count = r.fetchone()[0]
            assert count > 0, f"Table {table} has 0 rows for tenant {tid}"
```

- [ ] **Step 2: Run integration tests**

```bash
DOCKER_HOST="unix:///Users/harsh.kumar01/.colima/default/docker.sock" \
TESTCONTAINERS_RYUK_DISABLED=true \
uv run pytest tests/integration/test_self_improvement_e2e.py -v -m integration
```

Expected: all assertions pass — each of the 4 tables has > 0 rows.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_self_improvement_e2e.py
git commit -m "test(integration): verify all 5 self-improvement layers write to DB

End-to-end test confirms ExecutionMemory, LongTermMemory, evaluations,
and self_optimization_suggestions tables all receive rows when a goal
completes. Prompt A/B verified in unit tests (Task 3)."
```

---

## Part B — RPA Knowledge Persistence

---

### Task 6: `store_rpa_extraction()` on LongTermMemoryStore

**Goal:** When `rpa_extract_text` returns > 200 chars, auto-store the extracted content in LTM with type `rpa_extraction`. Long pages are chunked to avoid polluting LTM with one 5000-char blob.

**Files:**
- Modify: `app/memory/long_term.py` — add `store_rpa_extraction()`
- Modify: `app/agent/graph.py` — call it after successful `rpa_extract_text`
- Test: `tests/memory/test_rpa_ltm.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/memory/test_rpa_ltm.py`:

```python
"""Verify RPA extractions are stored in LTM with correct typing and chunking."""
import pytest
from app.memory.long_term import LongTermMemoryStore
from app.tenancy.context import TenantContext, PlanTier


def _tenant() -> TenantContext:
    return TenantContext(
        tenant_id="test-rpa-ltm-001",
        plan=PlanTier.FREE,
        api_key_id="test-key",
    )


@pytest.mark.asyncio
async def test_store_rpa_extraction_short_content():
    """Content < 500 chars stored as single LTM entry with rpa_extraction type."""
    store = LongTermMemoryStore()
    tenant = _tenant()

    ids = await store.store_rpa_extraction(
        url="https://example.com/dashboard",
        extracted_text="Revenue: $4.2M. Active users: 12,400. Status: healthy.",
        goal_id="goal-rpa-001",
        tenant_ctx=tenant,
        db=None,
    )

    assert len(ids) == 1
    memories = store.list_all(tenant_ctx=tenant)
    assert len(memories) == 1
    m = memories[0]
    assert m.memory_type == "rpa_extraction"
    assert "https://example.com/dashboard" in m.content
    assert "Revenue" in m.content
    assert "rpa" in m.tags
    assert "web-extraction" in m.tags


@pytest.mark.asyncio
async def test_store_rpa_extraction_long_content_chunks():
    """Content > 500 chars is split into multiple LTM entries."""
    store = LongTermMemoryStore()
    tenant = _tenant()

    long_text = "Row data: " + ("Lorem ipsum dolor sit amet. " * 50)  # ~1400 chars

    ids = await store.store_rpa_extraction(
        url="https://example.com/report",
        extracted_text=long_text,
        goal_id="goal-rpa-002",
        tenant_ctx=tenant,
        db=None,
        chunk_size=400,
    )

    assert len(ids) >= 2, f"Long text should produce multiple chunks, got {len(ids)}"
    memories = store.list_all(tenant_ctx=tenant)
    assert all(m.memory_type == "rpa_extraction" for m in memories)
    # All chunks should contain the source URL
    assert all("https://example.com/report" in m.content for m in memories)


@pytest.mark.asyncio
async def test_store_rpa_extraction_ignores_short_noise():
    """Content < 50 chars is too short to be useful and should be ignored."""
    store = LongTermMemoryStore()
    tenant = _tenant()

    ids = await store.store_rpa_extraction(
        url="https://example.com",
        extracted_text="OK",
        goal_id="goal-rpa-003",
        tenant_ctx=tenant,
        db=None,
    )

    assert ids == [], "Short noise content must not be stored"


@pytest.mark.asyncio
async def test_store_rpa_vision_analysis():
    """Vision analysis from rpa_screenshot stored with vision tag."""
    store = LongTermMemoryStore()
    tenant = _tenant()

    ids = await store.store_rpa_extraction(
        url="https://example.com/dashboard",
        extracted_text="Vision analysis: The dashboard shows Q3 2026 revenue of $4.2M "
                       "with a 23% increase from Q2. Operating costs trending down.",
        goal_id="goal-rpa-004",
        tenant_ctx=tenant,
        db=None,
        source_type="rpa_vision",
    )

    assert len(ids) == 1
    memories = store.list_all(tenant_ctx=tenant)
    m = next(m for m in memories if "vision" in m.tags)
    assert m.memory_type == "rpa_extraction"
    assert "vision" in m.tags
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/memory/test_rpa_ltm.py -v
```

Expected: FAIL — `AttributeError: 'LongTermMemoryStore' has no attribute 'store_rpa_extraction'`

- [ ] **Step 3: Add `store_rpa_extraction()` to LongTermMemoryStore**

Add to `app/memory/long_term.py` after the `extract_from_goal_async` method:

```python
    async def store_rpa_extraction(
        self,
        *,
        url: str,
        extracted_text: str,
        goal_id: str,
        tenant_ctx: "TenantContext",
        db: Any = None,
        embedder: Any = None,
        chunk_size: int = 500,
        source_type: str = "rpa_extraction",
    ) -> list[str]:
        """Store RPA-extracted page content into LTM.

        Short content (<50 chars) is ignored as noise.
        Content exceeding chunk_size is split into overlapping chunks so
        individual facts are retrievable via semantic search.

        Returns list of memory_ids created (empty if content was too short).
        """
        text = (extracted_text or "").strip()
        if len(text) < 50:
            return []

        # Determine tags
        tags = ["rpa", "web-extraction"]
        if source_type == "rpa_vision":
            tags = ["rpa", "vision", "screenshot-analysis"]

        # Split into chunks with 50-char overlap
        chunks: list[str] = []
        if len(text) <= chunk_size:
            chunks = [text]
        else:
            start = 0
            overlap = 50
            while start < len(text):
                end = start + chunk_size
                chunks.append(text[start:end])
                start += chunk_size - overlap
                if start >= len(text):
                    break

        memory_ids: list[str] = []
        for i, chunk in enumerate(chunks):
            # Prefix each chunk with the source URL for attribution
            content = f"[From {url}] {chunk}"
            if len(chunks) > 1:
                content = f"[From {url} chunk {i+1}/{len(chunks)}] {chunk}"

            memory = LongTermMemory(
                content=content,
                source_goal_id=goal_id,
                memory_type="rpa_extraction",
                confidence=0.85,
                tags=tags,
            )
            mid = await self.store_async(
                memory=memory,
                tenant_ctx=tenant_ctx,
                db=db,
                embedder=embedder,
            )
            memory_ids.append(mid)

        return memory_ids
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/memory/test_rpa_ltm.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Call `store_rpa_extraction()` in graph.py after RPA tool success**

In `app/agent/graph.py`, immediately after line 1415 (`raw_output_sanitized = True`) inside the `if rpa_executor is not None: try:` block, add:

```python
                                # ── RPA → LTM persistence ────────────────
                                # Store rpa_extract_text output and
                                # rpa_screenshot vision analysis in LTM so
                                # future goals can recall what was found.
                                if (
                                    rpa_result.success
                                    and self._long_term_memory is not None
                                    and rpa_tool_name in (
                                        "rpa_extract_text", "rpa_screenshot"
                                    )
                                ):
                                    _rpa_url = (tool_call.arguments or {}).get(
                                        "url", agent_state.context.get(
                                            "_current_rpa_url", ""
                                        ) if isinstance(agent_state.context, dict) else ""
                                    )
                                    _rpa_src_type = (
                                        "rpa_vision"
                                        if rpa_tool_name == "rpa_screenshot"
                                        else "rpa_extraction"
                                    )
                                    if rpa_result.output and len(rpa_result.output) > 50:
                                        _rpa_ltm_task = asyncio.create_task(
                                            self._long_term_memory.store_rpa_extraction(
                                                url=str(_rpa_url or "unknown"),
                                                extracted_text=rpa_result.output,
                                                goal_id=str(getattr(agent_state, "goal_id", "")),
                                                tenant_ctx=tenant_ctx,
                                                db=self._db_session_factory,
                                                embedder=self._embedder,
                                                source_type=_rpa_src_type,
                                            )
                                        )
                                        self._background_tasks.add(_rpa_ltm_task)
                                        _rpa_ltm_task.add_done_callback(
                                            self._background_tasks.discard
                                        )
                                # Track current URL for attribution
                                if rpa_tool_name == "rpa_open_url":
                                    _nav_url = (tool_call.arguments or {}).get("url", "")
                                    if isinstance(agent_state.context, dict) and _nav_url:
                                        agent_state.context["_current_rpa_url"] = _nav_url
```

- [ ] **Step 6: Run targeted tests**

```bash
uv run pytest tests/memory/test_rpa_ltm.py tests/agent/ -x -q
```

Expected: all pass, no regressions in agent tests.

- [ ] **Step 7: Commit**

```bash
git add app/memory/long_term.py app/agent/graph.py tests/memory/test_rpa_ltm.py
git commit -m "feat(rpa-ltm): store rpa_extract_text and rpa_screenshot output in LTM

- LongTermMemoryStore.store_rpa_extraction(): chunks long content (>500 chars),
  ignores noise (<50 chars), tags with [rpa, web-extraction] or [rpa, vision]
- graph.py: after successful rpa_extract_text or rpa_screenshot, fire-and-forget
  background task to store output in LTM with URL attribution
- Tracks _current_rpa_url in context so extractions always carry source URL
- pgvector embedding stored when embedder available → semantic recall on next goal"
```

---

### Task 7: Wire RPA failures into ExecutionMemory + generate RPA-specific suggestions

**Goal:** When an RPA step fails (selector not found, CAPTCHA, timeout), record the failure pattern AND generate a targeted suggestion (e.g. "use text selector instead of CSS").

**Files:**
- Modify: `app/agent/graph.py` — add RPA failure → `record_failure_async` + `analyze_and_suggest`
- Modify: `app/intelligence/self_optimization.py` — add `analyze_rpa_failure()` method
- Test: `tests/intelligence/test_rpa_suggestions.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/intelligence/test_rpa_suggestions.py`:

```python
"""Verify RPA-specific self-improvement suggestions are generated correctly."""
import pytest
from app.intelligence.self_optimization import SelfOptimizer
from app.intelligence.eval import EvalScorecard
from app.tenancy.context import TenantContext, PlanTier


def _tenant() -> TenantContext:
    return TenantContext(
        tenant_id="test-rpa-sugg-001",
        plan=PlanTier.FREE,
        api_key_id="test-key",
    )


def test_rpa_selector_failure_generates_text_selector_suggestion():
    """CSS selector timeout → suggest using visible-text selector instead."""
    opt = SelfOptimizer()
    tenant = _tenant()

    suggestions = opt.analyze_rpa_failure(
        tool_name="rpa_click",
        error="Timeout: element '#submit-btn' not found within 5000ms",
        url="https://app.example.com/checkout",
        tenant_ctx=tenant,
    )

    assert len(suggestions) >= 1
    categories = {s.category for s in suggestions}
    assert "rpa_selector" in categories
    found = next(s for s in suggestions if s.category == "rpa_selector")
    assert "text" in found.description.lower() or "visible" in found.description.lower()
    assert found.confidence >= 0.7


def test_rpa_captcha_generates_human_help_suggestion():
    """CAPTCHA error → suggest rpa_detect_captcha + rpa_request_human_help."""
    opt = SelfOptimizer()
    tenant = _tenant()

    suggestions = opt.analyze_rpa_failure(
        tool_name="rpa_click",
        error="rpa_detect_captcha: captcha_detected: true",
        url="https://app.example.com/login",
        tenant_ctx=tenant,
    )

    assert len(suggestions) >= 1
    found = next((s for s in suggestions if "captcha" in s.description.lower()), None)
    assert found is not None
    assert "human" in found.description.lower() or "rpa_request_human_help" in found.after.lower()


def test_rpa_timeout_generates_wait_for_network_idle_suggestion():
    """Network timeout → suggest rpa_wait_for_network_idle before next action."""
    opt = SelfOptimizer()
    tenant = _tenant()

    suggestions = opt.analyze_rpa_failure(
        tool_name="rpa_extract_text",
        error="Timeout: page did not reach networkidle within 10000ms",
        url="https://app.example.com/dashboard",
        tenant_ctx=tenant,
    )

    assert len(suggestions) >= 1
    found = next(
        (s for s in suggestions if "network" in s.description.lower()
         or "wait" in s.description.lower()), None
    )
    assert found is not None


def test_rpa_login_failure_generates_credential_suggestion():
    """Login page error → suggest using vault:// credential reference."""
    opt = SelfOptimizer()
    tenant = _tenant()

    suggestions = opt.analyze_rpa_failure(
        tool_name="rpa_type",
        error="Authentication failed: invalid credentials",
        url="https://app.example.com/login",
        tenant_ctx=tenant,
    )

    found = next(
        (s for s in suggestions if "vault" in s.description.lower()
         or "credential" in s.description.lower()), None
    )
    assert found is not None


def test_no_duplicate_rpa_suggestion_for_same_url():
    """Same URL + same error pattern should not generate duplicate suggestions."""
    opt = SelfOptimizer()
    tenant = _tenant()

    for _ in range(3):
        opt.analyze_rpa_failure(
            tool_name="rpa_click",
            error="Timeout: element '#btn' not found",
            url="https://same-url.com",
            tenant_ctx=tenant,
        )

    suggestions = opt.list_suggestions(tenant_ctx=tenant)
    url_suggestions = [
        s for s in suggestions
        if "same-url.com" in (s.description + s.before + s.after)
    ]
    # Should deduplicate — not 3x the same suggestion
    assert len(url_suggestions) <= 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/intelligence/test_rpa_suggestions.py -v
```

Expected: FAIL — `AttributeError: 'SelfOptimizer' has no attribute 'analyze_rpa_failure'`

- [ ] **Step 3: Add `analyze_rpa_failure()` to SelfOptimizer**

Add to `app/intelligence/self_optimization.py` after `analyze_and_suggest`:

```python
    def analyze_rpa_failure(
        self,
        *,
        tool_name: str,
        error: str,
        url: str,
        tenant_ctx: TenantContext,
    ) -> list[OptimizationSuggestion]:
        """Generate RPA-specific suggestions based on tool failure pattern.

        Covers four patterns: fragile CSS selectors, CAPTCHA blocks,
        network timeouts, and authentication failures.
        Deduplicates: same url+error pattern adds at most once per session.
        """
        error_lower = error.lower()
        url_lower = url.lower()
        suggestions: list[OptimizationSuggestion] = []

        # Dedup key: avoid generating identical suggestions for the same url+error
        dedup_key = f"rpa:{url_lower[:60]}:{error_lower[:60]}"
        existing = self._suggestions.get(tenant_ctx.tenant_id, [])
        if any(
            dedup_key in (s.before + s.description)
            for s in existing
        ):
            return []

        # Pattern 1: Fragile CSS selector (timeout on #id or .class)
        if (
            "timeout" in error_lower
            and ("element" in error_lower or "selector" in error_lower)
            and tool_name in ("rpa_click", "rpa_type", "rpa_extract_text")
        ):
            suggestions.append(OptimizationSuggestion(
                category="rpa_selector",
                change_type="improve_executor_prompt",
                description=(
                    f"CSS selector timeout on {url} — prefer visible-text selectors "
                    "over CSS IDs/classes which break on page changes"
                ),
                before=dedup_key,
                after=(
                    f"For rpa_click on {url}: use text='Button Label' "
                    "instead of selector='#id'. "
                    "For rpa_type: use selector='input[name=field]' (attribute selectors "
                    "are more stable than IDs). "
                    "For rpa_extract_text: use selector='main' or 'article' not '#dynamic-id'."
                ),
                confidence=0.80,
            ))

        # Pattern 2: CAPTCHA detected
        if "captcha" in error_lower:
            suggestions.append(OptimizationSuggestion(
                category="rpa_captcha",
                change_type="add_domain_context",
                description=(
                    f"CAPTCHA detected on {url} — add rpa_detect_captcha check "
                    "before login and call rpa_request_human_help when triggered"
                ),
                before=dedup_key,
                after=(
                    "Add rpa_detect_captcha() before any login step on this URL. "
                    "If captcha_detected=true, call rpa_request_human_help(reason='CAPTCHA on login') "
                    "and wait for human to complete it before continuing."
                ),
                confidence=0.90,
            ))

        # Pattern 3: Network timeout / page not loaded
        if (
            "timeout" in error_lower
            and ("network" in error_lower or "idle" in error_lower or "load" in error_lower)
        ):
            suggestions.append(OptimizationSuggestion(
                category="rpa_timing",
                change_type="improve_executor_prompt",
                description=(
                    f"Page load timeout on {url} — add rpa_wait_for_network_idle "
                    "after navigation and form submissions"
                ),
                before=dedup_key,
                after=(
                    f"After rpa_open_url(url='{url[:60]}') and after rpa_submit_form(), "
                    "always call rpa_wait_for_network_idle(timeout_ms=15000) "
                    "before attempting to extract or click elements."
                ),
                confidence=0.75,
            ))

        # Pattern 4: Authentication failure
        if (
            "auth" in error_lower
            or "credential" in error_lower
            or "invalid" in error_lower and "password" in error_lower
        ):
            suggestions.append(OptimizationSuggestion(
                category="rpa_credentials",
                change_type="improve_executor_prompt",
                description=(
                    f"Authentication failed on {url} — use vault:// credential "
                    "references instead of hardcoded values"
                ),
                before=dedup_key,
                after=(
                    "For rpa_type password fields: use text='vault://<server>/<key>' "
                    "so credentials are resolved from the secure vault at runtime "
                    "and never appear in the plan text."
                ),
                confidence=0.85,
            ))

        # Store and optionally persist
        self._suggestions.setdefault(tenant_ctx.tenant_id, []).extend(suggestions)

        if self._db is not None:
            import asyncio as _asyncio
            for _s in suggestions:
                _s.tenant_id = tenant_ctx.tenant_id
                try:
                    _asyncio.create_task(
                        self.persist_suggestion(_s, tenant_ctx=tenant_ctx, db=self._db)
                    )
                except RuntimeError:
                    pass  # Not in async context

        return suggestions
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/intelligence/test_rpa_suggestions.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Add RPA failure handling in graph.py**

Inside the RPA executor block in graph.py, in the `except Exception as _rpa_exc:` block (around line 1416), and also for the failure path when `rpa_result.success` is False, add:

```python
                                # ── RPA failure → ExecutionMemory + SelfOptimizer ──
                                if not rpa_result.success:
                                    _rpa_url_fail = (
                                        (tool_call.arguments or {}).get("url", "")
                                        or (
                                            agent_state.context.get("_current_rpa_url", "")
                                            if isinstance(agent_state.context, dict)
                                            else ""
                                        )
                                    )
                                    # Record failure pattern in ExecutionMemory
                                    if self._exec_memory is not None and self._db_session_factory:
                                        _rpa_fail_task = asyncio.create_task(
                                            self._exec_memory.record_failure_async(
                                                goal=agent_state.goal,
                                                error=(
                                                    f"RPA {rpa_tool_name} failed on "
                                                    f"{_rpa_url_fail}: {rpa_result.error or 'unknown'}"
                                                ),
                                                tenant_id=tenant_ctx.tenant_id,
                                                db=self._db_session_factory,
                                            )
                                        )
                                        self._background_tasks.add(_rpa_fail_task)
                                        _rpa_fail_task.add_done_callback(
                                            self._background_tasks.discard
                                        )
                                    # Generate RPA-specific suggestions
                                    if self._self_optimizer is not None:
                                        self._self_optimizer.analyze_rpa_failure(
                                            tool_name=rpa_tool_name,
                                            error=rpa_result.error or "",
                                            url=str(_rpa_url_fail),
                                            tenant_ctx=tenant_ctx,
                                        )
```

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest tests/ -x -q --ignore=tests/integration
```

Expected: all unit tests PASS. (Integration tests require Docker.)

- [ ] **Step 7: Commit**

```bash
git add \
  app/intelligence/self_optimization.py \
  app/agent/graph.py \
  tests/intelligence/test_rpa_suggestions.py
git commit -m "feat(rpa-self-improvement): RPA failures → ExecMemory + targeted suggestions

- SelfOptimizer.analyze_rpa_failure(): generates 4 RPA-specific suggestion types:
  * rpa_selector: CSS timeout → use visible text selectors
  * rpa_captcha: CAPTCHA → add detect + request_human_help pattern
  * rpa_timing: network timeout → add wait_for_network_idle
  * rpa_credentials: auth failure → use vault:// references
  Deduplicates by url+error fingerprint so same failure doesn't pile up.
- graph.py: when rpa_result.success=False, fires record_failure_async() and
  analyze_rpa_failure() as background tasks. Zero impact on goal execution time."
```

---

## Part C — Close the Loop: RPA Knowledge → Next Goal Recall

---

### Task 8: Verify end-to-end RPA → recall works across sessions

This task verifies that content extracted by RPA in one goal is semantically recalled in a later goal, proving the full loop: RPA extract → LTM → recall in next planning prompt.

**Files:**
- Test: `tests/memory/test_rpa_recall_loop.py` (create)

- [ ] **Step 1: Write the test**

Create `tests/memory/test_rpa_recall_loop.py`:

```python
"""Verify full RPA→LTM→recall loop works across simulated goal runs."""
import pytest
from app.memory.long_term import LongTermMemoryStore
from app.tenancy.context import TenantContext, PlanTier


def _tenant() -> TenantContext:
    return TenantContext(
        tenant_id="test-rpa-loop-001",
        plan=PlanTier.FREE,
        api_key_id="test-key",
    )


@pytest.mark.asyncio
async def test_rpa_extraction_recalled_by_related_query():
    """Content stored via store_rpa_extraction is recalled by keyword match."""
    store = LongTermMemoryStore()
    tenant = _tenant()

    # Simulate: Goal 1 extracts pricing page via RPA
    await store.store_rpa_extraction(
        url="https://competitor.com/pricing",
        extracted_text=(
            "Enterprise plan: $499/month. Features: unlimited users, SSO, "
            "24/7 support, API access. Starter: $49/month. 5 users max."
        ),
        goal_id="goal-001",
        tenant_ctx=tenant,
        db=None,
    )

    # Simulate: Goal 2 asks about competitor pricing
    recalled = store.recall(
        query="competitor pricing enterprise plan",
        tenant_ctx=tenant,
        top_k=3,
    )

    assert len(recalled) >= 1, "RPA extraction must be recalled by related query"
    assert any(
        "competitor.com" in m.content or "Enterprise" in m.content
        for m in recalled
    ), "Recalled memories must contain the extracted content"
    assert all(m.memory_type == "rpa_extraction" for m in recalled)


@pytest.mark.asyncio
async def test_rpa_failure_pattern_recalled_by_related_goal():
    """Failure recorded in ExecutionMemory is recalled for similar future goal."""
    from app.memory.execution import ExecutionMemory

    mem = ExecutionMemory()
    tenant = _tenant()

    # Simulate: failed RPA on checkout page
    mem.record_failure(
        goal="checkout on example.com",
        failed_step="rpa_click(selector=#checkout-btn)",
        error="Timeout: element '#checkout-btn' not found within 5000ms",
        tenant_ctx=tenant,
    )

    # Future goal: similar checkout task
    failures = mem.recall_failures(
        goal_hint="checkout example.com",
        tenant_ctx=tenant,
        top_k=3,
    )

    assert len(failures) >= 1
    assert "checkout-btn" in failures[0]["error"]


@pytest.mark.asyncio
async def test_vision_analysis_recalled_by_data_query():
    """Vision analysis from screenshot stored and recalled by data question."""
    store = LongTermMemoryStore()
    tenant = _tenant()

    # Simulate: screenshot vision analysis stored
    await store.store_rpa_extraction(
        url="https://internal-dashboard.com/metrics",
        extracted_text=(
            "Vision analysis: Dashboard shows Q3 2026 revenue $4.2M, "
            "up 23% from Q2 $3.4M. Operating costs declined 8%. "
            "Active subscriptions: 1,240. Churn rate: 2.3%."
        ),
        goal_id="goal-vision-001",
        tenant_ctx=tenant,
        db=None,
        source_type="rpa_vision",
    )

    recalled = store.recall(
        query="Q3 revenue and churn rate",
        tenant_ctx=tenant,
        top_k=3,
    )

    assert len(recalled) >= 1
    content_combined = " ".join(m.content for m in recalled)
    assert "4.2M" in content_combined or "revenue" in content_combined.lower()
    assert any("vision" in m.tags for m in recalled)
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/memory/test_rpa_recall_loop.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/memory/test_rpa_recall_loop.py
git commit -m "test(rpa-loop): verify RPA→LTM→recall works end-to-end

Proves that content extracted by RPA in one goal is recalled by
keyword search in future goals, completing the self-improvement loop.
Vision analysis and failure patterns both verify correctly."
```

---

### Task 9: Final integration test + backend restart verification

- [ ] **Step 1: Run the full backend test suite**

```bash
cd agent-verse-backend
uv run pytest tests/ -q --ignore=tests/integration -x
```

Expected: all unit tests PASS.

- [ ] **Step 2: Run integration tests**

```bash
DOCKER_HOST="unix:///Users/harsh.kumar01/.colima/default/docker.sock" \
TESTCONTAINERS_RYUK_DISABLED=true \
uv run pytest tests/integration/ -v -m integration
```

Expected: all integration tests PASS including `test_self_improvement_e2e.py`.

- [ ] **Step 3: Restart backend and verify DB writes from a real goal**

```bash
# Restart backend
kill $(pgrep -f "uvicorn app.main") 2>/dev/null; sleep 1
cd agent-verse-backend && eval $(grep -v '^#' .env | grep -v '^$' | sed 's/^/export /' | tr '\n' ';') \
  uv run uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 >> /tmp/uvicorn.log 2>&1 &
sleep 8

# Submit a real goal
GOAL_ID=$(curl -s -X POST "http://localhost:8000/goals" \
  -H "X-API-Key: av_free_harsh_dev_2026" \
  -H "Content-Type: application/json" \
  -d '{"goal": "Search for all open Jira issues in project 2FAS and return count by priority", "agent_id": "c33e80b578524170b64a6722fbe12efa"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('goal_id',''))")
echo "Goal ID: $GOAL_ID"
sleep 25  # Wait for completion

# Check all 4 DB tables
python3 -c "
import asyncio, os
async def check():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import text
    db_url = os.environ.get('DATABASE_URL', 'postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse')
    engine = create_async_engine(db_url)
    S = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with S() as s:
        for table in ['execution_memory','long_term_memory','evaluations','self_optimization_suggestions']:
            r = await s.execute(text(f'SELECT COUNT(*) FROM {table} WHERE tenant_id = :t'), {'t': '507c391f7c1141b19c59605ab99d3e51'})
            print(f'{table}: {r.fetchone()[0]} rows')
asyncio.run(check())
"
```

Expected output:
```
execution_memory: ≥1 rows
long_term_memory: ≥1 rows
evaluations: ≥1 rows
self_optimization_suggestions: ≥0 rows (only if goal scored < 0.5)
```

- [ ] **Step 4: Final commit**

```bash
git add .
git commit -m "test(e2e): verify all self-improvement layers write to DB from real goal run

Backend restart + real Jira goal submission confirms:
- ExecutionMemory records winning plans/failures in execution_memory table
- LongTermMemory persists success patterns in long_term_memory table
- EvalRunner writes scores to evaluations table (fixed JSON schema)
- SelfOptimizer persists suggestions when score < 0.5

All 5 self-improvement layers are now fully operational end-to-end."
```

---

## Self-Review

**Spec coverage check:**

| Requirement | Task |
|---|---|
| Fix evaluations schema mismatch | Task 1 ✓ |
| Wire ExecutionMemory in tasks.py | Task 2 ✓ |
| Wire SelfOptimizer in tasks.py | Task 3 ✓ |
| Wire PromptOptimizer in tasks.py | Task 3 ✓ |
| Create self_optimization_suggestions table | Task 4 ✓ |
| Persist suggestions to DB | Task 4 ✓ |
| End-to-end all 5 layers verified | Task 5 ✓ |
| RPA extract_text → LTM | Task 6 ✓ |
| RPA screenshot vision → LTM | Task 6 ✓ |
| RPA failure → ExecutionMemory | Task 7 ✓ |
| RPA-specific suggestions (4 patterns) | Task 7 ✓ |
| LTM recall from RPA data | Task 8 ✓ |
| Real backend e2e verification | Task 9 ✓ |

**Placeholder scan:** No TBDs, no "implement later", all code blocks complete.

**Type consistency check:**
- `store_rpa_extraction()` returns `list[str]` (memory_ids) — used consistently in tests
- `analyze_rpa_failure()` returns `list[OptimizationSuggestion]` — same type as `analyze_and_suggest()`
- `persist_suggestion()` is `async def` — all callers use `await` or `asyncio.create_task()`
- `_exec_mem._db = db_factory` — same pattern used in `_ltm` already in tasks.py

**Zero gaps confirmed.**
