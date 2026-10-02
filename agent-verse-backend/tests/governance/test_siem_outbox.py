"""AUDIT-06 unit checks: the outbox follows the SIEM configuration everywhere.

(The real-Postgres enqueue/drain/retry/DLQ path is in test_siem_outbox_postgres.)
"""

from __future__ import annotations

import pytest

from app.governance.audit import AuditLog


def test_audit_log_enqueues_for_siem_iff_a_siem_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SIEM_TYPE", raising=False)
    assert AuditLog()._siem_outbox is False
    monkeypatch.setenv("SIEM_TYPE", "webhook")
    # Same constructor the worker uses: worker audit events reach the SIEM too.
    assert AuditLog(db_session_factory=object())._siem_outbox is True


def test_forwarder_task_is_a_noop_without_a_siem(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import tasks

    monkeypatch.delenv("SIEM_TYPE", raising=False)
    result = tasks.forward_siem_outbox.run()
    assert result["status"] == "skipped"
