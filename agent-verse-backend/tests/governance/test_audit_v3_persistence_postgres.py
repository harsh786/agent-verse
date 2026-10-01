"""CHAIN-01 / CHAIN-04: AuditV3 persists to the migrated schema and verifies there.

``AuditV3.append`` inserted columns ``audit_events`` does not have (and omitted
its NOT NULL ``event_type``), then swallowed the error — every record, including
the GDPR deletion audit, was lost behind a warning, and the only tests used a
fake session. It now appends to the durable ``audit_chain`` hash chain
(``PersistentAuditChain``: per-tenant seq, race-safe across replicas) and raises
when the record cannot be stored; ``averify_chain`` verifies the stored chain.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.governance.audit_v3 import AuditV3


class _DownSession:
    async def __aenter__(self) -> Any:
        raise ConnectionError("db down")

    async def __aexit__(self, *a: object) -> None:
        return None


async def test_append_raises_when_the_record_cannot_be_stored() -> None:
    audit = AuditV3(db_factory=lambda: _DownSession())
    with pytest.raises(ConnectionError):
        await audit.append(tenant_id="t", goal_id="g", action="data_subject_deletion")


@pytest.mark.integration
async def test_append_persists_and_verifies_under_app_role(pg_url: str) -> None:
    from tests.memory._pg import app_role_engine, sessionmaker_for

    tenant = f"t-v3-{uuid.uuid4().hex[:8]}"
    engine = await app_role_engine(pg_url, ["audit_chain"])
    try:
        factory = sessionmaker_for(engine)
        writer_a = AuditV3(db_factory=factory)
        writer_b = AuditV3(db_factory=factory)  # a second replica: same chain, no fork
        r1 = await writer_a.append(
            tenant_id=tenant,
            goal_id="subj-1",
            action="data_subject_deletion",
            metadata={"total_deleted": 3},
        )
        r2 = await writer_b.append(
            tenant_id=tenant,
            goal_id="g-2",
            action="tool_call",
            tool_name="github",
            tool_args={"repo": "x"},
        )
        assert (r1.sequence, r2.sequence) == (0, 1)
        assert r2.previous_hash == r1.entry_hash

        result = await AuditV3(db_factory=factory).averify_chain(tenant)
        assert result["valid"] is True
        assert result["records_checked"] == 2

        admin = create_async_engine(pg_url)
        async with admin.connect() as conn:
            payload = (
                await conn.execute(
                    text("SELECT payload FROM audit_chain WHERE tenant_id = :t AND seq = 0"),
                    {"t": tenant},
                )
            ).scalar_one()
        assert payload["action"] == "data_subject_deletion"
        assert payload["goal_id"] == "subj-1"
        async with admin.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE audit_chain SET payload = jsonb_set(payload, '{action}', "
                    "'\"tool_call\"') WHERE tenant_id = :t AND seq = 0"
                ),
                {"t": tenant},
            )
        await admin.dispose()
        tampered = await AuditV3(db_factory=factory).averify_chain(tenant)
        assert tampered["valid"] is False
        assert tampered["broken_at"] == 0
    finally:
        await engine.dispose()
