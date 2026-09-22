"""PersistentAuditChain (app.governance.audit_chain_store) — Postgres-backed
tamper-evident hash chain. A real Postgres container is unavailable in this
environment, so the SQLAlchemy AsyncSession is mocked to exercise the seq/
prev-hash bookkeeping and tamper-detection logic directly.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.governance.audit_chain import _GENESIS, compute_hash
from app.governance.audit_chain_store import PersistentAuditChain


class _Result:
    def __init__(self, one_or_none_val=None, all_val=None):
        self._one_or_none = one_or_none_val
        self._all = all_val or []

    def one_or_none(self):
        return self._one_or_none

    def all(self):
        return self._all


def _fake_session_factory(execute_side_effect):
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=session)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)
    session.execute = AsyncMock(side_effect=execute_side_effect)

    def factory():
        return session

    return factory, session


# ── append ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_append_first_record_uses_genesis_and_seq_zero():
    captured = {}

    async def fake_execute(query, params=None):
        sql = str(query)
        if "SELECT seq, record_hash" in sql:
            return _Result(one_or_none_val=None)
        if "INSERT INTO audit_chain" in sql:
            captured["params"] = params
        return _Result()

    factory, _session = _fake_session_factory(fake_execute)
    chain = PersistentAuditChain(factory)

    result = await chain.append("t1", {"action": "login"})

    assert result["seq"] == 0
    assert result["prev_hash"] == _GENESIS
    assert captured["params"]["seq"] == 0
    assert captured["params"]["prev"] == _GENESIS
    assert captured["params"]["t"] == "t1"
    expected_hash = compute_hash(
        _GENESIS, {"action": "login"}, seq=0, at=result["at"]
    )
    assert result["record_hash"] == expected_hash
    assert captured["params"]["rh"] == expected_hash


@pytest.mark.asyncio
async def test_append_second_record_increments_seq_and_chains_prev_hash():
    prior_hash = "a" * 64

    async def fake_execute(query, params=None):
        sql = str(query)
        if "SELECT seq, record_hash" in sql:
            return _Result(one_or_none_val=(5, prior_hash))
        return _Result()

    factory, _session = _fake_session_factory(fake_execute)
    chain = PersistentAuditChain(factory)

    result = await chain.append("t1", {"action": "logout"})

    assert result["seq"] == 6
    assert result["prev_hash"] == prior_hash


# ── verify ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_empty_chain_is_valid():
    async def fake_execute(query, params=None):
        return _Result(all_val=[])

    factory, _session = _fake_session_factory(fake_execute)
    chain = PersistentAuditChain(factory)

    ok, broken_seq = await chain.verify("t1")
    assert ok is True
    assert broken_seq is None


@pytest.mark.asyncio
async def test_verify_valid_chain_of_records():
    payload0 = {"action": "a"}
    payload1 = {"action": "b"}
    at0 = datetime(2026, 1, 1, tzinfo=UTC)
    at1 = datetime(2026, 1, 2, tzinfo=UTC)
    hash0 = compute_hash(_GENESIS, payload0, seq=0, at=at0.isoformat())
    hash1 = compute_hash(hash0, payload1, seq=1, at=at1.isoformat())
    rows = [
        (0, at0, payload0, _GENESIS, hash0),
        (1, at1, payload1, hash0, hash1),
    ]

    async def fake_execute(query, params=None):
        return _Result(all_val=rows)

    factory, _session = _fake_session_factory(fake_execute)
    chain = PersistentAuditChain(factory)

    ok, broken_seq = await chain.verify("t1")
    assert ok is True
    assert broken_seq is None


@pytest.mark.asyncio
async def test_verify_detects_tampered_payload():
    payload0 = {"action": "a"}
    at0 = datetime(2026, 1, 1, tzinfo=UTC)
    hash0 = compute_hash(_GENESIS, payload0, seq=0, at=at0.isoformat())
    # Tamper: stored payload differs from the payload that produced hash0.
    rows = [(0, at0, {"action": "TAMPERED"}, _GENESIS, hash0)]

    async def fake_execute(query, params=None):
        return _Result(all_val=rows)

    factory, _session = _fake_session_factory(fake_execute)
    chain = PersistentAuditChain(factory)

    ok, broken_seq = await chain.verify("t1")
    assert ok is False
    assert broken_seq == 0


@pytest.mark.asyncio
async def test_verify_detects_broken_prev_hash_link():
    payload0 = {"action": "a"}
    payload1 = {"action": "b"}
    at0 = datetime(2026, 1, 1, tzinfo=UTC)
    at1 = datetime(2026, 1, 2, tzinfo=UTC)
    hash0 = compute_hash(_GENESIS, payload0, seq=0, at=at0.isoformat())
    hash1 = compute_hash(hash0, payload1, seq=1, at=at1.isoformat())
    rows = [
        (0, at0, payload0, _GENESIS, hash0),
        # prev_hash forged to not match hash0
        (1, at1, payload1, "f" * 64, hash1),
    ]

    async def fake_execute(query, params=None):
        return _Result(all_val=rows)

    factory, _session = _fake_session_factory(fake_execute)
    chain = PersistentAuditChain(factory)

    ok, broken_seq = await chain.verify("t1")
    assert ok is False
    assert broken_seq == 1


@pytest.mark.asyncio
async def test_verify_handles_json_string_payload():
    """payload may come back as a JSON string rather than a decoded dict."""
    payload0 = {"action": "a"}
    at0 = datetime(2026, 1, 1, tzinfo=UTC)
    hash0 = compute_hash(_GENESIS, payload0, seq=0, at=at0.isoformat())
    import json

    rows = [(0, at0, json.dumps(payload0), _GENESIS, hash0)]

    async def fake_execute(query, params=None):
        return _Result(all_val=rows)

    factory, _session = _fake_session_factory(fake_execute)
    chain = PersistentAuditChain(factory)

    ok, broken_seq = await chain.verify("t1")
    assert ok is True
    assert broken_seq is None
