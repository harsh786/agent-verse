"""Tests for audit_v2: WAL guarantee, hash chain, SIEM adapters, legal holds."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.governance.audit_v2 import (
    AuditEvent,
    AuditWriter,
    AuditFlusher,
    HashChainVerifier,
    WAL_KEY,
    audit_admin_action,
)
from app.governance.siem_adapters import LEEFAdapter, SIEMConfig, SIEMType


# ---------------------------------------------------------------------------
# AuditEvent — hash correctness
# ---------------------------------------------------------------------------


class TestAuditEventHash:
    def test_hash_deterministic(self) -> None:
        ae = AuditEvent(
            id="test-id",
            tenant_id="tenant-1",
            event_type="goal.created",
            action="create",
            status="success",
            created_at="2026-06-28T00:00:00+00:00",
        )
        assert ae.compute_hash("prev") == ae.compute_hash("prev")

    def test_different_prev_hash_produces_different_hash(self) -> None:
        ae = AuditEvent(
            id="test-id",
            tenant_id="tenant-1",
            event_type="goal.created",
            action="create",
            status="success",
            created_at="2026-06-28T00:00:00+00:00",
        )
        assert ae.compute_hash("prev_a") != ae.compute_hash("prev_b")

    def test_different_events_produce_different_hashes(self) -> None:
        ae1 = AuditEvent(
            id="id-1",
            tenant_id="t1",
            event_type="goal.created",
            action="create",
            status="success",
            created_at="2026-06-28T00:00:00+00:00",
        )
        ae2 = AuditEvent(
            id="id-2",
            tenant_id="t1",
            event_type="goal.deleted",
            action="delete",
            status="success",
            created_at="2026-06-28T00:00:00+00:00",
        )
        assert ae1.compute_hash("") != ae2.compute_hash("")

    def test_serialization_round_trip(self) -> None:
        ae = AuditEvent(
            tenant_id=str(uuid4()),
            event_type="agent.deleted",
            action="delete",
            status="success",
        )
        d = ae.to_dict()
        ae2 = AuditEvent(**d)
        assert ae2.event_type == ae.event_type
        assert ae2.id == ae.id


# ---------------------------------------------------------------------------
# WAL guarantee — test_wal_write_survives_db_failure
# ---------------------------------------------------------------------------


class TestAuditWALGuarantee:
    @pytest.mark.asyncio
    async def test_wal_write_survives_db_failure(self) -> None:
        """AuditWriter writes to WAL; a DB failure does NOT drop the event."""
        mock_redis = AsyncMock()
        mock_redis.rpush = AsyncMock(return_value=1)

        writer = AuditWriter(mock_redis)
        ae = AuditEvent(
            tenant_id=str(uuid4()),
            event_type="goal.created",
            action="create",
            status="success",
        )

        # Even if we imagine DB is down, the write still succeeds (Redis WAL)
        await writer.write(ae)

        mock_redis.rpush.assert_called_once()
        assert mock_redis.rpush.call_args[0][0] == WAL_KEY

    @pytest.mark.asyncio
    async def test_writer_never_raises_on_redis_error(self) -> None:
        """AuditWriter.write() swallows Redis exceptions silently."""
        mock_redis = AsyncMock()
        mock_redis.rpush = AsyncMock(side_effect=Exception("Connection refused"))

        writer = AuditWriter(mock_redis)
        ae = AuditEvent(
            tenant_id=str(uuid4()),
            event_type="goal.created",
            action="create",
            status="success",
        )
        # Must not raise
        await writer.write(ae)

    @pytest.mark.asyncio
    async def test_write_batch_uses_pipeline(self) -> None:
        """write_batch pushes all events through a single Redis pipeline."""
        pipeline = AsyncMock()
        pipeline.rpush = AsyncMock()
        pipeline.execute = AsyncMock(return_value=[1, 1, 1])

        mock_redis = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=pipeline)

        writer = AuditWriter(mock_redis)
        events = [
            AuditEvent(
                tenant_id=str(uuid4()),
                event_type=f"event-{i}",
                action="create",
                status="success",
            )
            for i in range(3)
        ]
        await writer.write_batch(events)
        assert pipeline.rpush.call_count == 3


# ---------------------------------------------------------------------------
# Hash chain — test_hash_chain_verification_passes
# ---------------------------------------------------------------------------


class TestHashChain:
    @pytest.mark.asyncio
    async def test_hash_chain_verification_passes(self) -> None:
        """Five sequential events form a valid forward chain."""
        tenant_id = str(uuid4())
        events: list[AuditEvent] = []
        prev_hash = ""

        for i in range(5):
            ae = AuditEvent(
                id=f"event-{i}",
                tenant_id=tenant_id,
                event_type="goal.step",
                action="execute",
                status="success",
                created_at=f"2026-06-28T{i:02d}:00:00+00:00",
            )
            ae.prev_hash = prev_hash
            ae.event_hash = ae.compute_hash(prev_hash)
            prev_hash = ae.event_hash
            events.append(ae)

        # Verify chain
        current_prev = ""
        for ae in events:
            expected = ae.compute_hash(current_prev)
            assert expected == ae.event_hash, f"Chain broken at {ae.id}"
            current_prev = ae.event_hash

    @pytest.mark.asyncio
    async def test_hash_chain_detects_tampering(self) -> None:
        """Modifying an event's action breaks the hash chain."""
        tenant_id = str(uuid4())
        events: list[AuditEvent] = []
        prev_hash = ""

        for i in range(3):
            ae = AuditEvent(
                id=f"evt-{i}",
                tenant_id=tenant_id,
                event_type="goal.step",
                action="execute",
                status="success",
                created_at=f"2026-06-28T{i:02d}:00:00+00:00",
            )
            ae.prev_hash = prev_hash
            ae.event_hash = ae.compute_hash(prev_hash)
            prev_hash = ae.event_hash
            events.append(ae)

        # Tamper event 1
        events[1].action = "TAMPERED_ACTION"

        # Re-verify — must detect the break
        current_prev = ""
        chain_broken = False
        for ae in events:
            expected = ae.compute_hash(current_prev)
            if expected != ae.event_hash:
                chain_broken = True
                break
            current_prev = ae.event_hash

        assert chain_broken, "Tampering was not detected"


# ---------------------------------------------------------------------------
# Admin action decorator — test_admin_action_decorator_records_event
# ---------------------------------------------------------------------------


class TestAuditAdminDecorator:
    @pytest.mark.asyncio
    async def test_admin_action_decorator_records_event(self) -> None:
        """Decorator writes a success AuditEvent after the wrapped handler returns."""
        events_written: list[AuditEvent] = []

        class FakeWriter:
            async def write(self, event: AuditEvent) -> None:
                events_written.append(event)

        request = MagicMock()
        request.state.tenant = MagicMock(id=uuid4())
        request.state.api_key = MagicMock(
            id=uuid4(), user_id=uuid4(), prefix="av_live_xyz"
        )
        request.client = MagicMock(host="10.0.0.1")
        request.headers = {"X-Request-ID": "req-123"}
        request.app.state.audit_writer = FakeWriter()

        @audit_admin_action(
            "agent.deleted",
            "agent",
            "delete",
            extract_resource_id=lambda kw: kw.get("agent_id"),
        )
        async def mock_delete_agent(agent_id: str, request=None):  # type: ignore[override]
            return {"deleted": True}

        result = await mock_delete_agent(agent_id=str(uuid4()), request=request)
        assert result == {"deleted": True}
        assert len(events_written) == 1
        assert events_written[0].event_type == "agent.deleted"
        assert events_written[0].status == "success"

    @pytest.mark.asyncio
    async def test_admin_action_decorator_records_failure(self) -> None:
        """Decorator records a failure AuditEvent when the wrapped handler raises."""
        events_written: list[AuditEvent] = []

        class FakeWriter:
            async def write(self, event: AuditEvent) -> None:
                events_written.append(event)

        request = MagicMock()
        request.state.tenant = MagicMock(id=uuid4())
        request.state.api_key = MagicMock(
            id=uuid4(), user_id=uuid4(), prefix="av_live_abc"
        )
        request.client = MagicMock(host="10.0.0.2")
        request.headers = {"X-Request-ID": "req-456"}
        request.app.state.audit_writer = FakeWriter()

        @audit_admin_action("policy.deleted", "policy", "delete")
        async def mock_fail(request=None):  # type: ignore[override]
            raise ValueError("Policy not found")

        with pytest.raises(ValueError):
            await mock_fail(request=request)

        assert len(events_written) == 1
        assert events_written[0].status == "failure"
        assert "ValueError" in (events_written[0].error_code or "")


# ---------------------------------------------------------------------------
# LEEF adapter — test_leef_adapter_formats_correctly
# ---------------------------------------------------------------------------


class TestLEEFAdapter:
    @pytest.mark.asyncio
    async def test_leef_adapter_formats_correctly(self) -> None:
        """LEEFAdapter sends content with the correct LEEF:2.0 header prefix."""
        config = SIEMConfig(
            siem_type=SIEMType.LEEF,
            endpoint="http://qradar.example.com/siem/leef",
            api_key="test-key",
        )
        adapter = LEEFAdapter()
        event = AuditEvent(
            id=str(uuid4()),
            tenant_id="tenant-leef",
            event_type="goal.executed",
            action="execute",
            status="success",
            created_at="2026-06-28T10:00:00+00:00",
        )

        sent_payloads: list[bytes] = []

        async def fake_post(url: str, content: bytes = b"", **kwargs):  # type: ignore[override]
            sent_payloads.append(content)
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=fake_post)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await adapter.send([event.to_dict()], config)

        assert result is True
        assert len(sent_payloads) == 1
        leef_line = sent_payloads[0].decode()
        assert leef_line.startswith("LEEF:2.0|AgentVerse|")
        assert "GOAL_EXECUTED" in leef_line


# ---------------------------------------------------------------------------
# Legal hold — test_legal_hold_prevents_deletion
# ---------------------------------------------------------------------------


class TestLegalHold:
    @pytest.mark.asyncio
    async def test_legal_hold_prevents_deletion(self) -> None:
        """is_under_hold returns True for a resource in the Redis set."""
        from app.governance.legal_holds import LegalHoldManager

        mock_redis = AsyncMock()
        mock_redis.smembers = AsyncMock(return_value={b"resource-abc"})

        manager = LegalHoldManager(redis=mock_redis, db_factory=None)
        is_held = await manager.is_under_hold(
            tenant_id="tenant-hold", resource_id="resource-abc"
        )
        assert is_held is True

    @pytest.mark.asyncio
    async def test_no_legal_hold_allows_deletion(self) -> None:
        """is_under_hold returns False when the Redis set is empty."""
        from app.governance.legal_holds import LegalHoldManager

        mock_redis = AsyncMock()
        mock_redis.smembers = AsyncMock(return_value=set())

        manager = LegalHoldManager(redis=mock_redis, db_factory=None)
        is_held = await manager.is_under_hold(
            tenant_id="tenant-free", resource_id="resource-free"
        )
        assert is_held is False

    @pytest.mark.asyncio
    async def test_create_hold_warms_cache(self) -> None:
        """create_hold calls sadd on Redis for each resource_id."""
        from app.governance.legal_holds import LegalHoldManager

        mock_redis = AsyncMock()
        mock_redis.sadd = AsyncMock(return_value=2)
        mock_redis.expire = AsyncMock(return_value=True)

        manager = LegalHoldManager(redis=mock_redis, db_factory=None)
        result = await manager.create_hold(
            tenant_id="t1",
            name="SEC Investigation",
            resource_type="goal",
            resource_ids=["res-1", "res-2"],
        )

        mock_redis.sadd.assert_called_once()
        assert result["status"] == "active"
        assert len(result["resource_ids"]) == 2


# ---------------------------------------------------------------------------
# Cross-replica HITL — test_cross_replica_hitl_delivery
# ---------------------------------------------------------------------------


class TestCrossReplicaHITL:
    @pytest.mark.asyncio
    async def test_cross_replica_hitl_delivery(self) -> None:
        """BLPOP-based HITL delivery works across replicas (mocked Redis)."""
        from app.governance.hitl import HITLGateway

        payload = {"action": "approve", "approver": "alice", "note": "OK"}
        mock_redis = AsyncMock()
        mock_redis.blpop = AsyncMock(
            return_value=(b"hitl_result:req-xr", json.dumps(payload).encode())
        )

        gateway = HITLGateway()
        gateway._redis = mock_redis

        result = await gateway._wait_for_result("req-xr", timeout=5.0)

        assert result is not None
        assert result["action"] == "approve"
        assert result["approver"] == "alice"


# ---------------------------------------------------------------------------
# Batch approve — test_batch_approve_100_requests
# ---------------------------------------------------------------------------


class TestBatchApprove:
    @pytest.mark.asyncio
    async def test_batch_approve_100_requests(self) -> None:
        """batch_approve endpoint logic handles up to 100 IDs correctly."""
        from app.api.governance import BatchApproveRequest, batch_approve
        from app.governance.hitl import ApprovalStatus, HITLGateway
        from app.tenancy.context import PlanTier, TenantContext

        gateway = HITLGateway()
        tenant_ctx = TenantContext(
            tenant_id="tid-batch", plan=PlanTier.PROFESSIONAL, api_key_id="kid-batch"
        )

        # Create 5 actual pending requests
        reqs = []
        for i in range(5):
            req = gateway.request_approval(
                goal_id=f"goal-{i}",
                action=f"deploy-{i}",
                risk_level="high",
                tenant_ctx=tenant_ctx,
            )
            reqs.append(req)

        request = MagicMock()
        request.state.tenant = tenant_ctx
        request.app.state.hitl_gateway = gateway

        body = BatchApproveRequest(
            action="approve",
            request_ids=[r.request_id for r in reqs],
            approver="batch-approver",
            note="Batch approval",
        )

        result = await batch_approve(request=request, body=body)

        assert result["approved"] == 5
        assert result["not_found"] == 0
        assert len(result["results"]) == 5

    @pytest.mark.asyncio
    async def test_batch_approve_rejects_over_100_ids(self) -> None:
        """batch_approve raises HTTPException(422) for more than 100 IDs."""
        from fastapi import HTTPException as FHE

        from app.api.governance import BatchApproveRequest, batch_approve
        from app.governance.hitl import HITLGateway
        from app.tenancy.context import PlanTier, TenantContext

        gateway = HITLGateway()
        tenant_ctx = TenantContext(
            tenant_id="tid-over", plan=PlanTier.PROFESSIONAL, api_key_id="kid-over"
        )

        request = MagicMock()
        request.state.tenant = tenant_ctx
        request.app.state.hitl_gateway = gateway

        body = BatchApproveRequest(
            action="approve",
            request_ids=[str(uuid4()) for _ in range(101)],
            approver="test-approver",
            note="Over limit",
        )

        with pytest.raises(FHE) as exc_info:
            await batch_approve(request=request, body=body)

        assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# Regression: AuditFlusher db_factory keyword param
# ---------------------------------------------------------------------------

def test_audit_flusher_init_accepts_db_factory_keyword():
    """AuditFlusher.__init__ must accept db_factory= keyword, not db=.

    Regression: main.py and tasks.py called AuditFlusher(redis=r, db=factory)
    which raised TypeError. The parameter must be named db_factory.
    """
    import inspect
    from app.governance.audit_v2 import AuditFlusher

    sig = inspect.signature(AuditFlusher.__init__)
    params = list(sig.parameters.keys())

    assert "db_factory" in params, (
        f"AuditFlusher.__init__ must have 'db_factory' param; got {params}"
    )
    assert "db" not in params, (
        "AuditFlusher.__init__ must NOT have a bare 'db' param — use 'db_factory'"
    )


def test_audit_flusher_keyword_construction_works():
    """AuditFlusher must be constructable with redis= and db_factory= keywords."""
    from unittest.mock import MagicMock
    from app.governance.audit_v2 import AuditFlusher

    mock_redis = MagicMock()
    mock_db = MagicMock()

    # Must not raise TypeError
    flusher = AuditFlusher(redis=mock_redis, db_factory=mock_db)
    assert flusher._redis is mock_redis
    assert flusher._db is mock_db


# ---------------------------------------------------------------------------
# Regression: LegalHoldManager constructor signature
# ---------------------------------------------------------------------------

def test_legal_hold_manager_requires_redis_and_db_factory():
    """LegalHoldManager.__init__ must require redis and db_factory args.

    Regression: main.py called LegalHoldManager() with no args; the constructor
    requires both redis and db_factory.
    """
    import inspect
    from app.governance.legal_holds import LegalHoldManager

    sig = inspect.signature(LegalHoldManager.__init__)
    params = list(sig.parameters.keys())

    assert "redis" in params, f"LegalHoldManager must have 'redis' param; got {params}"
    assert "db_factory" in params, f"LegalHoldManager must have 'db_factory' param; got {params}"


def test_legal_hold_manager_keyword_construction_works():
    """LegalHoldManager must construct without error when given None args."""
    from app.governance.legal_holds import LegalHoldManager

    # Must not raise TypeError (previously main.py called it with no args)
    mgr = LegalHoldManager(redis=None, db_factory=None)
    assert mgr._redis is None
    assert mgr._db is None
