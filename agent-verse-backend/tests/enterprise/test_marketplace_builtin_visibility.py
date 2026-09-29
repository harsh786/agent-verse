"""Built-in (system) marketplace templates must be visible to every tenant.

``seed_builtins`` publishes the catalogue as the ``system`` tenant with
``run_security_review=False``, which used to stamp every built-in
``review_status='unreviewed'`` — and cross-tenant visibility requires
``'approved'``. So after seeding, no tenant other than ``system`` could see or
install a single built-in, in DB mode and in memory alike.
"""

from __future__ import annotations

import pytest

from app.enterprise.marketplace_v2 import (
    _VISIBLE_SQL,
    MarketplaceV2,
    _visible_to,
)
from app.tenancy.context import PlanTier, TenantContext

T_OTHER = TenantContext(tenant_id="tenant-other", plan=PlanTier.STARTER, api_key_id="k")


@pytest.mark.asyncio
async def test_seeded_builtins_are_visible_to_other_tenants_in_memory() -> None:
    svc = MarketplaceV2(db_factory=None)
    seeded = await svc.seed_builtins()
    assert seeded > 0

    tpl = await svc.get_template(template_id="tpl-bug-fix", tenant_id=T_OTHER.tenant_id)
    assert tpl is not None
    assert tpl["review_status"] == "approved"
    assert tpl["is_builtin"] is True

    listed = await svc.list_templates(tenant_id=T_OTHER.tenant_id, page_size=1000)
    ids = {t["id"] for t in listed["templates"]}
    assert "tpl-bug-fix" in ids
    assert listed["total"] >= seeded


@pytest.mark.asyncio
async def test_seeded_builtin_is_installable_by_other_tenant_in_memory() -> None:
    svc = MarketplaceV2(db_factory=None)
    await svc.seed_builtins()
    result = await svc.install(
        template_id="tpl-bug-fix", params={"repo": "acme/api"}, tenant_ctx=T_OTHER
    )
    assert result["success"] is True, result


def test_visibility_rule_treats_system_builtins_as_approved() -> None:
    # A row seeded before the fix (still 'unreviewed' in the DB) is a system
    # built-in; the rule must not hide it from other tenants.
    row = {
        "tenant_id": "system",
        "is_builtin": True,
        "visibility": "public",
        "review_status": "unreviewed",
    }
    assert _visible_to(row, "tenant-other") is True
    assert "tenant_id = 'system' AND is_builtin" in _VISIBLE_SQL


def test_visibility_rule_does_not_approve_tenant_builtin_claims() -> None:
    # Only the system tenant's built-ins get the pass — a tenant row that
    # claims is_builtin is still gated on security review.
    row = {
        "tenant_id": "tenant-a",
        "is_builtin": True,
        "visibility": "public",
        "review_status": "unreviewed",
    }
    assert _visible_to(row, "tenant-other") is False


@pytest.mark.asyncio
async def test_tenant_publish_cannot_claim_builtin() -> None:
    svc = MarketplaceV2(db_factory=None)
    ctx = TenantContext(tenant_id="tenant-a", plan=PlanTier.STARTER, api_key_id="k")
    record = await svc.publish_template(
        data={"name": "x", "slug": "x-claim", "is_builtin": True, "is_verified": True},
        tenant_ctx=ctx,
        run_security_review=False,
    )
    assert record["is_builtin"] is False
    assert record["is_verified"] is False
    assert record["review_status"] == "unreviewed"
