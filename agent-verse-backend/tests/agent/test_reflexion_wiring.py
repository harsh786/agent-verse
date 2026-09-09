"""Reflexion must automatically store failure lessons after goal failure (doc-1 §3.3 Level 3)."""
from __future__ import annotations

import pytest

from app.agent.state import AgentState, GoalStatus
from app.state_runtime.reflexion_store import ReflexionStore
from app.tenancy.context import PlanTier, TenantContext


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
    store.record(tenant_id="t1", lesson="Use API not DB directly", source_goal_id="g1",
                 failure_class="auth_failure")
    lessons = store.recall(tenant_id="t1", limit=5)
    assert len(lessons) == 1
    assert "API" in lessons[0]["lesson"]


def test_reflexion_store_recalls_recent():
    store = ReflexionStore(max_per_tenant=5)
    for i in range(3):
        store.record(tenant_id="t1", lesson=f"Lesson {i}", source_goal_id=f"g{i}",
                     failure_class="unknown")
    lessons = store.recall(tenant_id="t1", limit=3)
    assert len(lessons) == 3


def test_reflexion_wirer_extracts_lesson():
    from app.agent.reflexion_wirer import ReflexionWirer
    wirer = ReflexionWirer()
    state = _make_state(GoalStatus.FAILED, "update user db")
    lesson = wirer.extract_lesson(state)
    assert lesson is not None
    assert len(lesson) > 20


def test_reflexion_wirer_stores_lesson_on_failure():
    from app.agent.reflexion_wirer import ReflexionWirer
    store = ReflexionStore()
    wirer = ReflexionWirer(store=store)
    state = _make_state(GoalStatus.FAILED, "update user db")
    stored = wirer.maybe_store(state)
    assert stored is True
    lessons = store.recall(tenant_id="t1", limit=5)
    assert len(lessons) == 1


def test_reflexion_wirer_skips_on_success():
    from app.agent.reflexion_wirer import ReflexionWirer
    store = ReflexionStore()
    wirer = ReflexionWirer(store=store)
    state = _make_state(GoalStatus.COMPLETE, "list tickets")
    wirer.maybe_store(state)
    lessons = store.recall(tenant_id="t1", limit=5)
    assert len(lessons) == 0


def test_reflexion_wirer_skips_empty_feedback():
    from app.agent.reflexion_wirer import ReflexionWirer
    store = ReflexionStore()
    wirer = ReflexionWirer(store=store)
    state = _make_state(GoalStatus.FAILED, "test")
    state.verification_feedback = ""
    wirer.maybe_store(state)
    lessons = store.recall(tenant_id="t1", limit=5)
    assert len(lessons) == 0


def test_reflexion_lessons_injected_into_prompt_context():
    store = ReflexionStore()
    store.record(tenant_id="t1", lesson="Don't access users table directly",
                 source_goal_id="g0", failure_class="auth_failure")
    lessons = store.recall(tenant_id="t1", limit=3)
    lesson_texts = [l["lesson"] for l in lessons]
    from app.context.prompt_builder import PromptBuilder, PromptContextBundle
    bundle = PromptContextBundle(
        goal_context="update user prefs", knowledge_chunks=[], citations=[],
        session_memory=[], reflexion_lessons=lesson_texts,
    )
    builder = PromptBuilder()
    prompt = builder.build_planner_context(bundle)
    assert "Don't access users table directly" in prompt


def test_get_reflexion_wirer_singleton():
    from app.agent.reflexion_wirer import ReflexionWirer, get_reflexion_wirer
    wirer = get_reflexion_wirer()
    assert wirer is not None
    assert isinstance(wirer, ReflexionWirer)
