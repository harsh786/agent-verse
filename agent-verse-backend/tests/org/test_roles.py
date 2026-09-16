"""Tests for the 456-role taxonomy module — app/org/roles.py"""
from __future__ import annotations

from app.org.roles import (
    DEPT_ROLE_MAP,
    ROLE_TAXONOMY,
    TOTAL_ROLES,
    RoleDefinition,
    count_roles,
    get_role_by_name,
    get_roles_for_dept,
)


def test_role_taxonomy_is_populated():
    assert len(ROLE_TAXONOMY) > 0
    assert len(ROLE_TAXONOMY) == TOTAL_ROLES


def test_role_taxonomy_covers_22_departments():
    depts = {role.department_id for role in ROLE_TAXONOMY.values()}
    assert len(depts) == 22


def test_role_definition_fields_populated_for_executive():
    ceo = ROLE_TAXONOMY["executive:ceo"]
    assert isinstance(ceo, RoleDefinition)
    assert ceo.name == "CEO"
    assert ceo.department_id == "executive"
    assert ceo.seniority == "staff"
    assert ceo.primary_model == "premium"
    assert ceo.risk_level == "high"
    assert ceo.quality_threshold == 0.95


def test_dept_role_map_matches_taxonomy():
    for dept, role_ids in DEPT_ROLE_MAP.items():
        for rid in role_ids:
            assert ROLE_TAXONOMY[rid].department_id == dept


def test_get_roles_for_dept_returns_all_roles_for_department():
    roles = get_roles_for_dept("engineering")
    assert len(roles) > 0
    assert all(r.department_id == "engineering" for r in roles)


def test_get_roles_for_dept_unknown_department_returns_empty():
    assert get_roles_for_dept("not_a_real_dept") == []


def test_get_role_by_name_case_insensitive():
    role = get_role_by_name("ceo")
    assert role is not None
    assert role.id == "executive:ceo"


def test_get_role_by_name_trims_whitespace():
    role = get_role_by_name("  CEO  ")
    assert role is not None


def test_get_role_by_name_unknown_returns_none():
    assert get_role_by_name("Definitely Not A Role") is None


def test_count_roles_matches_dept_role_map_lengths():
    counts = count_roles()
    for dept, role_ids in DEPT_ROLE_MAP.items():
        assert counts[dept] == len(role_ids)


def test_role_defaults_for_default_constructed_definition():
    role = RoleDefinition(id="x:y", name="Y", department_id="x", seniority="mid")
    assert role.responsibilities == []
    assert role.capabilities == []
    assert role.primary_model == "smart"
    assert role.fallback_model == "fast"
    assert role.allowed_tools == []
    assert role.budget_usd_per_task == 1.0
    assert role.max_task_duration_minutes == 60
    assert role.default_autonomy_level == 3
    assert role.risk_level == "medium"
    assert role.quality_threshold == 0.80


def test_finance_roles_have_high_quality_threshold():
    finance_roles = get_roles_for_dept("finance")
    assert all(r.quality_threshold >= 0.95 for r in finance_roles)


def test_security_roles_use_expert_model_and_high_risk():
    security_roles = get_roles_for_dept("security")
    assert all(r.primary_model == "expert" for r in security_roles)
    assert all(r.risk_level == "high" for r in security_roles)
