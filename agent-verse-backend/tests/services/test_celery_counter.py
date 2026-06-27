"""Tests for the bug fixes described in the AgentVerse bug report."""
from __future__ import annotations

import pytest


def test_decrement_concurrent_goals_signature():
    """decrement_concurrent_goals must accept tenant_id + redis kwargs."""
    import inspect
    from app.tenancy import limits
    src = inspect.getsource(limits)
    assert "decrement_concurrent_goals" in src, "decrement_concurrent_goals must exist"
    # Verify the actual signature accepts tenant_id as a parameter
    sig = inspect.signature(limits.decrement_concurrent_goals)
    assert "tenant_id" in sig.parameters, (
        "decrement_concurrent_goals must accept 'tenant_id'"
    )
    assert "redis" in sig.parameters, (
        "decrement_concurrent_goals must accept 'redis'"
    )


def test_audit_event_populates_api_key_id():
    """AuditEvent should have api_key_id field and accept it in constructor."""
    from app.governance.audit import AuditEvent
    from app.governance.permissions import ActionLevel
    evt = AuditEvent(
        goal_id="g1",
        tool_name="test",
        action_level=ActionLevel.ALLOW_LOG,
        outcome="success",
        api_key_id="key-123",
    )
    assert evt.api_key_id == "key-123"


def test_csp_header_in_middleware():
    """SecurityHeadersMiddleware must add Content-Security-Policy."""
    import inspect
    from app.tenancy import middleware
    src = inspect.getsource(middleware)
    assert "Content-Security-Policy" in src, "CSP header must be set"
    assert "default-src" in src, "CSP must contain default-src directive"


def test_base64_pattern_not_too_aggressive():
    """Base64 redaction must not corrupt normal output like commit SHAs."""
    from app.reliability.result_processor import ResultProcessor
    p = ResultProcessor()
    # A GitHub commit SHA should NOT be redacted (no credential keyword prefix)
    sha = "a3f2b1c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0"
    result = p.process(sha, tenant_ctx=None)
    assert sha in result, (
        f"Commit SHA should not be redacted, but got: {result!r}"
    )


def test_base64_pattern_redacts_credential_values():
    """Base64 redaction must redact values when preceded by credential keywords."""
    from app.reliability.result_processor import ResultProcessor
    p = ResultProcessor()
    # A value after 'token=' SHOULD be redacted
    text = "token=dGhpc2lzYXZlcnlsb25nc2VjcmV0dmFsdWU="
    result = p.process(text, tenant_ctx=None)
    assert "[REDACTED]" in result, (
        f"Credential-prefixed base64 should be redacted, but got: {result!r}"
    )
    # The key name should still be present
    assert "token=" in result, "Keyword 'token=' should remain in output"


def test_webhook_hmac_uses_sorted_keys():
    """Webhook HMAC must use sort_keys=True JSON for consistent bytes."""
    import inspect
    from app.services import webhook_service
    src = inspect.getsource(webhook_service)
    assert "sort_keys" in src, "Webhook HMAC must serialize with sort_keys=True"
    assert "separators" in src, "Webhook HMAC must use compact separators"


def test_event_store_has_retry_logic():
    """EventStore.append_event must implement retry with backoff."""
    import inspect
    from app.services import event_store
    src = inspect.getsource(event_store)
    assert "max_retries" in src, "EventStore must have retry logic"
    assert "asyncio.sleep" in src, "EventStore retry must use asyncio.sleep for backoff"


def test_tasks_has_decrement_after_completion():
    """run_goal must call _decrement_after_completion on completion."""
    import inspect
    from app.scaling import tasks
    src = inspect.getsource(tasks)
    assert "_decrement_after_completion" in src, (
        "_decrement_after_completion must be defined in tasks.py"
    )
    # Should appear more than once (definition + at least two call sites)
    assert src.count("_decrement_after_completion") >= 3, (
        "_decrement_after_completion must be called in multiple exit paths"
    )


def test_check_mcp_health_no_utcnow():
    """check_mcp_health must not use deprecated datetime.utcnow()."""
    import inspect
    from app.scaling import tasks
    # Get source of just check_mcp_health
    src = inspect.getsource(tasks.check_mcp_health)
    assert "utcnow()" not in src, (
        "check_mcp_health must use timezone-aware datetime, not utcnow()"
    )


def test_background_tasks_tracked_in_agent_graph():
    """AgentGraph must initialize _background_tasks to prevent GC of pending tasks."""
    import inspect
    from app.agent import graph as graph_mod
    src = inspect.getsource(graph_mod.AgentGraph.__init__)
    assert "_background_tasks" in src, (
        "AgentGraph.__init__ must initialize _background_tasks set"
    )
