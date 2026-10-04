"""a08-F177-01: org_attachments on real Postgres (RLS, tenant predicate, retention)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.integration


async def _make_org(session_factory: Any, tenant_id: str) -> uuid.UUID:
    from app.db.rls import sqlalchemy_rls_context
    from app.org.models import Organization

    org_id = uuid.uuid4()
    async with session_factory() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
        s.add(
            Organization(
                id=org_id, tenant_id=uuid.UUID(tenant_id), name="Acme", slug=f"a-{org_id.hex}"
            )
        )
    return org_id


async def test_store_load_isolation_and_purge(test_backends: Any) -> None:
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context
    from app.db.session import get_session_factory, get_system_session_factory
    from app.org.attachments import (
        load_attachment,
        purge_expired_org_attachments,
        store_attachment,
    )

    factory = get_session_factory()
    t1, t2 = str(uuid.uuid4()), str(uuid.uuid4())
    org1 = await _make_org(factory, t1)

    async with factory() as s, s.begin(), sqlalchemy_rls_context(s, t1):
        rec = await store_attachment(
            s,
            tenant_id=t1,
            org_id=org1,
            filename="scan.pdf",
            content_type="application/pdf",
            content=b"%PDF-1.4 bytes",
        )
    att_id = rec["attachment_id"]
    assert rec["size"] == len(b"%PDF-1.4 bytes")

    blob = await load_attachment(t1, att_id)
    assert blob is not None
    assert blob.content == b"%PDF-1.4 bytes"
    assert blob.content_type == "application/pdf"
    # Another tenant can never read it.
    assert await load_attachment(t2, att_id) is None
    assert await load_attachment(t1, "not-a-uuid") is None

    # Expire it: reads ignore it and the purge deletes it.
    sysf = get_system_session_factory()
    async with sysf() as s, s.begin():
        from app.db.rls import system_session

        async with system_session(s):
            await s.execute(
                text("UPDATE org_attachments SET expires_at = now() - interval '1 second'")
            )
    assert await load_attachment(t1, att_id) is None
    assert await purge_expired_org_attachments(sysf) >= 1
    async with sysf() as s, s.begin():
        async with system_session(s):
            left = (await s.execute(text("SELECT count(*) FROM org_attachments"))).scalar()
    assert left == 0
