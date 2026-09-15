"""Phase 3 / Part B — dual-mode identity: unified principals across channels."""

from __future__ import annotations

from app.identity import IdentityService, Principal, PrincipalKind


async def test_first_contact_creates_principal_and_link() -> None:
    svc = IdentityService()
    p = await svc.resolve_principal(
        tenant_id="t1", channel="whatsapp", channel_user_id="+15551234"
    )
    assert isinstance(p, Principal)
    assert p.tenant_id == "t1" and p.kind == PrincipalKind.INDIVIDUAL
    # Same channel identity resolves to the SAME principal.
    p2 = await svc.resolve_principal(
        tenant_id="t1", channel="whatsapp", channel_user_id="+15551234"
    )
    assert p2.id == p.id


async def test_linking_another_channel_continues_same_principal() -> None:
    svc = IdentityService()
    web = await svc.resolve_principal(tenant_id="t1", channel="web", channel_user_id="user-1")
    # User attaches their WhatsApp number to the same principal.
    await svc.link_identity(
        tenant_id="t1", principal_id=web.id, channel="whatsapp", channel_user_id="+15551234"
    )
    # An inbound WhatsApp message now resolves to the web principal (cross-channel).
    same = await svc.resolve_principal(
        tenant_id="t1", channel="whatsapp", channel_user_id="+15551234"
    )
    assert same.id == web.id
    links = await svc.identities_for(web.id, "t1")
    assert {(x.channel, x.channel_user_id) for x in links} == {
        ("web", "user-1"),
        ("whatsapp", "+15551234"),
    }


async def test_identity_is_tenant_scoped() -> None:
    svc = IdentityService()
    p1 = await svc.resolve_principal(tenant_id="t1", channel="web", channel_user_id="u")
    p2 = await svc.resolve_principal(tenant_id="t2", channel="web", channel_user_id="u")
    # Same channel_user_id in different tenants → different principals.
    assert p1.id != p2.id
    assert await svc.get_principal(p1.id, "t2") is None


async def test_link_does_not_silently_repoint_existing_identity() -> None:
    svc = IdentityService()
    a = await svc.resolve_principal(tenant_id="t1", channel="whatsapp", channel_user_id="+1")
    b = await svc.resolve_principal(tenant_id="t1", channel="web", channel_user_id="other")
    # Trying to link +1 (already a's) to b returns the existing binding to a.
    link = await svc.link_identity(
        tenant_id="t1", principal_id=b.id, channel="whatsapp", channel_user_id="+1"
    )
    assert link.principal_id == a.id
