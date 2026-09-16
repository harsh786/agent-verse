"""Tests for app/tenancy/sub_tenants.py — QA4 sub-tenant (enterprise hierarchy)."""
from __future__ import annotations

import pytest

from app.tenancy.sub_tenants import (
    SubTenant,
    SubTenantService,
    TenantHierarchy,
    TenantHierarchyNode,
    sub_tenant_service,
)


@pytest.fixture
def service() -> SubTenantService:
    return SubTenantService()


class TestCreate:
    async def test_create_returns_sub_tenant_with_expected_fields(
        self, service: SubTenantService
    ):
        sub = await service.create("parent-1", "EMEA Division")
        assert sub.parent_tenant_id == "parent-1"
        assert sub.name == "EMEA Division"
        assert sub.slug == "emea-division"
        assert sub.status == "active"
        assert sub.max_agents == 20
        assert sub.max_orgs == 3
        assert sub.budget_allocation_usd == 0.0
        assert sub.allowed_channels == []
        assert sub.data_residency_region == "us"

    async def test_create_slugifies_underscores_and_spaces(self, service: SubTenantService):
        sub = await service.create("parent-1", "APAC Trading_Org")
        assert sub.slug == "apac-trading-org"

    async def test_create_slug_truncated_to_64_chars(self, service: SubTenantService):
        long_name = "A" * 100
        sub = await service.create("parent-1", long_name)
        assert len(sub.slug) == 64

    async def test_create_with_custom_fields(self, service: SubTenantService):
        sub = await service.create(
            "parent-1",
            "APAC Division",
            budget_allocation_usd=5000.0,
            max_agents=50,
            max_orgs=10,
            allowed_channels=["slack", "email"],
            data_residency_region="ap",
        )
        assert sub.budget_allocation_usd == 5000.0
        assert sub.max_agents == 50
        assert sub.max_orgs == 10
        assert sub.allowed_channels == ["slack", "email"]
        assert sub.data_residency_region == "ap"

    async def test_create_stores_and_indexes_by_parent(self, service: SubTenantService):
        sub = await service.create("parent-1", "Sub A")
        assert sub.sub_tenant_id in service._store
        assert sub.sub_tenant_id in service._by_parent["parent-1"]

    async def test_create_generates_unique_ids(self, service: SubTenantService):
        sub1 = await service.create("parent-1", "Sub A")
        sub2 = await service.create("parent-1", "Sub B")
        assert sub1.sub_tenant_id != sub2.sub_tenant_id


class TestListByParent:
    async def test_lists_only_children_of_given_parent(self, service: SubTenantService):
        sub1 = await service.create("parent-1", "Sub A")
        await service.create("parent-2", "Sub B")
        result = await service.list_by_parent("parent-1")
        assert [s.sub_tenant_id for s in result] == [sub1.sub_tenant_id]

    async def test_list_by_parent_returns_empty_for_unknown_parent(
        self, service: SubTenantService
    ):
        assert await service.list_by_parent("nonexistent") == []

    async def test_list_by_parent_multiple_children(self, service: SubTenantService):
        s1 = await service.create("parent-1", "Sub A")
        s2 = await service.create("parent-1", "Sub B")
        result = await service.list_by_parent("parent-1")
        ids = {s.sub_tenant_id for s in result}
        assert ids == {s1.sub_tenant_id, s2.sub_tenant_id}


class TestGet:
    async def test_get_returns_created_sub_tenant(self, service: SubTenantService):
        sub = await service.create("parent-1", "Sub A")
        found = await service.get(sub.sub_tenant_id)
        assert found is sub

    async def test_get_returns_none_for_unknown_id(self, service: SubTenantService):
        assert await service.get("missing") is None


class TestUpdateBudget:
    async def test_update_budget_changes_value_and_returns_true(
        self, service: SubTenantService
    ):
        sub = await service.create("parent-1", "Sub A", budget_allocation_usd=1000.0)
        original_updated_at = sub.updated_at
        ok = await service.update_budget(sub.sub_tenant_id, 2500.0)
        assert ok is True
        assert sub.budget_allocation_usd == 2500.0
        assert sub.updated_at >= original_updated_at

    async def test_update_budget_unknown_id_returns_false(self, service: SubTenantService):
        assert await service.update_budget("missing", 100.0) is False


class TestGetHierarchy:
    async def test_hierarchy_for_parent_with_no_children(self, service: SubTenantService):
        hierarchy = await service.get_hierarchy("parent-1")
        assert isinstance(hierarchy, TenantHierarchy)
        assert hierarchy.tenant_id == "parent-1"
        assert hierarchy.sub_tenants == []

    async def test_hierarchy_includes_all_children_as_nodes(self, service: SubTenantService):
        sub1 = await service.create("parent-1", "Sub A")
        sub2 = await service.create("parent-1", "Sub B")
        hierarchy = await service.get_hierarchy("parent-1")
        assert len(hierarchy.sub_tenants) == 2
        assert all(isinstance(n, TenantHierarchyNode) for n in hierarchy.sub_tenants)
        ids = {n.sub_tenant.sub_tenant_id for n in hierarchy.sub_tenants}
        assert ids == {sub1.sub_tenant_id, sub2.sub_tenant_id}

    async def test_hierarchy_node_default_counts_zero(self, service: SubTenantService):
        await service.create("parent-1", "Sub A")
        hierarchy = await service.get_hierarchy("parent-1")
        node = hierarchy.sub_tenants[0]
        assert node.org_count == 0
        assert node.agent_count == 0


class TestDeactivate:
    async def test_deactivate_sets_status_inactive(self, service: SubTenantService):
        sub = await service.create("parent-1", "Sub A")
        ok = await service.deactivate(sub.sub_tenant_id)
        assert ok is True
        assert sub.status == "inactive"

    async def test_deactivate_unknown_id_returns_false(self, service: SubTenantService):
        assert await service.deactivate("missing") is False


class TestSubTenantDataclassDefaults:
    def test_defaults(self):
        sub = SubTenant(
            sub_tenant_id="s1",
            parent_tenant_id="p1",
            name="Test",
            slug="test",
        )
        assert sub.description == ""
        assert sub.budget_allocation_usd == 0.0
        assert sub.max_agents == 20
        assert sub.max_orgs == 3
        assert sub.allowed_channels == []
        assert sub.data_residency_region == "us"
        assert sub.status == "active"


class TestGlobalInstance:
    def test_global_sub_tenant_service_exists(self):
        assert isinstance(sub_tenant_service, SubTenantService)
