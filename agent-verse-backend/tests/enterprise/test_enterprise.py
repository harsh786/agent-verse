"""Tests for enterprise modules: compliance, simulation, red-team, marketplace, self-optimization."""
from __future__ import annotations

import pytest

from app.enterprise.compliance import ComplianceController
from app.enterprise.marketplace import Marketplace
from app.enterprise.red_team import RedTeamRunner
from app.enterprise.simulation import SimulationRunner
from app.intelligence.eval import EvalScorecard
from app.intelligence.self_optimization import SelfOptimizer
from app.memory.long_term import LongTermMemory, LongTermMemoryStore
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="ent-test", plan=PlanTier.ENTERPRISE, api_key_id="ekey-1")

# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compliance_export_request() -> None:
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    assert req.status == "ready"
    assert req.tenant_id == T.tenant_id
    assert req.download_url.startswith("/compliance/export/")
    assert "tenant_id" in req.payload
    assert req.request_id  # non-empty

    # Should be retrievable by request_id
    fetched = await cc.get_export_status(request_id=req.request_id, tenant_ctx=T)
    assert fetched is not None
    assert fetched.request_id == req.request_id


@pytest.mark.asyncio
async def test_compliance_export_wrong_tenant_returns_none() -> None:
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    other = TenantContext(tenant_id="other-tenant", plan=PlanTier.STARTER, api_key_id="k2")
    assert await cc.get_export_status(request_id=req.request_id, tenant_ctx=other) is None


@pytest.mark.asyncio
async def test_compliance_data_deletion() -> None:
    cc = ComplianceController()
    result = await cc.request_data_deletion(tenant_ctx=T)
    assert result["deletion_scheduled"] is True
    assert result["tenant_id"] == T.tenant_id
    assert "scheduled_at" in result
    assert "30 days" in result["note"]


def test_compliance_residency() -> None:
    cc = ComplianceController()
    res = cc.get_data_residency(tenant_ctx=T)
    assert res["gdpr_compliant"] is True
    assert res["soc2_type2"] is True
    assert "primary_region" in res
    assert res["tenant_id"] == T.tenant_id


@pytest.mark.asyncio
async def test_compliance_retention_sweep_no_old_records() -> None:
    cc = ComplianceController()
    # All records are fresh — nothing should be swept
    await cc.request_data_export(tenant_ctx=T)
    result = cc.retention_sweep(retention_days=90)
    assert result["records_swept"] == 0
    assert result["retention_days"] == 90


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def test_simulation_run_completes() -> None:
    runner = SimulationRunner()
    run = runner.start(
        goal="Analyse logs for errors",
        mock_tools={"log_reader": {"response": "[ERROR] db timeout"}},
        tenant_ctx=T,
    )
    assert run.status == "completed"
    assert "simulated_steps" in run.result
    assert run.result["outcome"] == "success (simulated)"
    assert run.result["side_effects"] == []


def test_simulation_run_records_mock_tools() -> None:
    runner = SimulationRunner()
    tools = {"tool_a": {}, "tool_b": {}}
    run = runner.start(goal="Do something", mock_tools=tools, tenant_ctx=T)
    assert set(run.result["mock_tools_used"]) == {"tool_a", "tool_b"}


def test_simulation_get_run() -> None:
    runner = SimulationRunner()
    run = runner.start(goal="Test goal", mock_tools={}, tenant_ctx=T)
    fetched = runner.get(run_id=run.run_id, tenant_ctx=T)
    assert fetched is not None
    assert fetched.run_id == run.run_id
    assert fetched.goal == "Test goal"


def test_simulation_get_unknown_run_returns_none() -> None:
    runner = SimulationRunner()
    assert runner.get(run_id="nonexistent", tenant_ctx=T) is None


def test_simulation_list_runs() -> None:
    runner = SimulationRunner()
    runner.start(goal="goal 1", mock_tools={}, tenant_ctx=T)
    runner.start(goal="goal 2", mock_tools={}, tenant_ctx=T)
    runs = runner.list_runs(tenant_ctx=T)
    assert len(runs) == 2


# ---------------------------------------------------------------------------
# Red Team
# ---------------------------------------------------------------------------

def test_red_team_runs_all_cases() -> None:
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T)
    assert report.cases_run == 5
    assert report.cases_run == report.cases_passed + report.cases_failed
    assert len(report.results) == 5


def test_red_team_detects_prompt_injection() -> None:
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T, cases=["prompt_injection"])
    assert report.cases_run == 1
    result = report.results[0]
    assert result["case_id"] == "prompt_injection"
    assert result["detected"] is True
    assert result["outcome"] == "blocked"


def test_red_team_runs_subset_of_cases() -> None:
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T, cases=["prompt_injection", "data_exfiltration"])
    assert report.cases_run == 2
    case_ids = {r["case_id"] for r in report.results}
    assert case_ids == {"prompt_injection", "data_exfiltration"}


def test_red_team_report_stored_and_retrievable() -> None:
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T)
    fetched = runner.get_report(report_id=report.report_id, tenant_ctx=T)
    assert fetched is not None
    assert fetched.report_id == report.report_id


def test_red_team_unknown_report_returns_none() -> None:
    runner = RedTeamRunner()
    assert runner.get_report(report_id="ghost", tenant_ctx=T) is None


# ---------------------------------------------------------------------------
# Marketplace
# ---------------------------------------------------------------------------

def test_marketplace_browse_all() -> None:
    m = Marketplace()
    templates = m.browse(tenant_ctx=T)
    assert len(templates) >= 6  # at least all builtin templates


def test_marketplace_browse_by_domain() -> None:
    m = Marketplace()
    templates = m.browse(domain="software", tenant_ctx=T)
    assert len(templates) >= 1
    assert all(t["domain"] == "software" for t in templates)


def test_marketplace_browse_by_query() -> None:
    m = Marketplace()
    templates = m.browse(query="onboarding", tenant_ctx=T)
    assert len(templates) >= 1
    assert any("onboard" in t["description"].lower() for t in templates)


def test_marketplace_get_template() -> None:
    m = Marketplace()
    t = m.get_template(template_id="tpl-bug-fix")
    assert t is not None
    assert t["template_id"] == "tpl-bug-fix"
    assert t["domain"] == "software"


def test_marketplace_get_unknown_template_returns_none() -> None:
    m = Marketplace()
    assert m.get_template(template_id="no-such-template") is None


async def test_marketplace_deploy_template() -> None:
    m = Marketplace()
    dep = await m.deploy(
        template_id="tpl-bug-fix",
        params={"repo": "acme/backend", "label": "prod-down"},
        tenant_ctx=T,
    )
    assert dep.template_id == "tpl-bug-fix"
    assert dep.tenant_id == T.tenant_id
    assert dep.agent_id  # non-empty generated id
    assert dep.deployment_id  # non-empty


async def test_marketplace_unknown_template_raises() -> None:
    m = Marketplace()
    with pytest.raises(ValueError, match="not found"):
        await m.deploy(template_id="no-such-template", params={}, tenant_ctx=T)


def test_marketplace_publish_custom_template() -> None:
    m = Marketplace()
    custom = {
        "name": "My Custom Agent",
        "domain": "finance",
        "description": "Reconcile invoices daily",
        "connectors": ["quickbooks"],
    }
    published = m.publish(template=custom, tenant_ctx=T)
    assert published["template_id"].startswith("tpl-custom-")
    assert published["published_by"] == T.tenant_id

    # Published template should appear in browse
    templates = m.browse(tenant_ctx=T)
    assert any(t["template_id"] == published["template_id"] for t in templates)


# ---------------------------------------------------------------------------
# Self-optimization
# ---------------------------------------------------------------------------

def _low_scorecard(goal_id: str = "g-1") -> EvalScorecard:
    """Build a scorecard with uniformly low scores to trigger suggestions."""
    return EvalScorecard(
        goal_id=goal_id,
        scores={
            "task_completion": 0.2,
            "accuracy": 0.2,
            "efficiency": 0.2,
            "safety": 0.2,
            "coherence": 0.2,
        },
    )


def test_self_optimizer_generates_suggestions() -> None:
    opt = SelfOptimizer()
    scorecard = _low_scorecard()
    suggestions = opt.analyze_and_suggest(
        goal="some goal",
        scorecard=scorecard,
        error_log="tool not found in registry",
        tenant_ctx=T,
    )
    assert len(suggestions) >= 2  # prompt + tool_selection + retry_strategy all triggered
    categories = {s.category for s in suggestions}
    assert "prompt" in categories
    assert "tool_selection" in categories


def test_self_optimizer_generates_retry_suggestion_on_low_efficiency() -> None:
    opt = SelfOptimizer()
    scorecard = EvalScorecard(
        goal_id="g-eff",
        scores={
            "task_completion": 0.8,
            "accuracy": 0.8,
            "efficiency": 0.3,  # below 0.4 threshold
            "safety": 0.9,
            "coherence": 0.9,
        },
    )
    suggestions = opt.analyze_and_suggest(
        goal="some goal", scorecard=scorecard, error_log="", tenant_ctx=T
    )
    categories = {s.category for s in suggestions}
    assert "retry_strategy" in categories


def test_self_optimizer_no_suggestions_on_high_score() -> None:
    opt = SelfOptimizer()
    scorecard = EvalScorecard(
        goal_id="g-good",
        scores={
            "task_completion": 0.9,
            "accuracy": 0.9,
            "efficiency": 0.9,
            "safety": 1.0,
            "coherence": 0.9,
        },
    )
    suggestions = opt.analyze_and_suggest(
        goal="goal", scorecard=scorecard, error_log="", tenant_ctx=T
    )
    assert suggestions == []


def test_self_optimizer_apply_suggestion() -> None:
    opt = SelfOptimizer()
    suggestions = opt.analyze_and_suggest(
        goal="goal", scorecard=_low_scorecard(), error_log="", tenant_ctx=T
    )
    sid = suggestions[0].suggestion_id
    assert opt.apply_suggestion(suggestion_id=sid, tenant_ctx=T) is True

    applied = opt.list_suggestions(tenant_ctx=T, applied=True)
    assert any(s.suggestion_id == sid for s in applied)

    # Not-yet-applied suggestions should not appear in the applied list
    applied_ids = {s.suggestion_id for s in applied}
    for s in suggestions[1:]:
        if not s.applied:
            assert s.suggestion_id not in applied_ids


def test_self_optimizer_apply_unknown_suggestion_returns_false() -> None:
    opt = SelfOptimizer()
    assert opt.apply_suggestion(suggestion_id="ghost-id", tenant_ctx=T) is False


def test_self_optimizer_reject_suggestion() -> None:
    opt = SelfOptimizer()
    suggestions = opt.analyze_and_suggest(
        goal="goal", scorecard=_low_scorecard(), error_log="", tenant_ctx=T
    )
    sid = suggestions[0].suggestion_id
    assert opt.reject_suggestion(suggestion_id=sid, tenant_ctx=T) is True

    all_suggestions = opt.list_suggestions(tenant_ctx=T)
    rejected = next(s for s in all_suggestions if s.suggestion_id == sid)
    assert rejected.rejected is True


def test_self_optimizer_tenant_isolation() -> None:
    opt = SelfOptimizer()
    ctx_a = TenantContext(tenant_id="t-a", plan=PlanTier.ENTERPRISE, api_key_id="ka")
    ctx_b = TenantContext(tenant_id="t-b", plan=PlanTier.ENTERPRISE, api_key_id="kb")

    opt.analyze_and_suggest(
        goal="goal", scorecard=_low_scorecard("ga"), error_log="", tenant_ctx=ctx_a
    )
    assert opt.list_suggestions(tenant_ctx=ctx_b) == []


# ---------------------------------------------------------------------------
# Long-term memory
# ---------------------------------------------------------------------------

def test_long_term_memory_store_and_recall() -> None:
    store = LongTermMemoryStore()
    mem = LongTermMemory(
        content="use github.create_pr for opening pull requests on the main branch",
        source_goal_id="g-1",
        memory_type="tool_preference",
    )
    mid = store.store(memory=mem, tenant_ctx=T)
    assert mid == mem.memory_id

    results = store.recall(query="github pull request", tenant_ctx=T)
    assert len(results) > 0
    assert results[0].memory_id == mid


def test_long_term_memory_recall_by_type() -> None:
    store = LongTermMemoryStore()
    store.store(
        memory=LongTermMemory(
            content="prefer slack for notifications",
            source_goal_id="g-1",
            memory_type="tool_preference",
        ),
        tenant_ctx=T,
    )
    store.store(
        memory=LongTermMemory(
            content="production database is postgres 15",
            source_goal_id="g-2",
            memory_type="domain_fact",
        ),
        tenant_ctx=T,
    )

    prefs = store.recall(query="slack", tenant_ctx=T, memory_type="tool_preference")
    assert all(m.memory_type == "tool_preference" for m in prefs)

    facts = store.recall(query="database", tenant_ctx=T, memory_type="domain_fact")
    assert all(m.memory_type == "domain_fact" for m in facts)


def test_long_term_memory_delete() -> None:
    store = LongTermMemoryStore()
    mem = LongTermMemory(
        content="use slack for notifications",
        source_goal_id="g-2",
        memory_type="tool_preference",
    )
    mid = store.store(memory=mem, tenant_ctx=T)
    assert store.delete(memory_id=mid, tenant_ctx=T) is True
    assert store.list_all(tenant_ctx=T) == []


def test_long_term_memory_delete_nonexistent_returns_false() -> None:
    store = LongTermMemoryStore()
    assert store.delete(memory_id="ghost-id", tenant_ctx=T) is False


def test_long_term_memory_tenant_isolation() -> None:
    store = LongTermMemoryStore()
    ctx1 = TenantContext(tenant_id="tenant-1", plan=PlanTier.ENTERPRISE, api_key_id="k1")
    ctx2 = TenantContext(tenant_id="tenant-2", plan=PlanTier.ENTERPRISE, api_key_id="k2")

    store.store(
        memory=LongTermMemory(
            content="memory for tenant1 only",
            source_goal_id="g-1",
            memory_type="domain_fact",
        ),
        tenant_ctx=ctx1,
    )
    store.store(
        memory=LongTermMemory(
            content="memory for tenant2 only",
            source_goal_id="g-2",
            memory_type="domain_fact",
        ),
        tenant_ctx=ctx2,
    )

    t1_mems = store.list_all(tenant_ctx=ctx1)
    t2_mems = store.list_all(tenant_ctx=ctx2)

    assert len(t1_mems) == 1
    assert t1_mems[0].content == "memory for tenant1 only"
    assert len(t2_mems) == 1
    assert t2_mems[0].content == "memory for tenant2 only"


def test_long_term_memory_extract_from_goal() -> None:
    store = LongTermMemoryStore()
    ids = store.extract_from_goal(
        goal="Fix the authentication bug in the login flow",
        result="Applied patch to JWT handler, tests pass",
        goal_id="g-extract",
        tenant_ctx=T,
    )
    assert len(ids) == 1
    mems = store.list_all(tenant_ctx=T)
    assert len(mems) == 1
    assert mems[0].memory_type == "success_pattern"
    assert "auto-extracted" in mems[0].tags
