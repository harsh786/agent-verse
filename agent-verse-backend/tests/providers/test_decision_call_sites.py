"""Regression: LLM calls that bypassed budget, ledger, timeout and circuit breaker.

RAPTOR / agentic-chunking indexing, knowledge-graph extraction, workflow LLM
steps, the NL scheduler, the goal classifier, the org services, memory
consolidation, tool self-healing, RAFT inference and the RAG strategy LLM all
called ``provider.complete()`` directly. Each now goes through
:func:`app.providers.guarded_completion.complete_decision` with the tenant it
serves (the RAG strategy LLM is already metered, so breaker + timeout only).

The provider handed to each site raises if it is called directly, and
``complete_decision`` is replaced by a recorder, so each test proves both that
the site routes through the guard and which tenant it charges.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from app.providers import guarded_completion as gc
from app.providers.base import CompletionResponse
from app.providers.guarded_completion import DecisionBudgetExceededError


class _DirectCallForbidden:
    """A provider whose ``complete`` must never be called directly."""

    _default_model = "m"

    async def complete(self, request: Any) -> Any:
        raise AssertionError("provider.complete() called directly, bypassing complete_decision")


@dataclass
class _Call:
    role: str
    tenant: str | None
    charge: bool
    goal_id: str | None
    timeout_seconds: float | None


@dataclass
class _Recorder:
    reply: str = "{}"
    refuse: bool = False
    calls: list[_Call] = field(default_factory=list)

    async def __call__(
        self,
        provider: Any,
        request: Any,
        *,
        role: str,
        tenant_ctx: Any = None,
        tenant_id: str | None = None,
        goal_id: str | None = None,
        timeout_seconds: float | None = None,
        charge: bool = True,
    ) -> Any:
        tenant = tenant_id or getattr(tenant_ctx, "tenant_id", None)
        self.calls.append(_Call(role, tenant, charge, goal_id, timeout_seconds))
        if self.refuse:
            raise DecisionBudgetExceededError("tenant daily LLM budget exhausted")
        return CompletionResponse(content=self.reply, model="m", input_tokens=1, output_tokens=1)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(gc, "complete_decision", rec)
    return rec


def _tenant_ctx(tenant_id: str = "t1") -> Any:
    from app.tenancy.context import PlanTier, TenantContext

    return TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")


# ── RAPTOR / agentic chunking indexing ────────────────────────────────────────


class _Store:
    def __init__(self) -> None:
        self.persisted: list[Any] = []

    async def persist_index_records(self, records: Any, **kwargs: Any) -> None:
        self.persisted.append(records)


class _Embedder:
    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        return EmbedResponse(embeddings=[[0.1, 0.2] for _ in request.texts], model="e")


def _indexing_pipeline() -> tuple[Any, _Store]:
    from app.rag.contracts import RAGStrategy
    from app.rag.indexing import IndexingDependency, RAGIndexingConfig, RAGIndexingPipeline

    store = _Store()
    pipeline = RAGIndexingPipeline(
        store=store,
        embedder=_Embedder(),
        dependencies={
            RAGStrategy.RAPTOR: IndexingDependency(provider=_DirectCallForbidden(), model="m")
        },
        config=RAGIndexingConfig(
            strategies=frozenset({RAGStrategy.RAPTOR}), raptor_cluster_size=2
        ),
    )
    return pipeline, store


async def test_raptor_indexing_charges_the_tenant(recorder: _Recorder) -> None:
    recorder.reply = '{"items": ["a summary of both chunks"]}'
    pipeline, store = _indexing_pipeline()
    records = await pipeline.index_document(
        collection_id="c",
        document_id="d",
        chunks=["first chunk of text", "second chunk of text"],
        tenant_ctx=_tenant_ctx("t-index"),
    )
    assert records and store.persisted
    [call] = recorder.calls
    assert call.role == "rag_indexing_raptor" and call.tenant == "t-index" and call.charge


async def test_raptor_indexing_budget_refusal_fails_the_ingest(recorder: _Recorder) -> None:
    recorder.refuse = True
    pipeline, store = _indexing_pipeline()
    with pytest.raises(DecisionBudgetExceededError):
        await pipeline.index_document(
            collection_id="c",
            document_id="d",
            chunks=["first chunk of text", "second chunk of text"],
            tenant_ctx=_tenant_ctx(),
        )
    assert store.persisted == []  # nothing half-indexed


# ── Knowledge graph extraction ────────────────────────────────────────────────


async def test_kg_entity_extraction_charges_the_tenant(recorder: _Recorder) -> None:
    from app.knowledge_graph.extractor import EntityExtractor

    recorder.reply = '[{"label": "Alice Smith", "type": "person", "confidence": 0.9}]'
    extractor = EntityExtractor()
    extractor.set_provider(_DirectCallForbidden())
    nodes = await extractor.extract_entities_llm("Alice Smith met Bob.", "t-kg")
    assert [n.label for n in nodes] == ["Alice Smith"]
    [call] = recorder.calls
    assert call.role == "kg_entity_extraction" and call.tenant == "t-kg"


async def test_kg_entity_extraction_budget_refusal_degrades_to_patterns(
    recorder: _Recorder,
) -> None:
    from app.knowledge_graph.extractor import EntityExtractor

    recorder.refuse = True
    extractor = EntityExtractor()
    extractor.set_provider(_DirectCallForbidden())
    nodes = await extractor.extract_entities_llm("Alice Smith met Bob Jones.", "t-kg")
    assert {n.metadata["extraction_method"] for n in nodes} == {"deterministic"}


async def test_kg_relationship_extraction_charges_the_tenant(recorder: _Recorder) -> None:
    from app.knowledge_graph.extractor import EntityExtractor

    extractor = EntityExtractor()
    entities = extractor.extract_entities_deterministic("Alice Smith met Bob Jones.", "t-kg")
    extractor.set_provider(_DirectCallForbidden())
    recorder.reply = "[]"
    await extractor.extract_relationships_llm("Alice Smith met Bob Jones.", entities, "t-kg")
    [call] = recorder.calls
    assert call.role == "kg_relationship_extraction" and call.tenant == "t-kg"


# ── Workflow LLM step ─────────────────────────────────────────────────────────


async def test_workflow_llm_step_charges_the_run_tenant(recorder: _Recorder) -> None:
    from app.workflow.context import ContextResolver
    from app.workflow.dsl import StepDefinition
    from app.workflow.steps.llm_step import LLMStepNode

    recorder.reply = '{"result": "done"}'
    node = LLMStepNode(
        StepDefinition(id="l1", type="llm", prompt="summarise"),
        ContextResolver(),
        llm_provider=_DirectCallForbidden(),
    )
    state: dict[str, Any] = {
        "run_id": "run-1",
        "tenant_id": "t-wf",
        "inputs": {},
        "step_outputs": {},
        "vars": {},
        "is_test_run": False,
        "mock_overrides": {},
    }
    out = await node.execute(state)  # type: ignore[arg-type]
    assert out["step_outputs"]["l1"] == {"result": "done"}
    [call] = recorder.calls
    assert call.role == "workflow_llm_step" and call.tenant == "t-wf"
    assert call.goal_id == "workflow:run-1"


async def test_workflow_llm_step_budget_refusal_fails_the_step(recorder: _Recorder) -> None:
    from app.workflow.context import ContextResolver
    from app.workflow.dsl import StepDefinition
    from app.workflow.steps.llm_step import LLMStepNode

    recorder.refuse = True
    node = LLMStepNode(
        StepDefinition(id="l1", type="llm", prompt="summarise"),
        ContextResolver(),
        llm_provider=_DirectCallForbidden(),
    )
    state: dict[str, Any] = {"run_id": "r", "tenant_id": "t", "step_outputs": {}}
    with pytest.raises(DecisionBudgetExceededError):
        await node.execute(state)  # type: ignore[arg-type]


# ── NL scheduler ──────────────────────────────────────────────────────────────


async def test_nl_scheduler_charges_the_tenant(recorder: _Recorder) -> None:
    from app.triggers.nl_scheduler import NLScheduler

    recorder.reply = '{"trigger_type": "cron", "cron_expression": "0 9 * * *"}'
    specs = await NLScheduler(_DirectCallForbidden()).parse(  # type: ignore[arg-type]
        "every day at 9am", tenant_ctx=_tenant_ctx("t-sched")
    )
    assert specs
    [call] = recorder.calls
    assert call.role == "nl_scheduler" and call.tenant == "t-sched"


async def test_nl_scheduler_budget_refusal_falls_back_to_keywords(recorder: _Recorder) -> None:
    from app.triggers.nl_scheduler import NLScheduler

    recorder.refuse = True
    specs = await NLScheduler(_DirectCallForbidden()).parse(  # type: ignore[arg-type]
        "every day at 9am", tenant_id="t"
    )
    assert specs  # keyword routing / ONCE fallback, as before


# ── Goal classifier (orchestration + agent) ───────────────────────────────────


async def test_orchestration_goal_classifier_charges_the_tenant(recorder: _Recorder) -> None:
    from app.orchestration.goal_classifier import Complexity, GoalClassifier

    classifier = GoalClassifier()
    goal = "Analyse the quarterly sales pipeline and propose three improvements"
    base = classifier.classify_fast(goal)
    base = replace(base, complexity=Complexity.MEDIUM, classifier_confidence=0.5)
    recorder.reply = '{"complexity": "complex", "confidence": 0.9}'
    out = await classifier.classify_with_llm(
        goal, _DirectCallForbidden(), base, tenant_id="t-cls", goal_id="g1"
    )
    assert out.complexity == Complexity.COMPLEX
    [call] = recorder.calls
    assert call.role == "goal_classifier" and call.tenant == "t-cls" and call.goal_id == "g1"


# ── Org services ──────────────────────────────────────────────────────────────


async def test_org_department_composition_charges_the_org_tenant(recorder: _Recorder) -> None:
    from app.org.service import OrgService

    recorder.reply = (
        '[{"name": "Eng", "purpose": "build", "capability_domains": ["code"]},'
        ' {"name": "Ops", "purpose": "run", "capability_domains": ["ops"]}]'
    )
    svc = OrgService(session=None, tenant_id="t-org")  # type: ignore[arg-type]
    depts = await svc._compose_departments_llm("build a SaaS", "tech", _DirectCallForbidden())
    assert len(depts) == 2
    [call] = recorder.calls
    assert call.role == "org_compose_departments" and call.tenant == "t-org"


async def test_org_mission_decomposition_charges_the_org_tenant(recorder: _Recorder) -> None:
    from app.org.service import OrgService

    recorder.reply = '[{"title": "a", "objective": "do a"}, {"title": "b", "objective": "do b"}]'
    svc = OrgService(session=None, tenant_id="t-org")  # type: ignore[arg-type]
    await svc._decompose_mission_llm("ship it", _DirectCallForbidden(), 4)
    [call] = recorder.calls
    assert call.role == "org_decompose_mission" and call.tenant == "t-org"


async def test_org_quality_gate_judge_charges_the_context_tenant(recorder: _Recorder) -> None:
    from app.org.quality_gates import QualityGateSystem

    recorder.reply = '{"score": 0.8, "reason": "fine"}'
    qg = QualityGateSystem(llm_provider=_DirectCallForbidden())
    verdict = await qg._llm_score(
        "output", {"task": "t", "tenant_id": "t-qg"}, system="judge", gate_label="g3"
    )
    assert verdict == (0.8, "fine")
    [call] = recorder.calls
    assert call.role == "org_quality_gate" and call.tenant == "t-qg"


async def test_org_goal_analyzer_and_team_formation_charge_the_tenant(
    recorder: _Recorder,
) -> None:
    from app.org.meta_orchestrator import GoalAnalyzer
    from app.org.team_formation import TeamFormationEngine

    recorder.reply = (
        '{"complexity": "low", "domain_count": 1, "has_dependencies": false,'
        ' "task_type": "research", "risk_level": "low", "estimated_phases": 1,'
        ' "primary_domains": ["research"], "breadth": "narrow"}'
    )
    await GoalAnalyzer(_DirectCallForbidden()).analyze("research competitors", tenant_id="t-a")
    recorder.reply = "[]"
    engine = TeamFormationEngine(_DirectCallForbidden())
    await engine._extract_capabilities("research competitors", tenant_id="t-b")
    assert [(c.role, c.tenant) for c in recorder.calls] == [
        ("org_goal_analysis", "t-a"),
        ("org_team_formation", "t-b"),
    ]


async def test_org_collaboration_chatter_charges_the_tenant(recorder: _Recorder) -> None:
    from app.org.brain_collaboration import LLMProviderCollaborationGateway

    recorder.reply = "Shipping today."
    gateway = LLMProviderCollaborationGateway(_DirectCallForbidden(), gateway=None)
    text, *_ = await gateway.complete_short("status?", max_tokens=40, tenant_id="t-collab")
    assert text == "Shipping today."
    [call] = recorder.calls
    assert call.role == "org_collaboration" and call.tenant == "t-collab"


async def test_org_strategic_brief_charges_the_tenant(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.org import advanced_services

    recorder.reply = (
        '{"health_summary": "ok", "accomplishments": [], "risks": [],'
        ' "opportunities": [], "recommendations": []}'
    )
    monkeypatch.setattr(advanced_services, "_brief_provider", lambda: _DirectCallForbidden())
    advisor = advanced_services.StrategicAdvisor()
    brief = await advisor.generate_weekly_brief(
        org_id="o1", org_name="Org", health={"health": "healthy"}, tenant_id="t-brief"
    )
    assert brief.generation_method == "llm"
    [call] = recorder.calls
    assert call.role == "org_strategic_brief" and call.tenant == "t-brief"


# ── Memory consolidation ──────────────────────────────────────────────────────


async def test_memory_consolidation_charges_the_tenant(recorder: _Recorder) -> None:
    from app.memory.consolidation import MemoryConsolidator

    recorder.reply = "deploys happen on fridays"
    memories = [{"content": "deploy on friday afternoon"} for _ in range(3)]
    result = await MemoryConsolidator(cluster_threshold=3).consolidate(
        memories, _DirectCallForbidden(), tenant_id="t-mem"  # type: ignore[arg-type]
    )
    assert result.clusters_merged == 1
    [call] = recorder.calls
    assert call.role == "memory_consolidation" and call.tenant == "t-mem"


# ── Tool self-healing ─────────────────────────────────────────────────────────


async def test_tool_self_heal_charges_the_tenant(recorder: _Recorder) -> None:
    from types import SimpleNamespace

    from app.mcp.tool_intelligence import SelfHealingToolCaller

    recorder.reply = '{"jql": "project = X"}'
    healer = SelfHealingToolCaller(_DirectCallForbidden())
    fixed = await healer.heal(
        tool_name="jira.search",
        tool_schema={"type": "object", "properties": {"jql": {"type": "string"}}},
        original_arguments={"query": "project = X"},
        failed_result=SimpleNamespace(error="KeyError: 'jql'"),
        tenant_ctx=_tenant_ctx("t-heal"),
    )
    assert fixed == {"jql": "project = X"}
    [call] = recorder.calls
    assert call.role == "tool_self_heal" and call.tenant == "t-heal"


# ── RAFT inference ────────────────────────────────────────────────────────────


async def test_raft_inference_charges_the_tenant(recorder: _Recorder) -> None:
    from app.rag.raft_inference import LLMFineTunedInferenceProvider

    recorder.reply = "the answer"
    provider = LLMFineTunedInferenceProvider(provider_id="openai", llm=_DirectCallForbidden())
    answer = await provider.infer(
        query="q", evidence=("e",), fine_tuned_model_id="ft:x", tenant_id="t-raft"
    )
    assert answer == "the answer"
    [call] = recorder.calls
    assert call.role == "rag_raft_inference" and call.tenant == "t-raft" and call.charge


# ── RAG strategy LLM (already budgeted) ───────────────────────────────────────


async def test_rag_budgeted_provider_adds_breaker_and_timeout_without_double_charge(
    recorder: _Recorder,
) -> None:
    from app.providers.base import CompletionRequest, Message
    from app.rag.gateway import _BudgetedProvider

    class _Guard:
        def __init__(self) -> None:
            self.reserved: list[str] = []
            self.tokens: list[int] = []

        async def reserve(self, operation: str) -> int:
            self.reserved.append(operation)
            return 0

        def record_tokens(self, index: int, tokens: int) -> None:
            self.tokens.append(tokens)

    guard = _Guard()
    recorder.reply = "answer"
    budgeted = _BudgetedProvider(_DirectCallForbidden(), guard)  # type: ignore[arg-type]
    resp = await budgeted.complete(
        CompletionRequest(messages=[Message(role="user", content="q")], model="m")
    )
    assert resp.content == "answer" and guard.reserved == ["completion"]
    [call] = recorder.calls
    assert call.role == "rag_strategy" and call.charge is False
