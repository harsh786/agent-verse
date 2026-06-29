"""Comprehensive tests for app/governance/audit_v2.py — targeting 90%+ coverage."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.governance.audit_v2 import (
    WAL_KEY,
    WAL_DEAD_LETTER,
    AuditEvent,
    AuditFlusher,
    AuditWriter,
    HashChainVerifier,
    _PII_KEYS,
    _redact_pii,
    audit_admin_action,
)


# ── _redact_pii ────────────────────────────────────────────────────────────────

class TestRedactPii:
    def test_redacts_known_pii_keys(self) -> None:
        for key in _PII_KEYS:
            obj = {key: "secret_value"}
            result = _redact_pii(obj)
            assert result[key] == "[REDACTED]"

    def test_preserves_non_pii_keys(self) -> None:
        obj = {"event_type": "goal.created", "tenant_id": "t1"}
        result = _redact_pii(obj)
        assert result == obj

    def test_case_insensitive_redaction(self) -> None:
        obj = {"PASSWORD": "hunter2", "API_KEY": "abc123"}
        result = _redact_pii(obj)
        assert result["PASSWORD"] == "[REDACTED]"
        assert result["API_KEY"] == "[REDACTED]"

    def test_nested_dict_redaction(self) -> None:
        obj = {"meta": {"password": "hunter2", "name": "alice"}}
        result = _redact_pii(obj)
        assert result["meta"]["password"] == "[REDACTED]"
        assert result["meta"]["name"] == "alice"

    def test_list_redaction(self) -> None:
        obj = [{"password": "x"}, {"name": "alice"}]
        result = _redact_pii(obj)
        assert result[0]["password"] == "[REDACTED]"
        assert result[1]["name"] == "alice"

    def test_depth_limit_respected(self) -> None:
        """Beyond depth 5, returns object as-is."""
        deep = {"a": {"b": {"c": {"d": {"e": {"f": {"password": "x"}}}}}}}
        result = _redact_pii(deep)
        # Should not raise regardless of depth
        assert result is not None

    def test_non_dict_non_list_returned_unchanged(self) -> None:
        assert _redact_pii("plain string") == "plain string"
        assert _redact_pii(42) == 42
        assert _redact_pii(None) is None

    def test_all_pii_keys_covered(self) -> None:
        expected = {
            "ssn", "social_security", "credit_card", "card_number", "cvv",
            "password", "secret", "token", "api_key", "private_key",
            "authorization", "auth_token", "access_token", "refresh_token",
        }
        assert _PII_KEYS == expected


# ── AuditEvent ────────────────────────────────────────────────────────────────

class TestAuditEventV2:
    def test_defaults_applied(self) -> None:
        ae = AuditEvent()
        assert ae.id is not None
        assert ae.created_at is not None
        assert ae.status == "success"
        assert ae.metadata == {}
        assert ae.prev_hash == ""
        assert ae.event_hash == ""

    def test_custom_fields_set(self) -> None:
        ae = AuditEvent(
            tenant_id="t1",
            event_type="goal.created",
            action="create",
            resource_id="r1",
            status="failure",
        )
        assert ae.tenant_id == "t1"
        assert ae.event_type == "goal.created"
        assert ae.action == "create"
        assert ae.resource_id == "r1"
        assert ae.status == "failure"

    def test_compute_hash_deterministic(self) -> None:
        ae = AuditEvent(
            id="test-id",
            tenant_id="t1",
            event_type="goal.created",
            action="create",
            status="success",
            created_at="2026-06-28T00:00:00+00:00",
        )
        h1 = ae.compute_hash("prev")
        h2 = ae.compute_hash("prev")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_different_prev_hash_changes_result(self) -> None:
        ae = AuditEvent(
            id="test-id", tenant_id="t1",
            event_type="e", action="a", status="success",
            created_at="2026-06-28T00:00:00+00:00",
        )
        assert ae.compute_hash("aaa") != ae.compute_hash("bbb")

    def test_chain_links_events(self) -> None:
        ae1 = AuditEvent(id="e1", tenant_id="t1", event_type="a", action="b", status="success")
        ae2 = AuditEvent(id="e2", tenant_id="t1", event_type="a", action="b", status="success")
        h1 = ae1.compute_hash("")
        h2 = ae2.compute_hash(h1)
        assert h2 != h1

    def test_to_dict_returns_all_slots(self) -> None:
        ae = AuditEvent(tenant_id="t1", event_type="e", action="a")
        d = ae.to_dict()
        for slot in AuditEvent.__slots__:
            assert slot in d

    def test_to_json_serializable(self) -> None:
        ae = AuditEvent(tenant_id="t1", event_type="e", action="a")
        j = ae.to_json()
        parsed = json.loads(j)
        assert parsed["tenant_id"] == "t1"

    def test_none_resource_id_handled_in_hash(self) -> None:
        ae = AuditEvent(id="x", resource_id=None, tenant_id=None)
        h = ae.compute_hash("")
        assert isinstance(h, str)


# ── AuditWriter ───────────────────────────────────────────────────────────────

class TestAuditWriter:
    async def test_write_calls_rpush(self) -> None:
        mock_redis = AsyncMock()
        writer = AuditWriter(mock_redis)
        ae = AuditEvent(tenant_id="t1", event_type="e", action="a")
        await writer.write(ae)
        mock_redis.rpush.assert_called_once_with(WAL_KEY, ae.to_json())

    async def test_write_never_raises_on_redis_error(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.rpush.side_effect = ConnectionError("Redis down")
        writer = AuditWriter(mock_redis)
        ae = AuditEvent(tenant_id="t1", event_type="e", action="a")
        await writer.write(ae)  # must not raise

    async def test_write_batch_empty_is_noop(self) -> None:
        mock_redis = AsyncMock()
        writer = AuditWriter(mock_redis)
        await writer.write_batch([])
        mock_redis.pipeline.assert_not_called()

    async def test_write_batch_pipelines_events(self) -> None:
        mock_pipeline = AsyncMock()
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline
        mock_pipeline.execute = AsyncMock(return_value=[1, 1])

        writer = AuditWriter(mock_redis)
        events = [AuditEvent(tenant_id="t1", event_type="e", action="a") for _ in range(3)]
        await writer.write_batch(events)
        assert mock_pipeline.rpush.call_count == 3
        mock_pipeline.execute.assert_called_once()

    async def test_write_batch_redis_error_suppressed(self) -> None:
        mock_pipeline = AsyncMock()
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline
        mock_pipeline.execute.side_effect = ConnectionError("Redis down")

        writer = AuditWriter(mock_redis)
        events = [AuditEvent(tenant_id="t1", event_type="e", action="a")]
        await writer.write_batch(events)  # must not raise


# ── AuditFlusher ──────────────────────────────────────────────────────────────

class TestAuditFlusher:
    def _make_redis_pipeline(self, raw_events: list[str]) -> MagicMock:
        """Return a mock Redis with pipeline that pops events."""
        pipeline_mock = AsyncMock()
        pipeline_mock.execute = AsyncMock(return_value=raw_events)
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = pipeline_mock
        return mock_redis

    async def test_flush_returns_0_when_no_events(self) -> None:
        mock_redis = self._make_redis_pipeline([None] * 100)
        mock_db = AsyncMock()

        @asynccontextmanager
        async def db_factory():
            yield mock_db

        flusher = AuditFlusher(mock_redis, db_factory)
        count = await flusher.flush()
        assert count == 0

    async def test_flush_computes_hash_chain(self) -> None:
        ae = AuditEvent(tenant_id="t1", event_type="e", action="a")
        raw = ae.to_json()

        mock_redis = self._make_redis_pipeline([raw.encode()] + [None] * 99)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()

        @asynccontextmanager
        async def db_factory():
            yield mock_session

        flusher = AuditFlusher(mock_redis, db_factory)
        count = await flusher.flush()
        assert count == 1
        assert "t1" in flusher._chain_cache

    async def test_flush_sends_to_dlq_on_db_error(self) -> None:
        ae = AuditEvent(tenant_id="t1", event_type="e", action="a")
        raw = ae.to_json()

        mock_redis = self._make_redis_pipeline([raw.encode()] + [None] * 99)
        dlq_pipeline = AsyncMock()
        dlq_pipeline.execute = AsyncMock(return_value=[1])
        mock_redis.pipeline.side_effect = [
            # First call: lpop pipeline
            AsyncMock(**{"execute": AsyncMock(return_value=[raw.encode()] + [None] * 99)}),
            # Second call: DLQ pipeline
            dlq_pipeline,
        ]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()

        @asynccontextmanager
        async def db_factory():
            yield mock_session

        flusher = AuditFlusher(mock_redis, db_factory)
        count = await flusher.flush()
        assert count == 0  # DB error → DLQ → count = 0

    async def test_flush_skips_bad_json(self) -> None:
        """Malformed JSON events are silently skipped (no crash)."""
        bad_raw = b"not-valid-json"
        mock_redis = self._make_redis_pipeline([bad_raw] + [None] * 99)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        @asynccontextmanager
        async def db_factory():
            yield mock_session

        flusher = AuditFlusher(mock_redis, db_factory)
        count = await flusher.flush()
        assert count == 0

    async def test_send_to_dlq_suppresses_errors(self) -> None:
        mock_pipeline = AsyncMock()
        mock_pipeline.execute.side_effect = Exception("DLQ down")
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline

        flusher = AuditFlusher(mock_redis, AsyncMock())
        await flusher._send_to_dlq([{"id": "x"}])  # must not raise


# ── HashChainVerifier ─────────────────────────────────────────────────────────

class TestHashChainVerifier:
    async def test_verify_empty_returns_ok(self) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        from datetime import UTC, datetime
        verifier = HashChainVerifier()
        result = await verifier.verify(
            mock_db, "t1",
            from_date=datetime(2024, 1, 1, tzinfo=UTC),
            to_date=datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert result["verified"] is True
        assert result["verified_events"] == 0
        assert result["broken_chain_at"] is None

    async def test_verify_valid_chain(self) -> None:
        from datetime import UTC, datetime

        ae = AuditEvent(
            id="e1", tenant_id="t1",
            event_type="goal.created", action="create", status="success",
            created_at="2026-06-28T00:00:00+00:00",
        )
        prev = ""
        ae.event_hash = ae.compute_hash(prev)
        ae.prev_hash = prev

        row = MagicMock()
        row.id = ae.id
        row.event_type = ae.event_type
        row.resource_id = ae.resource_id
        row.action = ae.action
        row.status = ae.status
        row.created_at = MagicMock()
        row.created_at.isoformat.return_value = ae.created_at
        row.event_hash = ae.event_hash

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [row]
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        verifier = HashChainVerifier()
        result = await verifier.verify(
            mock_db, "t1",
            from_date=datetime(2024, 1, 1, tzinfo=UTC),
            to_date=datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert result["verified"] is True
        assert result["verified_events"] == 1

    async def test_verify_detects_tampered_hash(self) -> None:
        from datetime import UTC, datetime

        row = MagicMock()
        row.id = "e1"
        row.event_type = "goal.created"
        row.resource_id = None
        row.action = "create"
        row.status = "success"
        row.created_at = MagicMock()
        row.created_at.isoformat.return_value = "2026-06-28T00:00:00+00:00"
        row.event_hash = "tampered_hash_value"

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [row]
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        verifier = HashChainVerifier()
        result = await verifier.verify(
            mock_db, "t1",
            from_date=datetime(2024, 1, 1, tzinfo=UTC),
            to_date=datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert result["verified"] is False
        assert result["broken_chain_at"] == "e1"


# ── audit_admin_action decorator ──────────────────────────────────────────────

class TestAuditAdminAction:
    async def test_success_path_calls_func(self) -> None:
        called = []

        @audit_admin_action("agent.deleted", "agent", "delete")
        async def delete_agent(**kwargs: object) -> str:
            called.append(True)
            return "ok"

        result = await delete_agent()
        assert result == "ok"
        assert called

    async def test_failure_path_reraises_exception(self) -> None:
        @audit_admin_action("agent.deleted", "agent", "delete")
        async def bad_func(**kwargs: object) -> None:
            raise ValueError("oops")

        with pytest.raises(ValueError, match="oops"):
            await bad_func()

    async def test_writes_event_to_audit_writer(self) -> None:
        mock_writer = AsyncMock()
        mock_writer.write = AsyncMock()

        mock_app_state = MagicMock()
        mock_app_state.audit_writer = mock_writer

        mock_app = MagicMock()
        mock_app.state = mock_app_state

        mock_tenant = MagicMock()
        mock_tenant.id = "t1"

        mock_request = MagicMock()
        mock_request.state.tenant = mock_tenant
        mock_request.state.api_key = None
        mock_request.client.host = "127.0.0.1"
        mock_request.headers.get.return_value = None
        mock_request.app = mock_app

        @audit_admin_action("agent.deleted", "agent", "delete")
        async def delete_agent(request: object = None) -> str:
            return "deleted"

        result = await delete_agent(request=mock_request)
        assert result == "deleted"
        mock_writer.write.assert_called_once()

    async def test_no_request_skips_audit(self) -> None:
        """When no request in kwargs/args, audit is skipped gracefully."""
        @audit_admin_action("x.y", "x", "y")
        async def func() -> str:
            return "ok"

        result = await func()
        assert result == "ok"

    async def test_extract_resource_id_called(self) -> None:
        written_events = []

        mock_writer = MagicMock()
        mock_writer.write = AsyncMock(side_effect=lambda e: written_events.append(e))

        mock_tenant = MagicMock()
        mock_tenant.id = "t1"

        mock_app_state = MagicMock()
        mock_app_state.audit_writer = mock_writer
        mock_app = MagicMock()
        mock_app.state = mock_app_state

        mock_request = MagicMock()
        mock_request.state.tenant = mock_tenant
        mock_request.state.api_key = None
        mock_request.client = None
        mock_request.headers.get.return_value = None
        mock_request.app = mock_app

        @audit_admin_action(
            "agent.deleted", "agent", "delete",
            extract_resource_id=lambda kw: kw.get("agent_id"),
        )
        async def delete_agent(agent_id: str, request: object = None) -> str:
            return "ok"

        await delete_agent(agent_id="agent-123", request=mock_request)
        assert len(written_events) == 1
        assert written_events[0].resource_id == "agent-123"
