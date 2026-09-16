"""Tests for the extended role taxonomy + agent status state machine —
app/org/role_taxonomy.py"""
from __future__ import annotations

from app.org.role_taxonomy import (
    AGENT_TRANSITIONS,
    FULL_ROLE_TAXONOMY,
    AgentStatus,
    FullRoleDefinition,
    count_total_roles,
    get_role,
    get_roles_for_dept,
    validate_transition,
)


# ── AgentStatus state machine ──────────────────────────────────────────────────


def test_valid_transition_idle_to_planning():
    assert validate_transition(AgentStatus.IDLE, AgentStatus.PLANNING) is True


def test_invalid_transition_idle_to_completed():
    assert validate_transition(AgentStatus.IDLE, AgentStatus.COMPLETED) is False


def test_completed_has_no_outgoing_transitions():
    assert AGENT_TRANSITIONS[AgentStatus.COMPLETED] == []
    assert validate_transition(AgentStatus.COMPLETED, AgentStatus.IDLE) is False


def test_failed_can_reset_to_idle():
    assert validate_transition(AgentStatus.FAILED, AgentStatus.IDLE) is True


def test_executing_can_reach_multiple_next_states():
    for target in (
        AgentStatus.WAITING_TOOL,
        AgentStatus.WAITING_APPROVAL,
        AgentStatus.VERIFYING,
        AgentStatus.BLOCKED,
        AgentStatus.FAILED,
    ):
        assert validate_transition(AgentStatus.EXECUTING, target) is True


def test_unknown_current_status_has_no_valid_transitions():
    # A status absent from AGENT_TRANSITIONS should default to an empty list.
    assert validate_transition("not_a_real_status", AgentStatus.IDLE) is False


def test_all_agent_status_values_are_lowercase_strings():
    for status in AgentStatus:
        assert status.value == status.value.lower()


# ── FULL_ROLE_TAXONOMY ────────────────────────────────────────────────────────


def test_full_role_taxonomy_covers_22_departments():
    assert len(FULL_ROLE_TAXONOMY) == 22


def test_get_role_case_insensitive_lookup():
    role = get_role("executive", "ceo")
    assert role is not None
    assert role.name == "CEO"
    assert isinstance(role, FullRoleDefinition)


def test_get_role_unknown_role_returns_none():
    assert get_role("executive", "Not A Real Role") is None


def test_get_role_unknown_department_returns_none():
    assert get_role("not_a_dept", "CEO") is None


def test_get_roles_for_dept_returns_list():
    roles = get_roles_for_dept("engineering")
    assert len(roles) >= 1
    assert all(r.department == "engineering" for r in roles)


def test_get_roles_for_dept_unknown_returns_empty_list():
    assert get_roles_for_dept("not_a_dept") == []


def test_count_total_roles_matches_manual_sum():
    manual_sum = sum(len(roles) for roles in FULL_ROLE_TAXONOMY.values())
    assert count_total_roles() == manual_sum
    assert count_total_roles() > 0


def test_full_role_definition_defaults():
    role = FullRoleDefinition(name="Test Role", department="test", seniority="mid")
    assert role.capabilities == []
    assert role.model_profile == "smart"
    assert role.cost_category == "specialist"
    assert role.autonomy_level == 3
    assert role.risk_level == "medium"
    assert role.quality_threshold == 0.80
    assert role.decision_authority == []
    assert role.requires_approval_for == []


def test_specific_roles_carry_approval_requirements():
    cfo = get_role("executive", "CFO")
    assert "spend_gt_50k" in cfo.requires_approval_for

    sdr = get_role("sales", "SDR")
    assert "email_send_gt_100" in sdr.requires_approval_for
