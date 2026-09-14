"""Grantex G5 — tamper-evident hash-chained audit."""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

from app.governance.audit_chain import AuditChain

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _chain() -> AuditChain:
    c = AuditChain(tenant_id="t1")
    for i in range(4):
        c.append({"action": "tool_call", "tool": f"jira.op{i}"}, at=_T0 + timedelta(minutes=i))
    return c


def test_intact_chain_verifies() -> None:
    c = _chain()
    ok, broken = c.verify()
    assert ok is True and broken is None
    # each record links to the previous
    for prev, rec in zip(c.records, c.records[1:], strict=False):
        assert rec.prev_hash == prev.record_hash


def test_tampering_a_middle_record_is_detected() -> None:
    c = _chain()
    # Mutate record 1's payload without recomputing hashes (a forger's edit).
    c.records[1] = dataclasses.replace(c.records[1], payload={"action": "hacked"})
    ok, broken = c.verify()
    assert ok is False
    assert broken == 1


def test_deleting_a_record_breaks_the_chain() -> None:
    c = _chain()
    del c.records[2]  # removal shifts hashes/seq
    ok, _ = c.verify()
    assert ok is False


def test_export_evidence_pack_is_self_verifying() -> None:
    c = _chain()
    pack = c.export_evidence_pack()
    assert pack["verified"] is True
    assert pack["count"] == 4
    assert pack["head_hash"] == c.records[-1].record_hash
    assert len(pack["records"]) == 4
