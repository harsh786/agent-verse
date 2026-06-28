"""Tests for policy version history and rollback (PolicyVersionManager)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.governance.policies import (
    Policy,
    PolicyEngine,
    PolicyResult,
    PolicyVersionManager,
    evaluate_with_domain_failsafe,
    REGULATED_DOMAINS,
)
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(
    tenant_id="tid-ver", plan=PlanTier.PROFESSIONAL, api_key_id="kid-ver"
)


# ---------------------------------------------------------------------------
# PolicyVersion ORM stub
# ---------------------------------------------------------------------------


class _FakePolicyVersion:
    """Minimal stand-in used where PolicyVersion ORM model is expected."""

    def __init__(self, **kwargs):
        # Ensure `id` always exists (set before any others so it can be overridden)
        self.id = uuid4().hex
        for k, v in kwargs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# PolicyVersionManager.create_policy
# ---------------------------------------------------------------------------


class TestCreatePolicy:
    @pytest.mark.asyncio
    async def test_create_returns_version_1(self) -> None:
        """create_policy inserts a PolicyVersion with version_number=1, is_active=True."""
        manager = PolicyVersionManager()

        db = AsyncMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()

        captured: list = []
        db.add = MagicMock(side_effect=captured.append)

        result = await manager.create_policy(
            db=db,
            tenant_id="tid-ver",
            name="no-large-transfers",
            rules=[{"type": "tool_call", "tool_name_pattern": "*transfer*", "action": "hitl"}],
            description="SOX controls",
            change_summary="Initial",
        )

        assert len(captured) == 1
        pv = captured[0]
        assert pv.version_number == 1
        assert pv.is_active is True
        assert pv.name == "no-large-transfers"
        assert result["version_number"] == 1

    @pytest.mark.asyncio
    async def test_create_generates_unique_policy_id(self) -> None:
        """Each call generates a distinct policy_id UUID."""
        manager = PolicyVersionManager()
        ids_seen: list[str] = []

        db = AsyncMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()

        def capture_add(obj: object) -> None:
            ids_seen.append(obj.policy_id)  # type: ignore[attr-defined]

        db.add = MagicMock(side_effect=capture_add)

        await manager.create_policy(db=db, tenant_id="t", name="p1", rules=[])
        await manager.create_policy(db=db, tenant_id="t", name="p2", rules=[])

        assert ids_seen[0] != ids_seen[1]


# ---------------------------------------------------------------------------
# PolicyVersionManager.update_policy
# ---------------------------------------------------------------------------


class TestUpdatePolicy:
    @pytest.mark.asyncio
    async def test_update_increments_version_number(self) -> None:
        """update_policy deactivates v1 and inserts v2.

        We mock db.execute to return a fake current version for the SELECT,
        a no-op for the UPDATE, and we capture what gets db.add()ed for the
        new version.
        """
        manager = PolicyVersionManager()

        # Fake 'current' version that db.execute(select...) returns
        current_pv = MagicMock()
        current_pv.id = uuid4().hex
        current_pv.version_number = 1
        current_pv.name = "original"
        current_pv.description = "Old desc"
        current_pv.rules = []
        current_pv.parent_policy_id = None
        current_pv.is_active = True

        db = AsyncMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()

        # First execute → SELECT returns current_pv
        # Second execute → UPDATE (no-op mock)
        select_result = MagicMock()
        select_result.scalar_one_or_none = MagicMock(return_value=current_pv)
        update_result = MagicMock()
        db.execute = AsyncMock(side_effect=[select_result, update_result])

        captured: list = []
        db.add = MagicMock(side_effect=captured.append)

        result = await manager.update_policy(
            db=db,
            tenant_id="tid-ver",
            policy_id=str(uuid4()),
            updates={"description": "New desc"},
            change_summary="Updated for compliance",
        )

        # One object should have been added (the new version)
        assert len(captured) == 1
        new_pv = captured[0]
        assert new_pv.version_number == 2
        assert new_pv.is_active is True
        assert new_pv.change_summary == "Updated for compliance"
        assert result["version_number"] == 2


# ---------------------------------------------------------------------------
# PolicyVersionManager.get_version_history
# ---------------------------------------------------------------------------


class TestGetVersionHistory:
    @pytest.mark.asyncio
    async def test_returns_all_versions_in_order(self) -> None:
        """get_version_history returns versions sorted ascending by version_number."""
        manager = PolicyVersionManager()
        policy_id = str(uuid4())

        # These are returned by result.scalars().all()
        v1 = _FakePolicyVersion(version_number=1, is_active=False)
        v2 = _FakePolicyVersion(version_number=2, is_active=False)
        v3 = _FakePolicyVersion(version_number=3, is_active=True)

        scalars_mock = MagicMock()
        scalars_mock.all = MagicMock(return_value=[v1, v2, v3])
        result_mock = MagicMock()
        result_mock.scalars = MagicMock(return_value=scalars_mock)

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        versions = await manager.get_version_history(db, "tid-ver", policy_id)

        assert len(versions) == 3
        assert versions[0].version_number == 1
        assert versions[2].is_active is True


# ---------------------------------------------------------------------------
# Timezone-aware time windows
# ---------------------------------------------------------------------------


class TestTimezoneAwarePolicyWindows:
    def test_policy_has_timezone_field(self) -> None:
        """Policy dataclass exposes timezone field."""
        policy = Policy(
            name="nyc-business-hours",
            timezone="America/New_York",
            allowed_hours_utc=(9, 17),
        )
        assert policy.timezone == "America/New_York"

    def test_default_timezone_is_utc(self) -> None:
        policy = Policy(name="default")
        assert policy.timezone == "UTC"

    def test_engine_evaluates_without_error(self) -> None:
        """PolicyEngine.evaluate completes when timezone field is set."""
        policy = Policy(
            name="business-hours",
            denied_tools=["*sensitive*"],
            allowed_hours_utc=(9, 17),
            timezone="UTC",
        )
        engine = PolicyEngine(policies=[policy])
        result = engine.evaluate("any_tool", tenant_ctx=_CTX)
        assert result in (
            PolicyResult.ALLOW,
            PolicyResult.DENY,
            PolicyResult.REQUIRE_APPROVAL,
        )


# ---------------------------------------------------------------------------
# Domain fail-closed
# ---------------------------------------------------------------------------


class TestDomainFailClosed:
    def test_regulated_domain_no_match_requires_approval(self) -> None:
        for domain in ("healthcare", "legal", "finance", "hipaa", "sox"):
            result = evaluate_with_domain_failsafe("send_email", domain=domain)
            assert result == PolicyResult.REQUIRE_APPROVAL, f"Expected REQUIRE_APPROVAL for {domain}"

    def test_unregulated_domain_no_match_allows(self) -> None:
        result = evaluate_with_domain_failsafe("send_email", domain="general")
        assert result == PolicyResult.ALLOW

    def test_none_domain_allows(self) -> None:
        result = evaluate_with_domain_failsafe("send_email", domain=None)
        assert result == PolicyResult.ALLOW

    def test_regulated_domains_set_is_not_empty(self) -> None:
        assert len(REGULATED_DOMAINS) > 0
        assert "healthcare" in REGULATED_DOMAINS
        assert "finance" in REGULATED_DOMAINS
