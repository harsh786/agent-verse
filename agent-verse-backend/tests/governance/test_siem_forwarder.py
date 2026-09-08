"""Tests for the audit → SIEM forwarding pipeline.

Verifies that audit events recorded on the primary ``AuditLog`` trail actually
reach a configured SIEM adapter via the batched background ``SIEMForwarder``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.governance.audit import AuditEvent, AuditLog
from app.governance.permissions import ActionLevel
from app.governance.siem_adapters import SIEMAdapter, SIEMConfig, SIEMForwarder, SIEMType
from app.tenancy.context import PlanTier, TenantContext


class CapturingSIEMAdapter(SIEMAdapter):
    """Fake adapter that records every batch it is asked to send."""

    def __init__(self) -> None:
        self.batches: list[list[dict[str, Any]]] = []
        self.configs: list[SIEMConfig] = []

    async def send(self, events: list[dict[str, Any]], config: SIEMConfig) -> bool:
        self.batches.append(list(events))
        self.configs.append(config)
        return True

    @property
    def all_events(self) -> list[dict[str, Any]]:
        return [e for batch in self.batches for e in batch]


def _tenant_ctx(tenant_id: str = "tenant-1") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="key-1")


def _event(tool: str = "deploy_service", outcome: str = "success") -> AuditEvent:
    return AuditEvent(
        goal_id="goal-1",
        tool_name=tool,
        action_level=ActionLevel.ALLOW_LOG,
        outcome=outcome,
        request_id="req-1",
        ip_address="10.0.0.1",
    )


def test_forwarder_flush_sends_buffered_events_to_adapter() -> None:
    """Events enqueued on the forwarder reach the adapter on flush, with the config."""
    adapter = CapturingSIEMAdapter()
    config = SIEMConfig(siem_type=SIEMType.WEBHOOK, endpoint="https://siem.example/ingest")
    forwarder = SIEMForwarder(adapter, config)

    forwarder.enqueue({"id": "a", "event_type": "audit"})
    forwarder.enqueue({"id": "b", "event_type": "audit"})

    sent = asyncio.run(forwarder.flush_once())

    assert sent == 2
    assert len(adapter.batches) == 1
    assert [e["id"] for e in adapter.batches[0]] == ["a", "b"]
    assert adapter.configs[0] is config


def test_forwarder_flush_noop_when_empty() -> None:
    """Flushing an empty buffer sends nothing."""
    adapter = CapturingSIEMAdapter()
    forwarder = SIEMForwarder(adapter, SIEMConfig(siem_type=SIEMType.NULL))

    assert asyncio.run(forwarder.flush_once()) == 0
    assert adapter.batches == []


def test_forwarder_respects_batch_size() -> None:
    """A single flush drains at most ``batch_size`` events."""
    adapter = CapturingSIEMAdapter()
    forwarder = SIEMForwarder(
        adapter, SIEMConfig(siem_type=SIEMType.NULL), batch_size=2
    )
    for i in range(5):
        forwarder.enqueue({"id": str(i)})

    async def _drain() -> list[int]:
        counts = [await forwarder.flush_once() for _ in range(3)]
        return counts

    counts = asyncio.run(_drain())
    assert counts == [2, 2, 1]
    assert [len(b) for b in adapter.batches] == [2, 2, 1]


def test_forwarder_enqueue_never_raises() -> None:
    """enqueue must be non-blocking and swallow all errors (audit write path)."""
    adapter = CapturingSIEMAdapter()
    forwarder = SIEMForwarder(adapter, SIEMConfig(siem_type=SIEMType.NULL))
    # Even a weird payload must not raise.
    forwarder.enqueue({"id": object()})  # type: ignore[dict-item]


def test_forwarder_flush_survives_adapter_failure() -> None:
    """A failing adapter is logged, not propagated; flush returns 0."""

    class BoomAdapter(SIEMAdapter):
        async def send(self, events: list[dict[str, Any]], config: SIEMConfig) -> bool:
            raise RuntimeError("network down")

    forwarder = SIEMForwarder(BoomAdapter(), SIEMConfig(siem_type=SIEMType.NULL))
    forwarder.enqueue({"id": "x"})
    assert asyncio.run(forwarder.flush_once()) == 0


def test_audit_log_record_forwards_event_to_siem() -> None:
    """AuditLog.record() enqueues a mapped event that reaches the SIEM adapter."""
    adapter = CapturingSIEMAdapter()
    config = SIEMConfig(siem_type=SIEMType.WEBHOOK, endpoint="https://siem.example")
    forwarder = SIEMForwarder(adapter, config)

    audit = AuditLog()
    audit.set_siem_forwarder(forwarder)

    audit.record(_event(tool="delete_prod_db", outcome="denied"), tenant_ctx=_tenant_ctx())

    sent = asyncio.run(forwarder.flush_once())
    assert sent == 1

    (event,) = adapter.all_events
    assert event["tenant_id"] == "tenant-1"
    assert event["action"] == "delete_prod_db"
    assert event["status"] == "denied"
    assert event["request_id"] == "req-1"
    assert event["ip_address"] == "10.0.0.1"
    assert event["id"]  # event_id carried through
    assert event["created_at"]  # timestamp populated for SIEM ingestion


def test_audit_log_record_without_forwarder_is_safe() -> None:
    """Recording with no forwarder wired must not raise."""
    audit = AuditLog()
    audit.record(_event(), tenant_ctx=_tenant_ctx())  # no forwarder => no-op


def test_forwarder_start_stop_lifecycle() -> None:
    """The background loop drains the buffer and stops cleanly."""
    adapter = CapturingSIEMAdapter()
    forwarder = SIEMForwarder(
        adapter, SIEMConfig(siem_type=SIEMType.NULL), flush_interval=0.01
    )

    async def _run() -> None:
        forwarder.start()
        forwarder.enqueue({"id": "1"})
        # Give the loop a couple of intervals to drain.
        for _ in range(50):
            if adapter.all_events:
                break
            await asyncio.sleep(0.01)
        await forwarder.stop()

    asyncio.run(_run())
    assert [e["id"] for e in adapter.all_events] == ["1"]


def test_main_wiring_construction_pattern() -> None:
    """Regression: the construction pattern used in create_app's H-4 block works.

    Guards against the historic bug where ``SIEMConfig`` was built with a
    non-existent ``credentials=`` kwarg and a ``SIEMConfig`` object was passed to
    ``build_siem_adapter`` (which expects a type string/enum).
    """
    from app.governance.siem_adapters import WebhookAdapter, build_siem_adapter

    cfg = SIEMConfig(
        siem_type=SIEMType("webhook"),
        endpoint="https://siem.example/ingest",
        api_key="secret-token",
        extra={"token": "secret-token"},
    )
    adapter = build_siem_adapter(cfg.siem_type)
    assert isinstance(adapter, WebhookAdapter)

    forwarder = SIEMForwarder(adapter, cfg)
    audit = AuditLog()
    audit.set_siem_forwarder(forwarder)
    audit.record(_event(), tenant_ctx=_tenant_ctx())
    assert len(forwarder._buffer) == 1  # event enqueued for the background drain


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
