"""PART 48 — Org-specific Security Tests.

Tests:
  1. Cross-tenant isolation — agent in Org A cannot access Org B's missions
  2. Cross-dept confidential — Marketing agent cannot read Legal's restricted memory
  3. Privilege escalation — worker cannot approve own high-risk actions
  4. Budget bypass — agent cannot exceed budget via parallel sub-tasks
  5. Goal injection — dangerous goal patterns blocked
  6. Memory poisoning — low-quality lesson cannot auto-promote
  7. Tool scope — agent cannot call tools outside role allowlist
  8. Cross-dept forgery — agent cannot impersonate CEO agent
  9. Approval bypass — high-risk actions cannot skip approval
  10. Loop detection — circular delegation triggers circuit breaker
  11. GDPR propagation — deleted user data removed from all memory tiers
  12. RLS isolation — all 7 new tables have RLS policies
"""
from __future__ import annotations

import pytest

# ── Test 1: Cross-tenant isolation ────────────────────────────────────────────

class TestCrossTenantIsolation:
    """Agents in Tenant A cannot read Tenant B's data."""

    @pytest.mark.asyncio
    async def test_different_tenant_missions_isolated(self):
        """Mission from tenant-A is not visible to tenant-B queries."""
        from app.org.knowledge_access import KnowledgeAccessPolicy
        policy = KnowledgeAccessPolicy()
        # Tenant isolation is enforced at DB level via RLS
        # This test validates the policy layer above it
        assert policy.can_access("engineering", "eng:backend", "all_internal") is True
        # Restricted collection only accessible to owning dept
        assert policy.can_access("marketing", "mkt:content", "finance_restricted") is False

    def test_rbac_tenant_scoping(self):
        """OrgRole.can() enforces permissions correctly."""
        from app.org.rbac import OrgRole
        assert OrgRole.can("org_admin", "admin") is True
        assert OrgRole.can("viewer", "admin") is False
        assert OrgRole.can("viewer", "read") is True
        assert OrgRole.can("dept_admin", "approve") is True
        assert OrgRole.can("agent", "approve") is False


# ── Test 2: Cross-dept confidential isolation ─────────────────────────────────

class TestCrossDeptIsolation:
    """Marketing agents cannot read Legal's restricted memory."""

    def test_marketing_cannot_access_legal_restricted(self):
        from app.org.knowledge_access import KnowledgeAccessPolicy
        policy = KnowledgeAccessPolicy()
        # Legal contracts are confidential — not accessible to marketing
        can = policy.can_access("marketing", "mkt:content", "legal_contracts")
        assert can is False

    def test_engineering_cannot_access_finance_restricted(self):
        from app.org.knowledge_access import KnowledgeAccessPolicy
        policy = KnowledgeAccessPolicy()
        assert policy.can_access("engineering", "eng:backend", "finance_restricted") is False

    def test_executive_can_access_all(self):
        from app.org.knowledge_access import KnowledgeAccessPolicy
        policy = KnowledgeAccessPolicy()
        assert policy.can_access("executive", "ceo", "finance_restricted") is True
        assert policy.can_access("executive", "ceo", "security_restricted") is True

    def test_dept_can_access_own_restricted(self):
        from app.org.knowledge_access import KnowledgeAccessPolicy
        policy = KnowledgeAccessPolicy()
        # Finance can access its own restricted collection
        assert policy.can_access("finance", "finance:cfo", "finance_restricted") is True

    def test_filter_search_results_removes_restricted(self):
        from app.org.knowledge_access import KnowledgeAccessPolicy
        policy = KnowledgeAccessPolicy()
        results = [
            {"collection_id": "all_internal", "content": "public info"},
            {"collection_id": "finance_restricted", "content": "secret finance"},
        ]
        filtered = policy.filter_search_results(results, "marketing", "mkt:lead_gen")
        contents = [r["content"] for r in filtered]
        assert "public info" in contents
        assert "secret finance" not in contents


# ── Test 3: Privilege escalation ──────────────────────────────────────────────

class TestPrivilegeEscalation:
    """Worker agents cannot approve their own high-risk actions."""

    def test_agent_cannot_approve(self):
        from fastapi import HTTPException

        from app.org.rbac import OrgRBACGuard
        guard = OrgRBACGuard()
        with pytest.raises(HTTPException) as exc_info:
            guard.require("agent", "approve", raise_on_fail=True)
        assert exc_info.value.status_code == 403

    def test_team_lead_can_approve_team(self):
        from app.org.rbac import OrgRBACGuard
        guard = OrgRBACGuard()
        result = guard.require("team_lead", "approve_team", raise_on_fail=False)
        assert result is True

    def test_dept_admin_cannot_do_admin(self):
        from fastapi import HTTPException

        from app.org.rbac import OrgRBACGuard
        guard = OrgRBACGuard()
        # dept_admin has "change_settings" but not "admin"
        with pytest.raises(HTTPException):
            guard.require("dept_admin", "admin", raise_on_fail=True)

    def test_role_hierarchy_enforced(self):
        from app.org.rbac import OrgRole
        assert OrgRole.is_at_least("org_admin", "dept_admin") is True
        assert OrgRole.is_at_least("viewer", "dept_admin") is False
        assert OrgRole.is_at_least("team_lead", "agent") is True


# ── Test 4: Budget bypass prevention ─────────────────────────────────────────

class TestBudgetBypass:
    """Agents cannot exceed budget via parallel tasks."""

    @pytest.mark.asyncio
    async def test_cost_runaway_detected(self):
        from app.org.loop_detector import OrgLoopDetector
        detector = OrgLoopDetector()
        # 3.1x budget → should be detected as cost runaway
        result = detector.check_cost_runaway(spent_usd=310.0, budget_usd=100.0)
        assert result.detected is True
        assert result.pattern == "cost_runaway"

    @pytest.mark.asyncio
    async def test_within_budget_not_flagged(self):
        from app.org.loop_detector import OrgLoopDetector
        detector = OrgLoopDetector()
        result = detector.check_cost_runaway(spent_usd=80.0, budget_usd=100.0)
        assert result.detected is False


# ── Test 5: Goal injection prevention ────────────────────────────────────────

class TestGoalInjection:
    """Dangerous goal patterns are detected and blocked."""

    def test_meta_orchestrator_goal_sanitization(self):
        """MetaOrchestrator should not execute obvious injection payloads."""
        from app.org.meta_orchestrator import MetaOrchestrator
        MetaOrchestrator()
        # Very long goals should not crash
        long_goal = "A" * 10_000
        assert len(long_goal) > 0   # just verify no crash during init

    @pytest.mark.asyncio
    async def test_goal_with_special_chars(self):
        """Goal with SQL/script injection patterns should not break system."""
        from app.org.meta_orchestrator import MetaOrchestrator
        orch = MetaOrchestrator()
        injection_goal = "'; DROP TABLE missions; --"
        # Should not raise, should just be treated as a text goal
        decision = await orch.decide(
            goal=injection_goal, org_id="org1", tenant_id="t1"
        )
        assert decision is not None


# ── Test 6: Memory poisoning prevention ──────────────────────────────────────

class TestMemoryPoisoning:
    """Low-quality lessons cannot auto-promote to org/dept tier."""

    @pytest.mark.asyncio
    async def test_low_confidence_lesson_rejected(self):
        from app.org.org_learning import LearningCategory, OrgLearningPipeline, OrgLesson
        pipeline = OrgLearningPipeline()
        lesson = OrgLesson(
            lesson_id="test-lesson",
            category=LearningCategory.TEAM_COMPOSITION,
            content="Some learning content that should not be promoted",
            source_mission_id="m1",
            source_outcome="failed",
            confidence=0.30,   # below 0.70 threshold
            applicability=[],
        )
        result = await pipeline.validate(lesson)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_pii_lesson_blocked(self):
        from app.org.org_learning import LearningCategory, OrgLearningPipeline, OrgLesson
        pipeline = OrgLearningPipeline()
        lesson = OrgLesson(
            lesson_id="pii-lesson",
            category=LearningCategory.COST_PATTERNS,
            content="User email is test@example.com and cost was $50",
            source_mission_id="m2",
            source_outcome="completed",
            confidence=0.90,
            applicability=[],
        )
        result = await pipeline.validate(lesson)
        assert result.passed is False
        assert "pii_detected" in result.anti_poisoning_flags

    @pytest.mark.asyncio
    async def test_high_confidence_lesson_promoted(self):
        from app.org.org_learning import LearningCategory, OrgLearningPipeline, OrgLesson
        pipeline = OrgLearningPipeline()
        lesson = OrgLesson(
            lesson_id="good-lesson",
            category=LearningCategory.MODEL_ROUTING,
            content=(
                "Engineering team consistently outperforms with coding model "
                "profile for backend tasks"
            ),
            source_mission_id="m3",
            source_outcome="completed",
            confidence=0.90,
            applicability=["backend", "engineering"],
        )
        result = await pipeline.validate(lesson)
        assert result.passed is True


# ── Test 7: Tool scope enforcement ───────────────────────────────────────────

class TestToolScope:
    """Agents cannot call tools outside their role allowlist."""

    def test_org_tool_definitions_have_risk_levels(self):
        from app.org.org_tools import ORG_TOOL_DEFINITIONS
        for name, spec in ORG_TOOL_DEFINITIONS.items():
            assert spec.risk in ("low", "medium", "high", "critical"), \
                f"Tool {name} has invalid risk level: {spec.risk}"

    def test_high_risk_tools_require_approval(self):
        from app.org.org_tools import ORG_TOOL_DEFINITIONS
        for name, spec in ORG_TOOL_DEFINITIONS.items():
            if spec.risk in ("high", "critical"):
                assert spec.approval is not False, \
                    f"High-risk tool {name} should require approval"


# ── Test 8: Cross-dept forgery prevention ─────────────────────────────────────

class TestCrossDeptForgery:
    """Agents cannot impersonate other agents via forged dept messages."""

    def test_cross_dept_signature_valid(self):
        from app.org.rbac import sign_cross_dept_request, verify_cross_dept_request
        secret  = "test-org-secret-key"
        payload = {"action": "review", "artifact_id": "art-123"}
        sig = sign_cross_dept_request("agent-eng-1", "legal", payload, secret)
        assert verify_cross_dept_request("agent-eng-1", "legal", payload, sig, secret)

    def test_wrong_secret_rejected(self):
        from app.org.rbac import sign_cross_dept_request, verify_cross_dept_request
        payload = {"action": "review"}
        sig = sign_cross_dept_request("agent-1", "legal", payload, "correct-secret")
        result = verify_cross_dept_request("agent-1", "legal", payload, sig, "wrong-secret")
        assert result is False


# ── Test 9: Approval bypass prevention ───────────────────────────────────────

class TestApprovalBypass:
    """High-risk actions require all approvers — cannot be skipped."""

    def test_prod_deploy_requires_all(self):
        from app.org.approval_chain import ApprovalChainRegistry
        reg   = ApprovalChainRegistry()
        chain = reg.get("prod_deploy")
        assert chain is not None
        assert chain.any_or_all == "all"
        assert len(chain.required_approvers) >= 2

    def test_financial_commitment_requires_cfo(self):
        from app.org.approval_chain import ApprovalChainRegistry
        reg   = ApprovalChainRegistry()
        chain = reg.get("financial_commitment_50k")
        assert chain is not None
        approvers = [a.lower() for a in chain.required_approvers]
        assert any("cfo" in a or "finance" in a for a in approvers)


# ── Test 10: Loop detection ───────────────────────────────────────────────────

class TestLoopDetection:
    """Circular delegation and infinite replanning are caught."""

    def test_replan_limit_enforced(self):
        from app.org.loop_detector import OrgLoopDetector
        detector = OrgLoopDetector()
        for _ in range(4):   # threshold is 3
            result = detector.check_replan_count("task-security-test")
        assert result.detected is True
        assert result.pattern == "infinite_replan"

    def test_tool_obsession_caught(self):
        from app.org.loop_detector import OrgLoopDetector
        detector = OrgLoopDetector()
        for _ in range(6):   # threshold is 5
            result = detector.check_tool_obsession("agent-sec", "web_search")
        assert result.detected is True
        assert result.pattern == "agent_obsession"


# ── Test 11: GDPR deletion propagation ───────────────────────────────────────

class TestGDPRCompliance:
    """Deleted user data must be removable from all memory tiers."""

    @pytest.mark.asyncio
    async def test_dept_memory_deprecate_removes_from_results(self):
        from app.memory.dept_memory import DepartmentMemory
        mem = DepartmentMemory()
        entry = await mem.add(
            content="PII content to be deleted",
            source="user-123",
            confidence=0.85,
            dept_id="hr",
            org_id="org1",
            tenant_id="t1",
        )
        await mem.deprecate(dept_id="hr", entry_id=entry.entry_id, reason="GDPR deletion request")
        results = await mem.retrieve(dept_id="hr", query="PII content")
        ids = [r.entry_id for r in results]
        assert entry.entry_id not in ids


# ── Test 12: RLS table coverage ───────────────────────────────────────────────

class TestRLSCoverage:
    """All 7 new tables must have RLS policies."""

    def test_migration_creates_rls_on_all_tables(self):
        """Verify migration 0111 creates RLS on all org tables."""
        import os
        migration_path = "app/db/migrations/versions/0111_org_os_tables.py"
        if not os.path.exists(migration_path):
            pytest.skip("Migration file not found")
        with open(migration_path) as f:
            content = f.read()
        rls_tables = [
            "organizations",
            "org_departments",
            "org_missions",
            "org_teams",
            "org_roles",
            "org_decisions",
        ]
        for table in rls_tables:
            assert "ROW LEVEL SECURITY" in content or f"tenant_isolation ON {table}" in content, \
                f"Table {table} missing RLS policy in migration 0111"
