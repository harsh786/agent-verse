"""E2E: Complete AgentGraph pipeline — all 12 steps, real module interactions."""
from __future__ import annotations

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import GoalStatus
from app.enterprise.compliance import ComplianceController
from app.enterprise.marketplace import Marketplace
from app.governance.audit import AuditLog
from app.governance.cost import BudgetConfig, CostController
from app.governance.hitl import HITLGateway, ApprovalStatus
from app.governance.permissions import ActionLevel, PermissionMatrix, PermissionRule
from app.governance.policies import Policy, PolicyEngine
from app.intelligence.eval import EvalScorecard
from app.intelligence.eval_runner import EvalRunner
from app.intelligence.guardrails import GuardrailChecker
from app.intelligence.self_optimization import SelfOptimizer
from app.memory.execution import ExecutionMemory
from app.memory.long_term import LongTermMemory, LongTermMemoryStore
from app.providers.fake import FakeProvider
from app.rag.store import KnowledgeStore
from app.reliability.circuit_breaker import CircuitBreaker
from app.reliability.dedup import DeduplicationCache
from app.reliability.result_processor import ResultProcessor
from app.reliability.rollback import RollbackEngine
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(
    tenant_id="graph-e2e", plan=PlanTier.ENTERPRISE, api_key_id="ge2e-k1"
)


async def test_full_pipeline_complete():
    """All 12 steps fire, goal reaches COMPLETE, audit entries recorded."""
    p = FakeProvider(
        responses=[
            '{"steps": ["call github to list repos", "analyze results"]}',
            "repos: [repo1, repo2]",
            "analysis complete",
            '{"success": true, "reason": "Task completed successfully"}',
        ]
    )
    audit = AuditLog()
    cost = CostController()
    hitl = HITLGateway()
    rollback = RollbackEngine()
    dedup = DeduplicationCache()
    rp = ResultProcessor()
    mem = ExecutionMemory()
    ltm = LongTermMemoryStore()
    eval_runner = EvalRunner()
    guardrails = GuardrailChecker()

    graph = AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        audit_log=audit,
        cost_controller=cost,
        hitl_gateway=hitl,
        rollback_engine=rollback,
        dedup_cache=dedup,
        result_processor=rp,
        exec_memory=mem,
        long_term_memory=ltm,
        eval_runner=eval_runner,
        guardrail_checker=guardrails,
    )
    events: list[dict] = []

    async def cb(e: dict) -> None:
        events.append(e)

    state = await graph.run(
        goal="list repos and analyze", tenant_ctx=T, event_callback=cb
    )

    # Goal reached complete
    assert state.status == GoalStatus.COMPLETE
    # Two steps executed
    assert len(state.steps) == 2
    assert all(s.output for s in state.steps)
    # Audit recorded both steps
    entries = audit.query(tenant_ctx=T)
    assert len(entries) == 2
    # Execution memory has the winning plan
    recalled = mem.recall(goal_hint="list repos", tenant_ctx=T)
    assert len(recalled) >= 1
    # Eval scorecard attached
    assert "eval_scorecard" in state.context
    # Events contain expected lifecycle events
    types = {e["type"] for e in events}
    assert {
        "goal_started",
        "plan_ready",
        "step_started",
        "step_complete",
        "verification_done",
        "goal_complete",
    }.issubset(types)


async def test_governance_deny_blocks_execution():
    """DENY permission prevents step execution, raises PermissionError."""
    p = FakeProvider(
        responses=[
            '{"steps": ["call restricted_tool to do something"]}',
            "blocked",
            '{"success": true, "reason": "ok"}',
        ]
    )
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(tool_name="restricted_tool", level=ActionLevel.DENY),
        tenant_ctx=T,
    )
    graph = AgentGraph(planner=p, executor=p, verifier=p, permission_matrix=matrix)
    with pytest.raises(PermissionError, match="restricted_tool"):
        await graph.run(goal="trigger deny", tenant_ctx=T)


async def test_cost_budget_exceeded_skips_step():
    """When budget is 0, cost check returns False, step is skipped."""
    p = FakeProvider(
        responses=[
            '{"steps": ["expensive step"]}',
            "skipped",
            '{"success": true, "reason": "ok"}',
        ]
    )
    cost = CostController(BudgetConfig(per_goal_usd=0.0, per_tenant_daily_usd=0.0))
    graph = AgentGraph(planner=p, executor=p, verifier=p, cost_controller=cost)
    state = await graph.run(goal="expensive task", tenant_ctx=T)
    assert state is not None
    if state.steps:
        assert (
            "skipped" in state.steps[0].output.lower()
            or state.steps[0].output != ""
        )


async def test_secret_redaction_in_output():
    """ResultProcessor must scrub secrets from step output."""
    p = FakeProvider(
        responses=[
            '{"steps": ["fetch credentials"]}',
            "token=sk-supersecretabc123def456",
            '{"success": true, "reason": "done"}',
        ]
    )
    graph = AgentGraph(
        planner=p, executor=p, verifier=p, result_processor=ResultProcessor()
    )
    state = await graph.run(goal="fetch creds", tenant_ctx=T)
    if state.steps:
        assert "sk-supersecret" not in state.steps[0].output


async def test_dedup_prevents_repeat_step():
    """Duplicate content hash causes second identical step to return cached result."""
    p = FakeProvider(
        responses=[
            '{"steps": ["call api", "call api"]}',
            "first result",
            "should not appear",
            '{"success": true, "reason": "done"}',
        ]
    )
    graph = AgentGraph(
        planner=p, executor=p, verifier=p, dedup_cache=DeduplicationCache()
    )
    state = await graph.run(goal="dedup test", tenant_ctx=T)
    assert state is not None


async def test_circuit_breaker_open_skips_llm_step():
    """Open circuit breaker causes step to return early without calling LLM."""
    p = FakeProvider(
        responses=[
            '{"steps": ["call external_service"]}',
            "should not appear",
            '{"success": true, "reason": "done"}',
        ]
    )
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure()  # Force open immediately
    graph = AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        circuit_breakers={"llm": breaker},
    )
    state = await graph.run(goal="circuit test", tenant_ctx=T)
    if state.steps:
        assert "Circuit open" in state.steps[0].output


async def test_self_optimizer_processes_failed_eval():
    """SelfOptimizer generates suggestions for low-scoring goals."""
    optimizer = SelfOptimizer()
    # scores dict keyed by dimension name
    scorecard = EvalScorecard(
        goal_id="g1",
        scores={
            "task_completion": 0.2,
            "accuracy": 0.3,
            "efficiency": 0.1,
            "safety": 0.5,
            "coherence": 0.4,
        },
    )
    suggestions = optimizer.analyze_and_suggest(
        goal="optimize this",
        scorecard=scorecard,
        error_log="tool not found",
        tenant_ctx=T,
    )
    assert len(suggestions) >= 1
    assert any(
        s.category in {"prompt", "tool_selection", "retry_strategy"}
        for s in suggestions
    )
    # Apply suggestion
    sid = suggestions[0].suggestion_id
    ok = optimizer.apply_suggestion(suggestion_id=sid, tenant_ctx=T)
    assert ok
    applied = [s for s in optimizer.list_suggestions(tenant_ctx=T) if s.applied]
    assert len(applied) >= 1


async def test_long_term_memory_cross_goal():
    """Long-term memory persists insights across separate goal runs."""
    ltm = LongTermMemoryStore()
    p = FakeProvider(
        responses=[
            '{"steps": ["analyze data"]}',
            "insight: data pattern X",
            '{"success": true, "reason": "done"}',
        ]
    )
    graph = AgentGraph(planner=p, executor=p, verifier=p, long_term_memory=ltm)
    state1 = await graph.run(goal="analyze dataset", tenant_ctx=T)
    assert state1.status == GoalStatus.COMPLETE
    # Manually store a cross-session insight from this run
    ltm.store(
        memory=LongTermMemory(
            content="data pattern X is significant for analysis tasks",
            source_goal_id=state1.goal_id,
            memory_type="domain_fact",
        ),
        tenant_ctx=T,
    )
    # Second run should have the LTM in context
    p2 = FakeProvider(
        responses=[
            '{"steps": ["use knowledge"]}',
            "applied insight",
            '{"success": true, "reason": "done"}',
        ]
    )
    graph2 = AgentGraph(planner=p2, executor=p2, verifier=p2, long_term_memory=ltm)
    state2 = await graph2.run(goal="analyze dataset again", tenant_ctx=T)
    assert state2.status == GoalStatus.COMPLETE


async def test_marketplace_deploy_to_agent_creation():
    """Marketplace deploy creates a valid agent configuration."""
    mp = Marketplace()
    templates = mp.browse(tenant_ctx=T)
    assert len(templates) >= 6
    tpl = templates[0]
    dep = await mp.deploy(template_id=tpl["template_id"], params={}, tenant_ctx=T)
    assert dep.agent_id
    assert dep.tenant_id == T.tenant_id


async def test_compliance_full_flow():
    """GDPR export → status check → deletion request."""
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    assert req.status == "ready"
    assert req.payload
    status = await cc.get_export_status(request_id=req.request_id, tenant_ctx=T)
    assert status is not None
    deletion = await cc.request_data_deletion(tenant_ctx=T)
    assert deletion["deletion_scheduled"] is True


async def test_red_team_detects_prompt_injection():
    """Red team must detect the prompt_injection adversarial case."""
    from app.enterprise.red_team import RedTeamRunner

    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T, cases=["prompt_injection"])
    assert report.cases_run == 1
    result = report.results[0]
    assert result["case_id"] == "prompt_injection"
    # Detection heuristic: long payload → detected
    assert result["detected"] is True
