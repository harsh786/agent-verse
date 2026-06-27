"""Tests for Phase 7 — Reliability layer.

Tests cover:
- CircuitBreaker: CLOSED/OPEN/HALF_OPEN state machine, failure threshold
- RollbackEngine: LIFO side-effect registry and rollback_all
- DeduplicationCache: content-hash based dedup with distributed lock semantics
- ResultProcessor: redact, truncate, normalize
"""

from __future__ import annotations

import pytest

from app.reliability.circuit_breaker import CircuitBreaker, CircuitState
from app.reliability.rollback import RollbackEngine
from app.reliability.dedup import DeduplicationCache
from app.reliability.result_processor import ResultProcessor
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-a", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")


# ── CircuitBreaker ─────────────────────────────────────────────────────────────

def test_circuit_breaker_starts_closed() -> None:
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    assert cb.state == CircuitState.CLOSED
    assert cb.is_closed()


def test_circuit_breaker_opens_after_threshold_failures() -> None:
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED  # still closed at 2
    cb.record_failure()
    assert cb.state == CircuitState.OPEN  # opens at 3


def test_circuit_breaker_resets_on_success() -> None:
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb._failure_count == 0


def test_circuit_breaker_open_blocks_calls() -> None:
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
    cb.record_failure()
    cb.record_failure()
    assert not cb.is_closed()


def test_circuit_breaker_half_open_after_cooldown() -> None:
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0)
    cb.record_failure()
    # cooldown_seconds=0 means immediately eligible for probe
    assert cb.state == CircuitState.OPEN
    # Force time forward by poking the internal state
    cb._opened_at = 0.0
    assert cb.allows_probe()


# ── RollbackEngine ─────────────────────────────────────────────────────────────

def test_rollback_engine_registers_and_executes_lifo() -> None:
    order: list[str] = []
    engine = RollbackEngine()
    engine.register(action="create_branch", inverse=lambda: order.append("delete_branch"))
    engine.register(action="create_pr", inverse=lambda: order.append("close_pr"))
    engine.register(action="merge_pr", inverse=lambda: order.append("revert_merge"))

    engine.rollback_all()
    assert order == ["revert_merge", "close_pr", "delete_branch"]


def test_rollback_engine_clears_after_rollback() -> None:
    executed: list[str] = []
    engine = RollbackEngine()
    engine.register(action="act", inverse=lambda: executed.append("undo"))
    engine.rollback_all()
    # Second rollback should do nothing
    engine.rollback_all()
    assert len(executed) == 1


def test_rollback_engine_empty_is_safe() -> None:
    engine = RollbackEngine()
    engine.rollback_all()  # should not raise


# ── DeduplicationCache ─────────────────────────────────────────────────────────

def test_dedup_cache_miss_on_new_hash() -> None:
    cache = DeduplicationCache()
    is_dup = cache.is_duplicate(content_hash="abc123", tenant_ctx=_CTX)
    assert is_dup is False


def test_dedup_cache_hit_on_repeated_hash() -> None:
    cache = DeduplicationCache()
    cache.mark_seen(content_hash="abc123", tenant_ctx=_CTX)
    is_dup = cache.is_duplicate(content_hash="abc123", tenant_ctx=_CTX)
    assert is_dup is True


def test_dedup_cache_tenant_isolation() -> None:
    ctx_b = TenantContext(tenant_id="tid-b", plan=PlanTier.STARTER, api_key_id="k2")
    cache = DeduplicationCache()
    cache.mark_seen(content_hash="xyz789", tenant_ctx=_CTX)
    is_dup = cache.is_duplicate(content_hash="xyz789", tenant_ctx=ctx_b)
    assert is_dup is False


# ── ResultProcessor ────────────────────────────────────────────────────────────

def test_result_processor_redacts_api_keys() -> None:
    proc = ResultProcessor()
    raw = "Token: sk-abc123def456 was used successfully"
    result = proc.process(raw)
    assert "sk-abc123def456" not in result
    assert "[REDACTED]" in result


def test_result_processor_truncates_long_output() -> None:
    proc = ResultProcessor(max_length=100)
    raw = "x" * 500
    result = proc.process(raw)
    # max_length=100 chars + truncation marker ("...[truncated]" = 14 chars) = 114 max
    assert len(result) <= 115  # slack for truncation marker length


def test_result_processor_passes_through_clean_output() -> None:
    proc = ResultProcessor()
    raw = "Successfully created PR #42 for feature/fix-checkout"
    result = proc.process(raw)
    assert "PR #42" in result


def test_result_processor_handles_empty_output() -> None:
    proc = ResultProcessor()
    result = proc.process("")
    assert isinstance(result, str)
