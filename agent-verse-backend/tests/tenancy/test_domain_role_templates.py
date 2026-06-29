"""Comprehensive tests for domain_role_templates.py — all domains, role fields."""
from __future__ import annotations

import pytest

from app.tenancy.domain_role_templates import DOMAIN_ROLE_TEMPLATES


# ── 1. Top-level structure ────────────────────────────────────────────────────

def test_all_expected_domains_present():
    expected = {"healthcare", "legal", "finance", "education", "ecommerce"}
    assert set(DOMAIN_ROLE_TEMPLATES.keys()) == expected


def test_all_domains_have_roles():
    for domain, roles in DOMAIN_ROLE_TEMPLATES.items():
        assert len(roles) > 0, f"Domain '{domain}' has no roles"


def test_all_roles_are_dicts():
    for domain, roles in DOMAIN_ROLE_TEMPLATES.items():
        for role in roles:
            assert isinstance(role, dict), f"Role in '{domain}' is not a dict"


# ── 2. Required fields on every role ─────────────────────────────────────────

def test_all_roles_have_name():
    for domain, roles in DOMAIN_ROLE_TEMPLATES.items():
        for role in roles:
            assert "name" in role, f"Role in '{domain}' missing 'name'"
            assert isinstance(role["name"], str)
            assert len(role["name"]) > 0


def test_all_roles_have_display_name():
    for domain, roles in DOMAIN_ROLE_TEMPLATES.items():
        for role in roles:
            assert "display_name" in role, f"Role in '{domain}' missing 'display_name'"
            assert isinstance(role["display_name"], str)


def test_all_roles_have_description():
    for domain, roles in DOMAIN_ROLE_TEMPLATES.items():
        for role in roles:
            assert "description" in role, f"Role in '{domain}' missing 'description'"
            assert len(role["description"]) > 0


def test_all_roles_have_permissions():
    for domain, roles in DOMAIN_ROLE_TEMPLATES.items():
        for role in roles:
            assert "permissions" in role, f"Role in '{domain}' missing 'permissions'"
            assert isinstance(role["permissions"], list)
            assert len(role["permissions"]) > 0, f"Role '{role['name']}' in '{domain}' has empty permissions"


def test_permissions_are_strings():
    for domain, roles in DOMAIN_ROLE_TEMPLATES.items():
        for role in roles:
            for perm in role["permissions"]:
                assert isinstance(perm, str), f"Permission '{perm}' in role '{role['name']}' is not a string"


def test_permissions_follow_colon_format():
    """All permissions should follow resource:action format."""
    for domain, roles in DOMAIN_ROLE_TEMPLATES.items():
        for role in roles:
            for perm in role["permissions"]:
                parts = perm.split(":")
                assert len(parts) == 2, f"Permission '{perm}' in '{domain}:{role['name']}' doesn't follow resource:action format"


# ── 3. Healthcare domain ─────────────────────────────────────────────────────

def test_healthcare_role_names():
    names = {r["name"] for r in DOMAIN_ROLE_TEMPLATES["healthcare"]}
    assert "phi_reader" in names
    assert "prescribing_physician" in names
    assert "care_coordinator" in names
    assert "hipaa_compliance_officer" in names


def test_phi_reader_permissions():
    roles = {r["name"]: r for r in DOMAIN_ROLE_TEMPLATES["healthcare"]}
    phi = roles["phi_reader"]
    assert "goals:read" in phi["permissions"]
    assert "knowledge:read" in phi["permissions"]
    # Read-only — no write
    assert "goals:write" not in phi["permissions"]


def test_prescribing_physician_has_approve():
    roles = {r["name"]: r for r in DOMAIN_ROLE_TEMPLATES["healthcare"]}
    doc = roles["prescribing_physician"]
    assert "governance:approve" in doc["permissions"]
    assert "goals:execute" in doc["permissions"]


def test_healthcare_conditions_present():
    roles = {r["name"]: r for r in DOMAIN_ROLE_TEMPLATES["healthcare"]}
    assert "conditions" in roles["phi_reader"]
    assert "conditions" in roles["prescribing_physician"]
    assert "conditions" in roles["care_coordinator"]


def test_hipaa_officer_no_conditions():
    roles = {r["name"]: r for r in DOMAIN_ROLE_TEMPLATES["healthcare"]}
    hipaa = roles["hipaa_compliance_officer"]
    # Officer has audit permissions
    assert "audit:read" in hipaa["permissions"]
    assert "audit:export" in hipaa["permissions"]


# ── 4. Legal domain ───────────────────────────────────────────────────────────

def test_legal_role_names():
    names = {r["name"] for r in DOMAIN_ROLE_TEMPLATES["legal"]}
    assert "paralegal" in names
    assert "associate_attorney" in names
    assert "senior_partner" in names
    assert "client_portal" in names


def test_senior_partner_full_access():
    roles = {r["name"]: r for r in DOMAIN_ROLE_TEMPLATES["legal"]}
    partner = roles["senior_partner"]
    perms = set(partner["permissions"])
    assert "goals:delete" in perms
    assert "costs:admin" in perms
    assert "governance:approve" in perms


def test_client_portal_limited_access():
    roles = {r["name"]: r for r in DOMAIN_ROLE_TEMPLATES["legal"]}
    client = roles["client_portal"]
    assert client["permissions"] == ["goals:read"]


def test_associate_attorney_conditions():
    roles = {r["name"]: r for r in DOMAIN_ROLE_TEMPLATES["legal"]}
    attorney = roles["associate_attorney"]
    assert "conditions" in attorney
    assert attorney["conditions"]["supervisor_approval_over_usd"] == 50000


# ── 5. Finance domain ────────────────────────────────────────────────────────

def test_finance_role_names():
    names = {r["name"] for r in DOMAIN_ROLE_TEMPLATES["finance"]}
    assert "analyst" in names
    assert "trader" in names
    assert "risk_officer" in names
    assert "sox_compliance_manager" in names


def test_trader_time_window_condition():
    roles = {r["name"]: r for r in DOMAIN_ROLE_TEMPLATES["finance"]}
    trader = roles["trader"]
    assert "conditions" in trader
    cond = trader["conditions"]
    assert "time_window" in cond
    assert cond["time_window"]["start"] == "09:30"
    assert cond["time_window"]["end"] == "16:00"
    assert cond["time_window"]["tz"] == "America/New_York"


def test_analyst_read_only():
    roles = {r["name"]: r for r in DOMAIN_ROLE_TEMPLATES["finance"]}
    analyst = roles["analyst"]
    for perm in analyst["permissions"]:
        assert ":write" not in perm
        assert ":execute" not in perm
        assert ":delete" not in perm
        assert ":admin" not in perm


def test_risk_officer_can_delete_and_admin():
    roles = {r["name"]: r for r in DOMAIN_ROLE_TEMPLATES["finance"]}
    officer = roles["risk_officer"]
    perms = set(officer["permissions"])
    assert "goals:delete" in perms
    assert "costs:admin" in perms


# ── 6. Education domain ───────────────────────────────────────────────────────

def test_education_role_names():
    names = {r["name"] for r in DOMAIN_ROLE_TEMPLATES["education"]}
    assert "student" in names
    assert "instructor" in names
    assert "institution_admin" in names


def test_student_limited_own_goals():
    roles = {r["name"]: r for r in DOMAIN_ROLE_TEMPLATES["education"]}
    student = roles["student"]
    assert "goals:read" in student["permissions"]
    assert "goals:execute" in student["permissions"]
    assert "conditions" in student
    assert student["conditions"]["ownership"] == "creator"


def test_institution_admin_full_access():
    roles = {r["name"]: r for r in DOMAIN_ROLE_TEMPLATES["education"]}
    admin = roles["institution_admin"]
    perms = set(admin["permissions"])
    assert "tenancy:write" in perms
    assert "costs:admin" in perms


# ── 7. E-commerce domain ─────────────────────────────────────────────────────

def test_ecommerce_role_names():
    names = {r["name"] for r in DOMAIN_ROLE_TEMPLATES["ecommerce"]}
    assert "catalog_manager" in names
    assert "customer_success" in names
    assert "operations_lead" in names


def test_operations_lead_no_costs_admin():
    roles = {r["name"]: r for r in DOMAIN_ROLE_TEMPLATES["ecommerce"]}
    lead = roles["operations_lead"]
    assert "costs:admin" not in lead["permissions"]
    assert "costs:read" in lead["permissions"]


def test_customer_success_has_department_condition():
    roles = {r["name"]: r for r in DOMAIN_ROLE_TEMPLATES["ecommerce"]}
    cs = roles["customer_success"]
    assert "conditions" in cs
    assert cs["conditions"]["department_match"] is True


# ── 8. Unique role names within each domain ───────────────────────────────────

def test_role_names_unique_within_domain():
    for domain, roles in DOMAIN_ROLE_TEMPLATES.items():
        names = [r["name"] for r in roles]
        assert len(names) == len(set(names)), f"Duplicate role names in '{domain}'"


# ── 9. Total role count sanity ────────────────────────────────────────────────

def test_total_roles_count():
    total = sum(len(roles) for roles in DOMAIN_ROLE_TEMPLATES.values())
    assert total >= 15, f"Expected at least 15 roles, got {total}"
