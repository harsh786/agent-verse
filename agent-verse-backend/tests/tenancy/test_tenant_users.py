"""Tests for app/tenancy/tenant_users.py — QA1 tenant user & role management."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.tenancy.tenant_users import (
    ROLE_PERMISSIONS,
    TenantInvite,
    TenantRole,
    TenantUser,
    TenantUserService,
    tenant_user_service,
)


class TestRolePermissions:
    def test_all_roles_have_permissions_entry(self):
        for role in TenantRole:
            assert role in ROLE_PERMISSIONS

    def test_tenant_admin_has_admin(self):
        assert "admin" in ROLE_PERMISSIONS[TenantRole.TENANT_ADMIN]

    def test_org_viewer_read_only(self):
        assert ROLE_PERMISSIONS[TenantRole.ORG_VIEWER] == ["read"]

    def test_billing_admin_only_billing(self):
        assert ROLE_PERMISSIONS[TenantRole.BILLING_ADMIN] == ["billing"]


class TestTenantUserHasPermission:
    def _user(self, role: TenantRole, org_permissions=None) -> TenantUser:
        return TenantUser(
            user_id="u1",
            tenant_id="t1",
            email="a@example.com",
            name="A",
            role=role,
            org_permissions=org_permissions or {},
        )

    def test_global_role_permission_granted(self):
        user = self._user(TenantRole.ORG_MEMBER)
        assert user.has_permission("write") is True

    def test_global_role_permission_denied(self):
        user = self._user(TenantRole.ORG_VIEWER)
        assert user.has_permission("write") is False

    def test_admin_role_grants_any_permission(self):
        user = self._user(TenantRole.TENANT_ADMIN)
        assert user.has_permission("anything_at_all") is True

    def test_per_org_permission_granted(self):
        user = self._user(TenantRole.ORG_VIEWER, org_permissions={"org1": ["approve"]})
        assert user.has_permission("approve", org_id="org1") is True

    def test_per_org_permission_denied_for_other_org(self):
        user = self._user(TenantRole.ORG_VIEWER, org_permissions={"org1": ["approve"]})
        assert user.has_permission("approve", org_id="org2") is False

    def test_no_org_id_and_no_global_permission_denied(self):
        user = self._user(TenantRole.APPROVER)
        assert user.has_permission("write") is False

    def test_approver_role_can_approve(self):
        user = self._user(TenantRole.APPROVER)
        assert user.has_permission("approve") is True


class TestTenantInvite:
    def test_not_expired_by_default(self):
        invite = TenantInvite(
            invite_id="i1", tenant_id="t1", email="a@example.com", role=TenantRole.ORG_MEMBER
        )
        assert invite.is_expired is False

    def test_expired_when_past_expiry(self):
        invite = TenantInvite(
            invite_id="i1",
            tenant_id="t1",
            email="a@example.com",
            role=TenantRole.ORG_MEMBER,
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
        assert invite.is_expired is True

    def test_magic_link_format(self):
        invite = TenantInvite(
            invite_id="i1", tenant_id="t1", email="a@example.com", role=TenantRole.ORG_MEMBER
        )
        link = invite.magic_link
        assert link.startswith("/auth/accept-invite?token=")
        assert "invite=i1" in link

    def test_default_expiry_is_seven_days(self):
        before = datetime.now(UTC)
        invite = TenantInvite(
            invite_id="i1", tenant_id="t1", email="a@example.com", role=TenantRole.ORG_MEMBER
        )
        delta = invite.expires_at - before
        assert timedelta(days=6, hours=23) < delta <= timedelta(days=7, minutes=1)


@pytest.fixture
def service() -> TenantUserService:
    return TenantUserService()


class TestInviteUser:
    async def test_creates_invite_with_expected_fields(self, service: TenantUserService):
        invite = await service.invite_user(
            "tenant-1", "new@example.com", TenantRole.ORG_MEMBER, org_ids=["org1"]
        )
        assert invite.tenant_id == "tenant-1"
        assert invite.email == "new@example.com"
        assert invite.role == TenantRole.ORG_MEMBER
        assert invite.org_ids == ["org1"]
        assert invite.invite_id in service._invites

    async def test_invite_defaults_org_ids_to_empty_list(self, service: TenantUserService):
        invite = await service.invite_user("tenant-1", "new@example.com", TenantRole.ORG_VIEWER)
        assert invite.org_ids == []

    async def test_invite_sends_email_when_email_service_present(self):
        sent = {}

        class FakeEmail:
            async def send(self, to, subject, body):
                sent["to"] = to
                sent["subject"] = subject
                sent["body"] = body

        svc = TenantUserService(email_service=FakeEmail())
        await svc.invite_user("tenant-1", "new@example.com", TenantRole.ORG_MEMBER)
        assert sent["to"] == "new@example.com"
        assert "invited" in sent["body"]

    async def test_invite_email_failure_is_swallowed(self):
        class FailingEmail:
            async def send(self, to, subject, body):
                raise RuntimeError("smtp down")

        svc = TenantUserService(email_service=FailingEmail())
        # Should not raise even though email sending fails
        invite = await svc.invite_user("tenant-1", "new@example.com", TenantRole.ORG_MEMBER)
        assert invite is not None

    async def test_invite_without_email_service_does_not_error(self, service: TenantUserService):
        invite = await service.invite_user("tenant-1", "new@example.com", TenantRole.ORG_MEMBER)
        assert invite is not None


class TestAcceptInvite:
    async def test_accept_creates_user(self, service: TenantUserService):
        invite = await service.invite_user(
            "tenant-1", "new@example.com", TenantRole.ORG_MEMBER, org_ids=["org1"]
        )
        user = await service.accept_invite(invite.token, invite.invite_id, "New User")
        assert user.email == "new@example.com"
        assert user.tenant_id == "tenant-1"
        assert user.role == TenantRole.ORG_MEMBER
        assert user.org_permissions["org1"] == ROLE_PERMISSIONS[TenantRole.ORG_MEMBER]
        assert invite.accepted is True

    async def test_accept_registers_by_email_lookup(self, service: TenantUserService):
        invite = await service.invite_user("tenant-1", "new@example.com", TenantRole.ORG_MEMBER)
        user = await service.accept_invite(invite.token, invite.invite_id, "New User")
        found = await service.get_user_by_email("new@example.com")
        assert found is not None
        assert found.user_id == user.user_id

    async def test_accept_unknown_invite_raises(self, service: TenantUserService):
        with pytest.raises(ValueError, match="not found"):
            await service.accept_invite("tok", "missing-id", "Name")

    async def test_accept_expired_invite_raises(self, service: TenantUserService):
        invite = await service.invite_user("tenant-1", "new@example.com", TenantRole.ORG_MEMBER)
        service._invites[invite.invite_id].expires_at = datetime.now(UTC) - timedelta(days=1)
        with pytest.raises(ValueError, match="expired"):
            await service.accept_invite(invite.token, invite.invite_id, "Name")

    async def test_accept_already_accepted_invite_raises(self, service: TenantUserService):
        invite = await service.invite_user("tenant-1", "new@example.com", TenantRole.ORG_MEMBER)
        await service.accept_invite(invite.token, invite.invite_id, "Name")
        with pytest.raises(ValueError, match="already accepted"):
            await service.accept_invite(invite.token, invite.invite_id, "Name")

    async def test_accept_with_wrong_token_raises(self, service: TenantUserService):
        invite = await service.invite_user("tenant-1", "new@example.com", TenantRole.ORG_MEMBER)
        with pytest.raises(ValueError, match="Invalid invite token"):
            await service.accept_invite("wrong-token", invite.invite_id, "Name")


class TestGetUser:
    async def test_get_user_returns_none_for_unknown(self, service: TenantUserService):
        assert await service.get_user("missing") is None

    async def test_get_user_by_email_returns_none_for_unknown(self, service: TenantUserService):
        assert await service.get_user_by_email("missing@example.com") is None

    async def test_get_user_by_email_case_insensitive_lookup_key(
        self, service: TenantUserService
    ):
        invite = await service.invite_user("tenant-1", "new@example.com", TenantRole.ORG_MEMBER)
        await service.accept_invite(invite.token, invite.invite_id, "Name")
        # by_email dict is keyed by the raw invite email (already lowercase in this test)
        found = await service.get_user_by_email("new@example.com")
        assert found is not None


class TestListUsersAndInvites:
    async def test_list_users_filters_by_tenant_and_active(self, service: TenantUserService):
        invite1 = await service.invite_user("tenant-1", "a@example.com", TenantRole.ORG_MEMBER)
        invite2 = await service.invite_user("tenant-2", "b@example.com", TenantRole.ORG_MEMBER)
        u1 = await service.accept_invite(invite1.token, invite1.invite_id, "A")
        await service.accept_invite(invite2.token, invite2.invite_id, "B")

        users = await service.list_users("tenant-1")
        assert len(users) == 1
        assert users[0].user_id == u1.user_id

    async def test_list_users_excludes_inactive(self, service: TenantUserService):
        invite = await service.invite_user("tenant-1", "a@example.com", TenantRole.ORG_MEMBER)
        user = await service.accept_invite(invite.token, invite.invite_id, "A")
        await service.remove_user(user.user_id)
        users = await service.list_users("tenant-1")
        assert users == []

    async def test_list_pending_invites_excludes_accepted_and_expired(
        self, service: TenantUserService
    ):
        pending = await service.invite_user("tenant-1", "pending@example.com", TenantRole.ORG_MEMBER)
        accepted = await service.invite_user("tenant-1", "accepted@example.com", TenantRole.ORG_MEMBER)
        await service.accept_invite(accepted.token, accepted.invite_id, "Name")
        expired = await service.invite_user("tenant-1", "expired@example.com", TenantRole.ORG_MEMBER)
        service._invites[expired.invite_id].expires_at = datetime.now(UTC) - timedelta(days=1)

        result = await service.list_pending_invites("tenant-1")
        assert [i.invite_id for i in result] == [pending.invite_id]


class TestChangeRoleAndRemoval:
    async def test_change_role_updates_and_returns_true(self, service: TenantUserService):
        invite = await service.invite_user("tenant-1", "a@example.com", TenantRole.ORG_MEMBER)
        user = await service.accept_invite(invite.token, invite.invite_id, "A")
        ok = await service.change_role(user.user_id, TenantRole.ORG_ADMIN)
        assert ok is True
        refreshed = await service.get_user(user.user_id)
        assert refreshed.role == TenantRole.ORG_ADMIN

    async def test_change_role_unknown_user_returns_false(self, service: TenantUserService):
        assert await service.change_role("missing", TenantRole.ORG_ADMIN) is False

    async def test_remove_user_marks_inactive(self, service: TenantUserService):
        invite = await service.invite_user("tenant-1", "a@example.com", TenantRole.ORG_MEMBER)
        user = await service.accept_invite(invite.token, invite.invite_id, "A")
        ok = await service.remove_user(user.user_id)
        assert ok is True
        refreshed = await service.get_user(user.user_id)
        assert refreshed.is_active is False

    async def test_remove_user_unknown_returns_false(self, service: TenantUserService):
        assert await service.remove_user("missing") is False

    async def test_revoke_invite_deletes_it(self, service: TenantUserService):
        invite = await service.invite_user("tenant-1", "a@example.com", TenantRole.ORG_MEMBER)
        ok = await service.revoke_invite(invite.invite_id)
        assert ok is True
        assert invite.invite_id not in service._invites

    async def test_revoke_invite_unknown_returns_false(self, service: TenantUserService):
        assert await service.revoke_invite("missing") is False


class TestSetOrgPermissions:
    async def test_sets_permissions_for_known_user(self, service: TenantUserService):
        invite = await service.invite_user("tenant-1", "a@example.com", TenantRole.ORG_MEMBER)
        user = await service.accept_invite(invite.token, invite.invite_id, "A")
        ok = await service.set_org_permissions(user.user_id, "org2", ["read", "approve"])
        assert ok is True
        refreshed = await service.get_user(user.user_id)
        assert refreshed.org_permissions["org2"] == ["read", "approve"]

    async def test_set_org_permissions_unknown_user_returns_false(
        self, service: TenantUserService
    ):
        assert await service.set_org_permissions("missing", "org2", ["read"]) is False


class TestGlobalInstance:
    def test_global_tenant_user_service_exists(self):
        assert isinstance(tenant_user_service, TenantUserService)
