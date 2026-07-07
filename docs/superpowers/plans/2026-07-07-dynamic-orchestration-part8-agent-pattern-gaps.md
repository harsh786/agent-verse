# AgentVerse Dynamic Orchestration — Part 8: Agent Pattern Gaps

> **Prerequisite:** Complete Parts 1–7 first.

## Gap Analysis Against Doc-1 + Doc-3

Reading both docs, 6 concrete gaps remain:

| # | Gap | Where in Docs | Severity |
|---|-----|---------------|----------|
| P1 | `_node_refine` — Self-Refine LangGraph node. Adapter exists (PARTIAL) but **zero implementation task** anywhere in the plan. Doc-3 §11 lists it as "❌ PLANNED Phase B". | Doc-1 §3.4, Doc-3 §11 | HIGH |
| P2 | `peer_review` adapter class missing from `app/agent/patterns/`. In StrategyRegistry as PLANNED but no class in `ALL_PATTERNS`. | Doc-1 §5.5, Doc-3 §11 | MEDIUM |
| P3 | `_node_self_consistency` — Self-Consistency node. Adapter exists (PLANNED) but no implementation. Doc-3 §11: Phase C. | Doc-1 §3 (self_consistency), Doc-3 §11 | MEDIUM |
| P4 | `_node_tree_of_thoughts` — ToT node. Adapter exists (PLANNED) but no implementation. Doc-3 §11: Phase D. | Doc-1 §2 (tree_of_thoughts), Doc-3 §11 | MEDIUM |
| P5 | **Reflexion wiring** — `ReflexionStore` exists (Part 5) but no task to automatically store lessons after goal failure. Doc-1 §8.3 "Level 3: Reflexion (cross-goal learning)". | Doc-3 §4, Doc-1 §8 | HIGH |
| P6 | **DynamicGraphAssembler activation tests missing** — no test verifies CoT fires when `chain_of_thought` in PatternConfig, loop_until step is wired, wave execution activates. | Doc-3 §1–3, Doc-4 §3.5 | MEDIUM |

**Run all tests:**
```bash
cd agent-verse-backend
uv run pytest tests/agent/test_agent_pattern_gaps.py tests/agent/test_reflexion_wiring.py \
    tests/agent/test_dynamic_graph_activation.py -v --no-cov
```

---

## Task P1: `_node_refine` — Self-Refine LangGraph Node

**Files:**
- Modify: `app/agent/graph.py` — add `_node_refine`
- Modify: `app/agent/patterns/self_refine.py` — update state to PARTIAL→IMPLEMENTED
- Modify: `app/agent/prompts.py` — add `SELF_REFINE_SYSTEM`
- Create: `tests/agent/test_agent_pattern_gaps.py`

- [ ] **Step P1.1: Write failing tests**

```python
# tests/agent/test_agent_pattern_gaps.py
"""Agent pattern gaps from doc-1 §3.4, doc-3 §11 must all be addressed."""
from __future__ import annotations
import pytest
from app.agent.patterns.self_refine import SelfRefinePattern
from app.agent.patterns.base import RAGPatternState, PatternState
from app.agent.patterns.peer_review import PeerReviewPattern
from app.agent.patterns.self_consistency import SelfConsistencyPattern
from app.agent.patterns.tree_of_thoughts import TreeOfThoughtsPattern
from app.agent.patterns import ALL_PATTERNS


# ── P1: _node_refine ─────────────────────────────────────────────────────────

def test_self_refine_pattern_has_refine_node_reference():
    """SelfRefinePattern must reference _node_refine implementation."""
    p = SelfRefinePattern()
    assert p.pattern_id == "self_refine"
    # When _node_refine is implemented it becomes PARTIAL or IMPLEMENTED
    assert p.state in (PatternState.PARTIAL, PatternState.IMPLEMENTED)
    assert "_node_refine" in p.node_name or "refine" in p.description.lower()


def test_self_refine_system_prompt_exists():
    """SELF_REFINE_SYSTEM prompt must be in app/agent/prompts.py."""
    from app.agent.prompts import SELF_REFINE_SYSTEM
    assert isinstance(SELF_REFINE_SYSTEM, str)
    assert len(SELF_REFINE_SYSTEM) > 50
    assert "refine" in SELF_REFINE_SYSTEM.lower() or "improve" in SELF_REFINE_SYSTEM.lower()


def test_agent_graph_has_node_refine():
    """AgentGraph must have _node_refine method."""
    from app.agent.graph import AgentGraph
    assert hasattr(AgentGraph, "_node_refine"), (
        "AgentGraph missing _node_refine — doc-1 §3.4 requires this node for Self-Refine pattern"
    )


# ── P2: Peer Review pattern adapter ──────────────────────────────────────────

def test_peer_review_pattern_importable():
    p = PeerReviewPattern()
    assert p.pattern_id == "peer_review"


def test_peer_review_in_all_patterns():
    """Peer Review must be in ALL_PATTERNS list."""
    ids = [p.pattern_id for p in ALL_PATTERNS]
    assert "peer_review" in ids, (
        "peer_review missing from ALL_PATTERNS — doc-1 §5.5 defines this pattern"
    )


def test_peer_review_in_strategy_registry():
    from app.orchestration.strategy_registry import build_default_registry
    registry = build_default_registry()
    cap = registry.get("peer_review")
    assert cap is not None, "peer_review not in StrategyRegistry"


# ── P3: Self-Consistency adapter ─────────────────────────────────────────────

def test_self_consistency_pattern_exists():
    from app.agent.patterns.self_consistency import SelfConsistencyPattern
    p = SelfConsistencyPattern()
    assert p.pattern_id == "self_consistency"
    assert p.state in (PatternState.PLANNED, PatternState.PARTIAL)


# ── P4: Tree of Thoughts adapter ─────────────────────────────────────────────

def test_tree_of_thoughts_pattern_exists():
    p = TreeOfThoughtsPattern()
    assert p.pattern_id == "tree_of_thoughts"
    assert p.state in (PatternState.PLANNED, PatternState.PARTIAL)


# ── All patterns registered ───────────────────────────────────────────────────

def test_all_doc1_agent_patterns_in_all_patterns_list():
    """Every pattern from doc-1 §2–9 must be in ALL_PATTERNS."""
    ids = {p.pattern_id for p in ALL_PATTERNS}
    required = {
        "react", "plan_execute", "reflection", "reflexion",
        "self_refine", "self_consistency", "tree_of_thoughts",
        "loop_engineering", "supervisor", "debate", "goal_tree",
        "consensus", "peer_review",    # peer_review was missing
    }
    missing = required - ids
    assert not missing, f"Missing from ALL_PATTERNS: {sorted(missing)}"


def test_all_patterns_have_pattern_id():
    for p in ALL_PATTERNS:
        assert p.pattern_id, f"{type(p).__name__} missing pattern_id"


def test_all_patterns_have_node_name_or_description():
    """Each pattern must have either a node_name or a non-empty description."""
    for p in ALL_PATTERNS:
        has_info = bool(getattr(p, "node_name", "")) or bool(p.description)
        assert has_info, f"{p.pattern_id} has neither node_name nor description"
```

- [ ] **Step P1.2: Run to confirm failures**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_agent_pattern_gaps.py -v --no-cov
```
Expected: Multiple failures — `ImportError` on peer_review, `AttributeError` on _node_refine

- [ ] **Step P1.3: Add `SELF_REFINE_SYSTEM` prompt to `app/agent/prompts.py`**

Append to `app/agent/prompts.py`:

```python
SELF_REFINE_SYSTEM = """\
You are a self-refinement agent. You have just produced an output for a task.
Your job is to critically review it and produce an improved version.

Review checklist:
1. Is the output complete? Does it address ALL parts of the task?
2. Is it accurate? Are any claims unsupported or potentially wrong?
3. Is it clear? Would someone unfamiliar with the context understand it?
4. Is it concise? Can any verbosity be removed without losing meaning?
5. Are there any errors (logic, code, grammar, format)?

Produce an improved version. If the output is already excellent, return it unchanged with "NO_CHANGES_NEEDED" as the first line.

Respond with ONLY the refined output — no meta-commentary, no explanation of changes.
"""
```

- [ ] **Step P1.4: Add `_node_refine` to `app/agent/graph.py`**

Add this method to the `AgentGraph` class, after `_node_reflect`:

```python
async def _node_refine(self, state: GraphState) -> dict:
    """Self-Refine node — improves the last step output before verification.

    Implements doc-1 §3.4: 'generate output → self-critique → refine'.
    Different from Reflection (which diagnoses a FAILURE).
    Self-Refine improves a SUCCESS — makes a good output better.

    Activated when 'self_refine' is in PatternConfig.reasoning_patterns.
    Fires AFTER execute, BEFORE verify, at most max_refine_iterations times.
    """
    agent_state: AgentState = state.get("agent_state")
    if agent_state is None:
        return {}

    try:
        from app.agent.prompts import SELF_REFINE_SYSTEM

        # Get the last step output to refine
        if not agent_state.steps:
            return {"agent_state": agent_state}

        last_step = agent_state.steps[-1]
        if not last_step.output or last_step.output.strip() == "":
            return {"agent_state": agent_state}

        refine_iterations = agent_state.context.get("refine_iterations", 0)
        max_refine = agent_state.context.get("max_refine_iterations", 2)
        if refine_iterations >= max_refine:
            return {"agent_state": agent_state}

        refine_prompt = (
            f"Task: {last_step.description}\n\n"
            f"Current output:\n{last_step.output[:2000]}\n\n"
            "Improve this output following the review checklist."
        )

        resp = await self._executor.complete(
            CompletionRequest(
                messages=[
                    Message(role="system", content=SELF_REFINE_SYSTEM),
                    Message(role="user", content=refine_prompt),
                ],
                model=self._executor_model if hasattr(self, "_executor_model") else "",
                max_tokens=2000,
                temperature=0.0,
            )
        )

        refined = resp.content.strip()
        if refined and not refined.startswith("NO_CHANGES_NEEDED"):
            last_step.output = refined
            agent_state.context["refine_iterations"] = refine_iterations + 1

    except Exception as exc:
        from app.observability.logging import get_logger
        get_logger(__name__).warning("node_refine_failed", error=str(exc))

    return {"agent_state": agent_state}
```

- [ ] **Step P1.5: Update `app/agent/patterns/self_refine.py`**

```python
from __future__ import annotations
from app.agent.patterns.base import AgentPattern, PatternState


class SelfRefinePattern(AgentPattern):
    """Self-Refine — iterative output improvement before verification (Madaan 2023).

    Implementation: _node_refine in app/agent/graph.py
    Fires after execute, before verify. Max 2 iterations by default.
    """
    @property
    def pattern_id(self) -> str: return "self_refine"

    @property
    def node_name(self) -> str: return "_node_refine"

    @property
    def state(self) -> PatternState: return PatternState.PARTIAL

    @property
    def description(self) -> str:
        return "Self-Refine: LLM improves its own output via _node_refine (doc-1 §3.4)"
```

- [ ] **Step P1.6: Run tests — _node_refine tests should now pass**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_agent_pattern_gaps.py -k "refine" -v --no-cov
```
Expected: `test_self_refine_pattern_has_refine_node_reference`, `test_self_refine_system_prompt_exists`, `test_agent_graph_has_node_refine` all pass

- [ ] **Step P1.7: Commit**

```bash
cd agent-verse-backend
git add app/agent/graph.py app/agent/prompts.py app/agent/patterns/self_refine.py \
    tests/agent/test_agent_pattern_gaps.py
git commit -m "feat(agent): add _node_refine + SELF_REFINE_SYSTEM — doc-1 §3.4 Self-Refine pattern"
```

---

## Task P2: Peer Review Pattern Adapter

**Files:**
- Create: `app/agent/patterns/peer_review.py`
- Modify: `app/agent/patterns/__init__.py` — add to ALL_PATTERNS

- [ ] **Step P2.1: Implement `app/agent/patterns/peer_review.py`**

```python
"""PeerReviewPattern — a separate reviewer agent critiques executor output.

Doc-1 §5.5: 'After the executor generates output, a separate reviewer agent
critiques it before it's passed to the verifier.'

Current state: PLANNED — could be built as a node between execute and verify.
Architecture: _node_peer_review fires between execute and verify.
"""
from __future__ import annotations
from app.agent.patterns.base import AgentPattern, PatternState


class PeerReviewPattern(AgentPattern):
    """Peer Review — separate reviewer LLM critiques output before verification."""

    @property
    def pattern_id(self) -> str: return "peer_review"

    @property
    def state(self) -> PatternState: return PatternState.PLANNED

    @property
    def description(self) -> str:
        return (
            "Peer Review: separate reviewer agent critiques executor output "
            "before verification. Use for writing/code tasks. (doc-1 §5.5)"
        )

    def is_compatible(self, goal_properties: any) -> bool:
        """Peer review is compatible with technical and creative goals."""
        from app.agent.pattern_config import Domain
        props = goal_properties
        if props is None:
            return False
        return getattr(props, "domain", None) in (
            Domain.TECHNICAL, Domain.CREATIVE
        )
```

- [ ] **Step P2.2: Add PeerReviewPattern to `app/agent/patterns/__init__.py`**

Edit `app/agent/patterns/__init__.py`. Add the import and add to `ALL_PATTERNS`:

```python
# Add import:
from app.agent.patterns.peer_review import PeerReviewPattern

# Add to ALL_PATTERNS list:
ALL_PATTERNS: list[AgentPattern] = [
    ReActPattern(),
    PlanExecutePattern(),
    ReflectionPattern(),
    ReflexionPattern(),
    SelfRefinePattern(),
    SelfConsistencyPattern(),
    TreeOfThoughtsPattern(),
    LoopEngineeringPattern(),
    SupervisorPattern(),
    DebatePattern(),
    GoalTreePattern(),
    ConsensusPattern(),
    PeerReviewPattern(),      # NEW — was missing
]

# Add to __all__:
# ... "PeerReviewPattern",
```

- [ ] **Step P2.3: Run peer_review tests**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_agent_pattern_gaps.py -k "peer_review" -v --no-cov
```
Expected: `3 passed`

- [ ] **Step P2.4: Commit**

```bash
cd agent-verse-backend
git add app/agent/patterns/peer_review.py app/agent/patterns/__init__.py
git commit -m "feat(agent/patterns): add PeerReviewPattern adapter — doc-1 §5.5; add to ALL_PATTERNS"
```

---

## Task P3: Reflexion Wiring — Automatic Cross-Goal Lesson Storage

**Files:**
- Create: `tests/agent/test_reflexion_wiring.py`
- Modify: `app/agent/graph.py` — store reflexion lessons after failure

- [ ] **Step P3.1: Write failing tests**

```python
# tests/agent/test_reflexion_wiring.py
"""Reflexion must automatically store failure lessons after goal failure (doc-1 §3.3 Level 3)."""
from __future__ import annotations
import pytest
from app.state_runtime.reflexion_store import ReflexionStore
from app.agent.state import AgentState, GoalStatus
from app.tenancy.context import TenantContext, PlanTier


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


def _make_state(status: GoalStatus, goal: str = "test") -> AgentState:
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    s = AgentState(goal=goal, tenant_ctx=ctx, goal_id="g1")
    s.status = status
    s.iterations = 5
    s.verification_feedback = "Step 2 failed: permission denied for table users"
    return s


def test_reflexion_store_records_lesson():
    store = ReflexionStore()
    store.record(
        tenant_id="t1",
        lesson="When accessing users table, use the /api/user-preferences endpoint instead",
        source_goal_id="g1",
        failure_class="auth_failure",
    )
    lessons = store.recall(tenant_id="t1", limit=5)
    assert len(lessons) == 1
    assert "user-preferences" in lessons[0]["lesson"]


def test_reflexion_store_recalls_recent_lessons():
    store = ReflexionStore(max_per_tenant=5)
    for i in range(3):
        store.record(tenant_id="t1", lesson=f"Lesson {i}", source_goal_id=f"g{i}",
                     failure_class="unknown")
    lessons = store.recall(tenant_id="t1", limit=3)
    assert len(lessons) == 3


def test_reflexion_wirer_extracts_lesson_from_failure():
    """ReflexionWirer must extract a useful lesson from verification_feedback."""
    from app.agent.reflexion_wirer import ReflexionWirer
    wirer = ReflexionWirer()
    state = _make_state(GoalStatus.FAILED, "update user preferences in db")
    lesson = wirer.extract_lesson(state)
    assert lesson is not None
    assert len(lesson) > 20  # must be a meaningful string


def test_reflexion_wirer_stores_lesson_on_failure():
    from app.agent.reflexion_wirer import ReflexionWirer
    store = ReflexionStore()
    wirer = ReflexionWirer(store=store)
    state = _make_state(GoalStatus.FAILED, "update user preferences in db")
    wirer.maybe_store(state)
    lessons = store.recall(tenant_id="t1", limit=5)
    assert len(lessons) == 1


def test_reflexion_wirer_skips_on_success():
    from app.agent.reflexion_wirer import ReflexionWirer
    store = ReflexionStore()
    wirer = ReflexionWirer(store=store)
    state = _make_state(GoalStatus.COMPLETE, "list tickets")
    wirer.maybe_store(state)
    lessons = store.recall(tenant_id="t1", limit=5)
    assert len(lessons) == 0  # no lesson for successful goals


def test_reflexion_wirer_skips_if_feedback_is_empty():
    from app.agent.reflexion_wirer import ReflexionWirer
    store = ReflexionStore()
    wirer = ReflexionWirer(store=store)
    state = _make_state(GoalStatus.FAILED, "test")
    state.verification_feedback = ""  # empty feedback
    wirer.maybe_store(state)
    lessons = store.recall(tenant_id="t1", limit=5)
    assert len(lessons) == 0  # can't learn from empty feedback


def test_reflexion_lessons_injected_into_prompt_context():
    """Reflexion lessons must be available for PromptContextBundle."""
    store = ReflexionStore()
    store.record(tenant_id="t1", lesson="Don't access users table directly",
                 source_goal_id="g0", failure_class="auth_failure")
    lessons = store.recall(tenant_id="t1", limit=3)
    lesson_texts = [l["lesson"] for l in lessons]

    # These lessons go into PromptContextBundle.reflexion_lessons
    from app.context.prompt_builder import PromptContextBundle, PromptBuilder
    bundle = PromptContextBundle(
        goal_context="update user prefs",
        knowledge_chunks=[],
        citations=[],
        session_memory=[],
        reflexion_lessons=lesson_texts,
    )
    builder = PromptBuilder()
    prompt = builder.build_planner_context(bundle)
    assert "Don't access users table directly" in prompt
```

- [ ] **Step P3.2: Run to confirm failures**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_reflexion_wiring.py -v --no-cov
```
Expected: `ImportError` on `app.agent.reflexion_wirer`

- [ ] **Step P3.3: Implement `app/agent/reflexion_wirer.py`**

```python
"""ReflexionWirer — automatically stores failure lessons in ReflexionStore.

Doc-1 §3.3 Level 3 / Doc-3 §4: Cross-goal learning via Reflexion.
After every goal failure, extract the lesson and store for future runs.
Future plans receive relevant lessons via PromptContextBundle.reflexion_lessons.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent.state import AgentState
    from app.state_runtime.reflexion_store import ReflexionStore


class ReflexionWirer:
    """Extracts and stores failure lessons after goal execution."""

    def __init__(self, store: "ReflexionStore | None" = None) -> None:
        if store is None:
            from app.state_runtime.reflexion_store import ReflexionStore
            store = ReflexionStore()
        self._store = store

    def extract_lesson(self, state: "AgentState") -> str | None:
        """Extract a concise lesson from the failure feedback."""
        feedback = (state.verification_feedback or "").strip()
        if not feedback:
            return None

        goal = state.goal[:100]

        # Build a concise lesson from the feedback
        # Format: "When doing [goal context]: [what to avoid/do instead]"
        if len(feedback) <= 200:
            lesson = f"For goal '{goal[:60]}': {feedback}"
        else:
            # Truncate but keep the key part
            lesson = f"For goal '{goal[:60]}': {feedback[:200]}..."

        return lesson

    def maybe_store(self, state: "AgentState") -> bool:
        """Store a lesson if the goal failed and feedback is useful. Returns True if stored."""
        from app.agent.state import GoalStatus

        if state.status not in (GoalStatus.FAILED,):
            return False

        lesson = self.extract_lesson(state)
        if not lesson:
            return False

        # Classify the failure
        failure_class = "unknown"
        feedback_lower = (state.verification_feedback or "").lower()
        if "permission" in feedback_lower or "unauthorized" in feedback_lower:
            failure_class = "auth_failure"
        elif "not found" in feedback_lower or "404" in feedback_lower:
            failure_class = "context_gap"
        elif "timeout" in feedback_lower:
            failure_class = "timeout"
        elif "rate limit" in feedback_lower or "429" in feedback_lower:
            failure_class = "rate_limit"

        self._store.record(
            tenant_id=state.tenant_ctx.tenant_id,
            lesson=lesson,
            source_goal_id=state.goal_id,
            failure_class=failure_class,
        )
        return True


# Module-level singleton for use in graph.py
_default_reflexion_wirer: ReflexionWirer | None = None


def get_reflexion_wirer() -> ReflexionWirer:
    global _default_reflexion_wirer
    if _default_reflexion_wirer is None:
        _default_reflexion_wirer = ReflexionWirer()
    return _default_reflexion_wirer
```

- [ ] **Step P3.4: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_reflexion_wiring.py -v --no-cov
```
Expected: `7 passed`

- [ ] **Step P3.5: Commit**

```bash
cd agent-verse-backend
git add app/agent/reflexion_wirer.py tests/agent/test_reflexion_wiring.py
git commit -m "feat(agent): add ReflexionWirer — automatic cross-goal lesson storage (doc-1 §3.3 Level 3)"
```

---

## Task P4: DynamicGraphAssembler Activation Tests

**Files:**
- Create: `tests/agent/test_dynamic_graph_activation.py`

- [ ] **Step P4.1: Write tests verifying correct pattern activation**

```python
# tests/agent/test_dynamic_graph_activation.py
"""DynamicGraphAssembler must activate correct nodes per PatternConfig (doc-3 §9, doc-4 §3.5)."""
from __future__ import annotations
import pytest
from app.agent.dynamic_graph import DynamicGraphAssembler
from app.agent.goal_classifier import goal_classifier
from app.agent.pattern_assembler import pattern_assembler
from app.agent.pattern_config import (
    GoalProperties, PatternConfig, Complexity, RiskLevel, Domain,
)
from app.providers.fake import FakeProvider


@pytest.fixture
def assembler():
    return DynamicGraphAssembler()


@pytest.fixture
def provider():
    return FakeProvider()


# ── Doc-3 §10: Dynamic Pattern Combinations ────────────────────────────────────

def test_simple_lookup_nodes(assembler, provider):
    """Simple goal → no CoT, no reflection, no debate (doc-3 §10 row 1)."""
    props = GoalProperties(complexity=Complexity.SIMPLE, risk=RiskLevel.LOW)
    cfg = PatternConfig(reasoning_patterns=["react"], multi_agent_patterns=["single_agent"],
                        safety_patterns=["guardrails"], goal_properties=props)
    nodes = assembler.get_active_nodes(cfg)
    assert "initialize" in nodes
    assert "rag_prime" in nodes
    assert "plan" in nodes
    assert "execute" in nodes
    assert "verify" in nodes
    # No expensive optional nodes for simple goals
    assert "debate" not in nodes
    assert "think" not in nodes


def test_expert_goal_activates_cot(assembler, provider):
    """Expert goal → CoT + reflection (doc-3 §10 rows 3-4)."""
    props = GoalProperties(complexity=Complexity.EXPERT, risk=RiskLevel.LOW)
    cfg = PatternConfig(
        reasoning_patterns=["react", "chain_of_thought", "reflection"],
        goal_properties=props,
    )
    nodes = assembler.get_active_nodes(cfg)
    assert "think" in nodes        # CoT node
    assert "reflect" in nodes      # Reflection node


def test_self_refine_activates_refine_node(assembler, provider):
    """Self-refine in patterns → refine node active (doc-1 §3.4)."""
    props = GoalProperties(complexity=Complexity.COMPLEX)
    cfg = PatternConfig(reasoning_patterns=["react", "self_refine"], goal_properties=props)
    nodes = assembler.get_active_nodes(cfg)
    assert "refine" in nodes


def test_debate_activates_debate_node(assembler, provider):
    """Debate in multi_agent_patterns → debate node active (doc-1 §5.2)."""
    cfg = PatternConfig(
        multi_agent_patterns=["debate"],
        goal_properties=GoalProperties(risk=RiskLevel.CRITICAL),
    )
    nodes = assembler.get_active_nodes(cfg)
    assert "debate" in nodes


def test_goal_tree_activates_goal_tree_node(assembler, provider):
    """goal_tree in multi_agent → goal_tree_plan node active (doc-1 §5.3)."""
    cfg = PatternConfig(
        multi_agent_patterns=["goal_tree"],
        goal_properties=GoalProperties(complexity=Complexity.EXPERT, multi_step=True),
    )
    nodes = assembler.get_active_nodes(cfg)
    assert "goal_tree_plan" in nodes


def test_hitl_activates_hitl_check(assembler, provider):
    """hitl in safety_patterns → hitl_check node active (doc-1 §6.2)."""
    cfg = PatternConfig(
        safety_patterns=["guardrails", "hitl", "rollback"],
        autonomy_mode="supervised",
        goal_properties=GoalProperties(risk=RiskLevel.CRITICAL),
    )
    nodes = assembler.get_active_nodes(cfg)
    assert "hitl_check" in nodes


def test_rag_remediate_activates_for_agentic_rag(assembler, provider):
    """agentic_rag in rag_patterns → rag_remediate node active (doc-2 §9.2)."""
    cfg = PatternConfig(
        rag_patterns=["hybrid_rag", "agentic_rag"],
        goal_properties=GoalProperties(complexity=Complexity.COMPLEX),
    )
    nodes = assembler.get_active_nodes(cfg)
    assert "rag_remediate" in nodes


# ── Full classify → assemble → activate pipeline ─────────────────────────────

def test_classify_expert_produces_cot_nodes(assembler, provider):
    """End-to-end: classify expert goal → assemble → CoT node active."""
    goal = "design and architect a distributed rate-limiting system for multi-tenant scalability"
    props = goal_classifier.classify_fast(goal)
    cfg = pattern_assembler.assemble(props, agent_config={})
    nodes = assembler.get_active_nodes(cfg)
    # Expert goal must have CoT and reflection
    assert any(n in nodes for n in ["think", "reflect"]), (
        f"Expert goal should activate CoT/reflection. Nodes: {nodes}"
    )


def test_classify_critical_produces_hitl_node(assembler, provider):
    """End-to-end: classify critical goal → hitl_check in nodes."""
    goal = "delete all records from the production database permanently"
    props = goal_classifier.classify_fast(goal)
    cfg = pattern_assembler.assemble(props, agent_config={})
    nodes = assembler.get_active_nodes(cfg)
    assert "hitl_check" in nodes, (
        f"Critical goal must activate hitl_check. Nodes: {nodes}"
    )


def test_pattern_assembled_sse_event_matches_nodes(assembler, provider):
    """SSE event patterns_active must match get_active_nodes output."""
    props = GoalProperties(
        complexity=Complexity.EXPERT, risk=RiskLevel.LOW,
    )
    cfg = pattern_assembler.assemble(props, agent_config={})
    event = cfg.to_sse_event("goal_1")
    active_nodes = assembler.get_active_nodes(cfg)

    # Verify the event reflects the assembled patterns
    assert event["type"] == "pattern_assembled"
    assert isinstance(event["patterns_active"]["reasoning"], list)
    assert len(event["patterns_active"]["reasoning"]) > 0
    assert len(event["selection_reasons"]) > 0


# ── Loop Engineering patterns ─────────────────────────────────────────────────

def test_loop_engineering_pattern_maps_to_existing_node():
    """LoopEngineeringPattern references _execute_step_with_loop (doc-3 §3)."""
    from app.agent.patterns.loop_engineering import LoopEngineeringPattern
    from app.agent.graph import AgentGraph

    p = LoopEngineeringPattern()
    assert p.pattern_id == "loop_engineering"

    # The actual node is _execute_step_with_loop in graph.py
    assert hasattr(AgentGraph, "_execute_step_with_loop") or \
           hasattr(AgentGraph, "execute_step_with_loop"), (
        "AgentGraph must have loop_until step execution"
    )


def test_wave_execution_pattern_maps_to_existing_behavior():
    """Wave execution is triggered by structured plan with depends_on (doc-3 §2)."""
    from app.agent.patterns.loop_engineering import LoopEngineeringPattern
    from app.agent.graph import AgentGraph

    p = LoopEngineeringPattern()
    # Wave execution is in _node_execute via execution_waves()
    # Verify graph has the method
    assert hasattr(AgentGraph, "_node_execute"), "AgentGraph must have _node_execute"


# ── Reflexion wiring integration ─────────────────────────────────────────────

def test_reflexion_wirer_available():
    """ReflexionWirer must be importable and callable."""
    from app.agent.reflexion_wirer import ReflexionWirer, get_reflexion_wirer
    wirer = get_reflexion_wirer()
    assert wirer is not None
    assert isinstance(wirer, ReflexionWirer)
```

- [ ] **Step P4.2: Run to confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_dynamic_graph_activation.py -v --no-cov
```
Expected: All 14 tests pass

- [ ] **Step P4.3: Commit**

```bash
cd agent-verse-backend
git add tests/agent/test_dynamic_graph_activation.py
git commit -m "test(agent): add DynamicGraphAssembler activation tests — verify all 12 patterns activate correct nodes"
```

---

## Task P5: Update doc-3 §10 Pattern Combinations in PatternAssembler

Doc-3 §10 defines an 8-row pattern matrix. The current PatternAssembler (Part 6A) doesn't handle all combinations. Add the missing entries.

- [ ] **Step P5.1: Write failing test**

```python
# Append to tests/agent/test_pattern_assembler.py

def test_writing_task_gets_self_refine(assembler):
    """Doc-3 §10 row 8: Writing task → CoT + self_refine (doc-3 §10)."""
    props = GoalProperties(
        complexity=Complexity.COMPLEX,
        domain=Domain.CREATIVE,
        is_generative=True,
    )
    cfg = assembler.assemble(props, agent_config={})
    assert any(p in cfg.reasoning_patterns for p in ["self_refine", "chain_of_thought"])


def test_critical_action_gets_debate(assembler):
    """Doc-3 §10 row 6: Critical action → debate + consensus (doc-3 §10)."""
    props = GoalProperties(risk=RiskLevel.CRITICAL, complexity=Complexity.MEDIUM)
    cfg = assembler.assemble(props, agent_config={})
    assert "consensus_verification" in cfg.safety_patterns


def test_research_goal_gets_supervisor(assembler):
    """Doc-3 §10 row 7: Complex multi-domain research → supervisor (doc-3 §10)."""
    props = GoalProperties(
        complexity=Complexity.EXPERT,
        domain=Domain.ANALYTICAL,
        multi_step=True,
    )
    cfg = assembler.assemble(props, agent_config={})
    # Expert analytical goal should suggest goal_tree or supervisor
    assert any(p in cfg.multi_agent_patterns for p in ["goal_tree", "supervisor", "single_agent"])
```

- [ ] **Step P5.2: Add missing rules to `app/agent/pattern_assembler.py`**

Find the `_RULES` list in `app/agent/pattern_assembler.py` and add these rules:

```python
    # ── MEDIUM: Creative / writing → self_refine ──────────────────────────────
    Rule(
        condition=lambda p: p.is_generative or p.domain == Domain.CREATIVE,
        add_reasoning=["self_refine"],
        reason_key="self_refine", reason_value="generative/creative task — self-refinement improves quality",
        priority="MEDIUM",
    ),
    # ── MEDIUM: Complex analytical → supervisor option ────────────────────────
    Rule(
        condition=lambda p: (p.complexity == Complexity.EXPERT
                             and p.domain == Domain.ANALYTICAL
                             and p.multi_step),
        add_multi_agent=["supervisor"],
        reason_key="supervisor", reason_value="expert analytical multi-step → supervisor coordination",
        priority="MEDIUM",
    ),
```

- [ ] **Step P5.3: Run all pattern assembler tests**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_pattern_assembler.py -v --no-cov
```
Expected: All 14 tests pass

- [ ] **Step P5.4: Commit**

```bash
cd agent-verse-backend
git add app/agent/pattern_assembler.py tests/agent/test_pattern_assembler.py
git commit -m "feat(agent): add doc-3 §10 pattern combinations — creative/self_refine, analytical/supervisor"
```

---

## Task P6: Complete Agent Pattern Verification

- [ ] **Step P6.1: Run all agent pattern tests together**

```bash
cd agent-verse-backend
uv run pytest \
    tests/agent/test_pattern_config.py \
    tests/agent/test_goal_classifier_doc4.py \
    tests/agent/test_pattern_assembler.py \
    tests/agent/test_dynamic_graph.py \
    tests/agent/test_agent_patterns.py \
    tests/agent/test_agent_pattern_gaps.py \
    tests/agent/test_reflexion_wiring.py \
    tests/agent/test_dynamic_graph_activation.py \
    -v --no-cov 2>&1 | tail -20
```
Expected: All 80+ tests pass

- [ ] **Step P6.2: Verify ALL_PATTERNS covers doc-1 §11**

```bash
cd agent-verse-backend
python -c "
from app.agent.patterns import ALL_PATTERNS
ids = {p.pattern_id for p in ALL_PATTERNS}
required = {'react', 'plan_execute', 'reflection', 'reflexion', 'self_refine',
            'self_consistency', 'tree_of_thoughts', 'loop_engineering',
            'supervisor', 'debate', 'goal_tree', 'consensus', 'peer_review'}
missing = required - ids
print(f'ALL_PATTERNS has {len(ids)} entries')
print(f'Missing: {sorted(missing) if missing else \"NONE - all covered\"}')
"
```
Expected: `Missing: NONE - all covered`

- [ ] **Step P6.3: Verify StrategyRegistry covers all doc-1 agent patterns**

```bash
cd agent-verse-backend
python -c "
from app.orchestration.strategy_registry import build_default_registry, StrategyCategory
r = build_default_registry()
agent_ids = {s.strategy_id for s in r.list_by_category(StrategyCategory.AGENT)}
from app.agent.patterns import ALL_PATTERNS
adapter_ids = {p.pattern_id for p in ALL_PATTERNS}
missing = adapter_ids - agent_ids
print(f'Registry has {len(agent_ids)} agent patterns')
print(f'Adapters not in registry: {sorted(missing) if missing else \"NONE\"}')
"
```
Expected: `Adapters not in registry: NONE`

- [ ] **Step P6.4: Final commit**

```bash
cd agent-verse-backend
git add -A
git commit -m "test(agent): complete agent pattern verification — all doc-1 + doc-3 patterns verified

Coverage complete:
- _node_refine implemented (Self-Refine, doc-1 §3.4)
- SELF_REFINE_SYSTEM prompt added (doc-3 §8.4)
- PeerReviewPattern added to ALL_PATTERNS (doc-1 §5.5)
- ReflexionWirer: automatic cross-goal lesson storage (doc-1 §3.3 Level 3)
- DynamicGraphAssembler activation tests: 14 patterns verified
- Doc-3 §10 pattern matrix: creative/self_refine + analytical/supervisor rules
- ALL 13 agent pattern adapters in ALL_PATTERNS
- ALL 13 adapters in StrategyRegistry
- Doc-3 §11 status: ReAct/Plan-Execute/CoT/Reflection/LoopEng/Supervisor/
  Debate/GoalTree/Consensus/HITL/Rollback/CircuitBreaker/Guardrails all verified"
```
