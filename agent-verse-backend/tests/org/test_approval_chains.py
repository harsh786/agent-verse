"""Tests for ApprovalChain — app/org/approval_chain.py"""
from __future__ import annotations

import pytest
from app.org.approval_chain import ApprovalChain, ApprovalChainRegistry, ApprovalRequest


def test_approval_chain_prod_deploy_requires_multiple():
    reg = ApprovalChainRegistry()
    chain = reg.get("prod_deploy")
    assert chain is not None
    assert len(chain.required_approvers) >= 2


def test_approval_chain_financial_commitment_requires_cfo():
    reg = ApprovalChainRegistry()
    chain = reg.get("financial_commitment_50k")
    assert chain is not None
    approver_names = [a.lower() for a in chain.required_approvers]
    assert any("cfo" in a or "finance" in a for a in approver_names)


def test_approval_chain_unknown_action_returns_none():
    reg = ApprovalChainRegistry()
    assert reg.get("nonexistent_action_xyz") is None


def test_approval_chain_policy_lookup():
    reg = ApprovalChainRegistry()
    chains = reg.list_all()
    assert len(chains) >= 3


def test_approval_request_creation():
    req = ApprovalRequest(
        request_id="req-test-001",
        chain_id="prod_deploy",
        action="Production Deployment",
        action_detail="Deploy v2.0 to production",
        mission_id="mission-123",
        agent_id="agent-123",
        tenant_id="t1",
        org_id="org1",
        approvers_needed=["qa_lead", "sre_lead"],
    )
    assert req.request_id == "req-test-001"
    assert req.status == "pending"


def test_approval_chain_timeout_configured():
    reg = ApprovalChainRegistry()
    chain = reg.get("prod_deploy")
    assert chain.timeout_hours > 0


def test_approval_chain_any_vs_all():
    """prod_deploy requires all approvers; not any."""
    reg = ApprovalChainRegistry()
    chain = reg.get("prod_deploy")
    assert chain.any_or_all == "all"
