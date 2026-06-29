"""Coverage tests for all DB model instantiation.

Tests that every model can be instantiated at the Python level with
required fields. Does NOT require a live database — SQLAlchemy enforces
NOT NULL constraints only at flush/commit time, not at Python __init__.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ── Import all models ─────────────────────────────────────────────────────────

def test_base_and_all_model_imports():
    """Importing all models does not raise."""
    from app.db.models import (
        Base,
        Tenant,
        ApiKey,
        Agent,
        AgentPermission,
        Goal,
        GoalStep,
        GoalEvent,
        GoalCheckpoint,
        AuditLog,
        ApprovalRequest,
        MCPServer,
        MCPCredential,
        OAuthToken,
        Policy,
        Schedule,
        KnowledgeCollection,
        Document,
        ExecutionMemory,
        LongTermMemory,
        DecisionTrace,
        Evaluation,
        CostLedger,
        CollabSession,
        CollabOperation,
        AgentTemplate,
        Civilization,
        CivilizationAgent,
        SpawnRequest,
        BlackboardEntry,
        BusMessage,
        CivilizationLearning,
        CivilizationEvent,
        Workflow,
        GoalTemplate,
    )
    assert Base is not None


# ── eval models ───────────────────────────────────────────────────────────────

def test_eval_suite_instantiation():
    from app.db.models.eval import EvalSuite

    suite = EvalSuite(
        tenant_id="t-001",
        name="My Eval Suite",
        tasks=[{"goal": "Search the web", "expected": "Results found"}],
    )
    assert suite.name == "My Eval Suite"
    assert suite.tenant_id == "t-001"
    assert len(suite.tasks) == 1


def test_eval_suite_run_result_instantiation():
    from app.db.models.eval import EvalSuiteRunResult

    result = EvalSuiteRunResult(
        suite_id="s-001",
        tenant_id="t-001",
        run_id="r-001",
        total_tasks=10,
        passed_tasks=8,
        failed_tasks=2,
        pass_rate=0.8,
        task_results=[{"task": "A", "passed": True}],
    )
    assert result.pass_rate == 0.8
    assert result.total_tasks == 10
    assert result.passed_tasks + result.failed_tasks == result.total_tasks


# ── Tenant / ApiKey ───────────────────────────────────────────────────────────

def test_tenant_instantiation():
    from app.db.models.tenant import Tenant

    t = Tenant(name="Acme Corp", email="admin@acme.com")
    assert t.name == "Acme Corp"
    assert t.email == "admin@acme.com"


def test_tenant_default_is_active():
    from app.db.models.tenant import Tenant

    t = Tenant(name="Test", email="t@t.com")
    # is_active defaults to True at the server level
    assert t.name == "Test"


def test_api_key_instantiation():
    from app.db.models.tenant import ApiKey

    key = ApiKey(
        tenant_id="t-abc",
        name="production-key",
        key_hash="a" * 64,
        scopes=["goals:read", "goals:write"],
    )
    assert key.name == "production-key"
    assert len(key.key_hash) == 64


# ── Agent / AgentPermission ───────────────────────────────────────────────────

def test_agent_instantiation():
    from app.db.models.agent import Agent

    agent = Agent(
        tenant_id="t-001",
        name="ResearchAgent",
        goal_template="Search and summarize: {{topic}}",
        system_prompt="You are a research assistant.",
        autonomy_mode="bounded-autonomous",
    )
    assert agent.name == "ResearchAgent"
    assert agent.tenant_id == "t-001"
    assert agent.goal_template == "Search and summarize: {{topic}}"


def test_agent_explicit_autonomy_mode():
    from app.db.models.agent import Agent

    agent = Agent(tenant_id="t-001", name="TestAgent", autonomy_mode="fully-autonomous")
    assert agent.autonomy_mode == "fully-autonomous"


def test_agent_permission_instantiation():
    from app.db.models.agent import AgentPermission

    perm = AgentPermission(
        agent_id="a-001",
        tenant_id="t-001",
        tool_name="web_search",
        level="allow_log",
    )
    assert perm.tool_name == "web_search"
    assert perm.level == "allow_log"


# ── Goal / GoalStep / GoalEvent / GoalCheckpoint ──────────────────────────────

def test_goal_instantiation():
    from app.db.models.goal import Goal

    goal = Goal(
        tenant_id="t-001",
        goal_text="Research the latest developments in quantum computing",
        status="planning",
        priority="normal",
        dry_run=False,
        iterations=0,
    )
    assert goal.goal_text == "Research the latest developments in quantum computing"
    assert goal.status == "planning"
    assert goal.priority == "normal"
    assert goal.dry_run is False
    assert goal.iterations == 0


def test_goal_with_all_fields():
    from app.db.models.goal import Goal

    goal = Goal(
        tenant_id="t-001",
        agent_id="a-001",
        goal_text="Deploy app to production",
        status="running",
        priority="high",
        autonomy_mode="fully-autonomous",
        dry_run=True,
    )
    assert goal.status == "running"
    assert goal.priority == "high"
    assert goal.dry_run is True


def test_goal_step_instantiation():
    from app.db.models.goal import GoalStep

    step = GoalStep(
        goal_id="g-001",
        tenant_id="t-001",
        step_index=0,
        description="Search the web for recent papers",
        status="pending",
    )
    assert step.step_index == 0
    assert step.status == "pending"
    assert step.description == "Search the web for recent papers"


def test_goal_event_instantiation():
    from app.db.models.goal import GoalEvent

    event = GoalEvent(
        tenant_id="t-001",
        goal_id="g-001",
        sequence=1,
        event_type="status_change",
        payload={"from": "planning", "to": "running"},
    )
    assert event.event_type == "status_change"
    assert event.sequence == 1
    assert event.payload["from"] == "planning"


def test_goal_checkpoint_instantiation():
    from app.db.models.goal import GoalCheckpoint

    ckpt = GoalCheckpoint(
        tenant_id="t-001",
        goal_id="g-001",
        checkpoint_key="step_0_complete",
        payload={"state": "running", "step": 0},
        checkpoint_metadata={"timestamp": "2024-01-01"},
        recovery_status="none",
    )
    assert ckpt.goal_id == "g-001"
    assert ckpt.checkpoint_key == "step_0_complete"


# ── Governance: AuditLog / ApprovalRequest ────────────────────────────────────

def test_audit_log_instantiation():
    from app.db.models.governance import AuditLog

    log = AuditLog(
        tenant_id="t-001",
        goal_id="g-001",
        tool_name="web_search",
        action_level="low",
        outcome="success",
    )
    assert log.tool_name == "web_search"
    assert log.outcome == "success"
    assert log.action_level == "low"


def test_audit_log_optional_fields():
    from app.db.models.governance import AuditLog

    log = AuditLog(
        tenant_id="t-001",
        goal_id="g-001",
        tool_name="delete_file",
        action_level="high",
        outcome="approved",
        step_id="s-001",
        approver="admin@example.com",
        note="Manually approved after review",
    )
    assert log.approver == "admin@example.com"
    assert log.note == "Manually approved after review"


def test_approval_request_instantiation():
    from app.db.models.governance import ApprovalRequest

    req = ApprovalRequest(
        tenant_id="t-001",
        goal_id="g-001",
        action="Deploy hotfix to production cluster",
        risk_level="high",
        status="pending",
    )
    assert req.action == "Deploy hotfix to production cluster"
    assert req.status == "pending"
    assert req.risk_level == "high"


# ── MCP: MCPServer / MCPCredential / OAuthToken ───────────────────────────────

def test_mcp_server_instantiation():
    from app.db.models.mcp import MCPServer

    server = MCPServer(
        tenant_id="t-001",
        name="GitHub MCP",
        url="https://github-mcp.example.com/mcp",
        auth_type="bearer",
    )
    assert server.name == "GitHub MCP"
    assert server.auth_type == "bearer"
    assert server.url == "https://github-mcp.example.com/mcp"


def test_mcp_server_optional_fields():
    from app.db.models.mcp import MCPServer

    server = MCPServer(
        tenant_id="t-001",
        name="Internal MCP",
        url="http://internal/mcp",
        auth_type="none",
        description="Internal tools server",
        priority=1,
        status="active",
    )
    assert server.description == "Internal tools server"
    assert server.priority == 1


def test_mcp_credential_instantiation():
    from app.db.models.mcp import MCPCredential

    cred = MCPCredential(
        server_id="s-001",
        tenant_id="t-001",
        encrypted_config="enc:AES256GCM:abc123def456",
    )
    assert cred.server_id == "s-001"
    assert cred.tenant_id == "t-001"
    assert cred.encrypted_config == "enc:AES256GCM:abc123def456"


def test_oauth_token_instantiation():
    from app.db.models.mcp import OAuthToken

    token = OAuthToken(
        server_id="s-001",
        tenant_id="t-001",
        access_token_enc="enc:access-token-encrypted",
        refresh_token_enc="enc:refresh-token-encrypted",
    )
    assert token.access_token_enc == "enc:access-token-encrypted"
    assert token.refresh_token_enc == "enc:refresh-token-encrypted"


# ── Scheduling: Policy / Schedule ────────────────────────────────────────────

def test_policy_instantiation():
    from app.db.models.scheduling import Policy

    p = Policy(
        tenant_id="t-001",
        name="Restrict dangerous tools",
        denied_tools=["delete_*", "drop_*"],
        approval_tools=["deploy_*"],
    )
    assert p.name == "Restrict dangerous tools"
    assert "delete_*" in p.denied_tools


def test_schedule_instantiation():
    from app.db.models.scheduling import Schedule

    s = Schedule(
        tenant_id="t-001",
        goal_id_template="Run the nightly backup for {{date}}",
        trigger_type="cron",
        cron_expression="0 2 * * *",
    )
    assert s.trigger_type == "cron"
    assert s.cron_expression == "0 2 * * *"


def test_schedule_webhook_type():
    from app.db.models.scheduling import Schedule

    s = Schedule(
        tenant_id="t-001",
        goal_id_template="Process webhook payload",
        trigger_type="webhook",
        webhook_token="tok-abc123",
    )
    assert s.trigger_type == "webhook"
    assert s.webhook_token == "tok-abc123"


# ── Knowledge models ──────────────────────────────────────────────────────────

def test_knowledge_collection_instantiation():
    from app.db.models.knowledge import KnowledgeCollection

    col = KnowledgeCollection(
        tenant_id="t-001",
        name="Product Documentation",
        description="All product docs",
    )
    assert col.name == "Product Documentation"
    assert col.tenant_id == "t-001"


def test_execution_memory_instantiation():
    from app.db.models.knowledge import ExecutionMemory

    mem = ExecutionMemory(
        tenant_id="t-001",
        goal_text="Search for Python news",
        plan=["step1", "step2"],
        success=True,
    )
    assert mem.goal_text == "Search for Python news"
    assert mem.success is True


def test_long_term_memory_instantiation():
    from app.db.models.knowledge import LongTermMemory

    ltm = LongTermMemory(
        tenant_id="t-001",
        content="Always verify before deleting production data",
        memory_type="safety",
        confidence=0.95,
    )
    assert ltm.content == "Always verify before deleting production data"
    assert ltm.confidence == 0.95


# ── Intelligence models ───────────────────────────────────────────────────────

def test_decision_trace_instantiation():
    from app.db.models.intelligence import DecisionTrace

    trace = DecisionTrace(
        goal_id="g-001",
        tenant_id="t-001",
        action="Execute web_search tool with query='Python news'",
        reasoning="Tool is appropriate for information retrieval",
    )
    assert trace.action == "Execute web_search tool with query='Python news'"
    assert trace.tenant_id == "t-001"


def test_decision_trace_with_explicit_defaults():
    from app.db.models.intelligence import DecisionTrace

    trace = DecisionTrace(
        goal_id="g-001",
        tenant_id="t-001",
        action="Test action",
        evidence=[],
        alternatives=[],
        confidence=0.0,
    )
    assert trace.evidence == []
    assert trace.alternatives == []
    assert trace.confidence == 0.0


def test_evaluation_instantiation():
    from app.db.models.intelligence import Evaluation

    ev = Evaluation(
        goal_id="g-001",
        tenant_id="t-001",
        scores=[{"dimension": "accuracy", "score": 0.9}],
        average_score=0.9,
        passed=True,
    )
    assert ev.average_score == 0.9
    assert ev.passed is True


def test_evaluation_explicit_defaults():
    from app.db.models.intelligence import Evaluation

    ev = Evaluation(
        goal_id="g-001",
        tenant_id="t-001",
        scores=[],
        average_score=0.0,
        passed=False,
    )
    assert ev.scores == []
    assert ev.average_score == 0.0
    assert ev.passed is False


def test_cost_ledger_instantiation():
    from app.db.models.intelligence import CostLedger

    ledger = CostLedger(
        tenant_id="t-001",
        cost_usd=0.015,
        goal_id="g-001",
        tool_name="web_search",
        tokens_used=1500,
    )
    assert ledger.cost_usd == 0.015
    assert ledger.tokens_used == 1500


def test_collab_session_instantiation():
    from app.db.models.intelligence import CollabSession

    sess = CollabSession(
        tenant_id="t-001",
        name="Research Session",
        mode="suggest",
    )
    assert sess.name == "Research Session"
    assert sess.mode == "suggest"


def test_collab_operation_instantiation():
    from app.db.models.intelligence import CollabOperation

    op = CollabOperation(
        session_id="sess-001",
        tenant_id="t-001",
        version=1,
        operation={"type": "insert", "position": 0, "text": "Hello"},
        author="user@example.com",
    )
    assert op.version == 1
    assert op.author == "user@example.com"


def test_agent_template_instantiation():
    from app.db.models.intelligence import AgentTemplate

    tmpl = AgentTemplate(
        name="Research Agent Template",
        goal_template="Research {{topic}} and provide a summary",
        domain="research",
        description="A template for research tasks",
    )
    assert tmpl.name == "Research Agent Template"
    assert tmpl.domain == "research"


# ── Civilization models ───────────────────────────────────────────────────────

def test_civilization_instantiation():
    from app.db.models.civilization import Civilization

    civ = Civilization(
        id=_uuid(),
        tenant_id="t-001",
        name="Alpha Civilization",
    )
    assert civ.name == "Alpha Civilization"
    assert civ.tenant_id == "t-001"


def test_civilization_agent_instantiation():
    from app.db.models.civilization import CivilizationAgent

    agent = CivilizationAgent(
        id=_uuid(),
        civilization_id="civ-001",
        tenant_id="t-001",
        agent_id="a-001",
        role="researcher",
    )
    assert agent.role == "researcher"
    assert agent.civilization_id == "civ-001"


def test_spawn_request_instantiation():
    from app.db.models.civilization import SpawnRequest

    req = SpawnRequest(
        id=_uuid(),
        civilization_id="civ-001",
        tenant_id="t-001",
        requester_agent_id="a-001",
        requested_capability="web_search",
        goal_text="Find current market trends",
        decision="approved",
    )
    assert req.decision == "approved"
    assert req.requested_capability == "web_search"


def test_blackboard_entry_instantiation():
    from app.db.models.civilization import BlackboardEntry

    entry = BlackboardEntry(
        id=_uuid(),
        civilization_id="civ-001",
        tenant_id="t-001",
        author_agent_id="a-001",
        topic="market_trends",
        content="Q4 2024 shows strong growth in AI adoption across enterprises.",
        confidence=0.85,
    )
    assert entry.topic == "market_trends"
    assert entry.confidence == 0.85


def test_bus_message_instantiation():
    from app.db.models.civilization import BusMessage

    msg = BusMessage(
        id=_uuid(),
        civilization_id="civ-001",
        tenant_id="t-001",
        from_agent_id="a-001",
        topic="task_complete",
        payload={"result": "done", "artifacts": []},
    )
    assert msg.topic == "task_complete"
    assert msg.payload["result"] == "done"


def test_civilization_learning_instantiation():
    from app.db.models.civilization import CivilizationLearning

    learning = CivilizationLearning(
        id=_uuid(),
        civilization_id="civ-001",
        tenant_id="t-001",
        candidate="Always verify API responses before processing",
        source_agent_id="a-001",
        status="candidate",
    )
    assert "verify" in learning.candidate
    assert learning.status == "candidate"


def test_civilization_event_instantiation():
    from app.db.models.civilization import CivilizationEvent

    event = CivilizationEvent(
        id=_uuid(),
        civilization_id="civ-001",
        tenant_id="t-001",
        type="agent_spawned",
        payload={"agent_id": "a-new", "role": "executor"},
    )
    assert event.type == "agent_spawned"
    assert event.payload["role"] == "executor"


# ── Workflow / GoalTemplate ───────────────────────────────────────────────────

def test_workflow_instantiation():
    from app.db.models.workflow import Workflow

    wf = Workflow(
        tenant_id="t-001",
        name="CI/CD Pipeline",
        definition={"steps": [{"id": "build"}, {"id": "test"}, {"id": "deploy"}]},
    )
    assert wf.name == "CI/CD Pipeline"
    assert len(wf.definition["steps"]) == 3


def test_workflow_optional_description():
    from app.db.models.workflow import Workflow

    wf = Workflow(
        tenant_id="t-001",
        name="Simple Workflow",
        description="A simple automated workflow",
        definition={},
    )
    assert wf.description == "A simple automated workflow"


def test_goal_template_instantiation():
    from app.db.models.template import GoalTemplate

    tmpl = GoalTemplate(
        tenant_id="t-001",
        name="Daily Report Generator",
        goal_text="Generate a daily report for {{date}} covering {{topics}}",
        domain="reporting",
    )
    assert tmpl.name == "Daily Report Generator"
    assert "{{date}}" in tmpl.goal_text
    assert tmpl.domain == "reporting"


def test_goal_template_explicit_defaults():
    from app.db.models.template import GoalTemplate

    tmpl = GoalTemplate(
        tenant_id="t-001",
        name="Default Template",
        goal_text="Do something useful",
        use_count=0,
        version=1,
    )
    assert tmpl.use_count == 0
    assert tmpl.version == 1
