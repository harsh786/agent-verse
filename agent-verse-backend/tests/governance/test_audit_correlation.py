"""Correlation-id consistency across a request / goal's audit trail.

Investigation for this file found that "correlation_id" propagation in this
codebase is split across three independent, loosely-connected mechanisms:

  1. HTTP error helpers (app/api/errors.py::error_response,
     app/api/goals.py::_not_found_response) read ``request.state.correlation_id``
     and fall back to minting a fresh short uuid — but until the fix applied
     alongside this test file, that freshly-minted id was never written back
     onto ``request.state``. Every error helper call (even a second one in the
     *same* request) therefore got a *different* random correlation_id, which
     defeats the entire point of a correlation id. Regression tests below
     pin the fix: within one request, all error-helper calls now share the
     same id.

  2. OTel trace/span correlation (app/observability/logging.py::
     add_trace_correlation) stamps the active span's trace_id/span_id onto
     every structlog line. This *is* fully wired and *is* the mechanism that
     actually ties multiple log lines emitted during one goal's execution
     together — tested here for multi-call consistency within one span
     (analogous to multiple audit-adjacent log lines during one goal run).

  3. The tamper-evident audit chain (app/governance/audit_v3.py::AuditV3)
     accepts an explicit ``correlation_id`` string per call (e.g. via
     ``append_security_event``) and folds it into the record's
     ``metadata_hash`` — it is never stored in the clear (by design, for
     privacy), but its presence is cryptographically verifiable: the same
     correlation_id (with everything else held constant) always reproduces
     the same metadata_hash, and a different correlation_id always changes
     it. That's what's tested here as "consistent across audit entries".
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import Request
from starlette.datastructures import State

from app.api.errors import error_response
from app.api.goals import _not_found_response
from app.governance.audit_v3 import AuditV3, _hash_dict
from app.observability.logging import add_trace_correlation
from app.observability.tracing import safe_pattern_attributes


def _fake_request() -> Request:
    """A minimal Request-like object with a real mutable .state (Starlette's
    State supports arbitrary attribute get/set, same as a real Request)."""
    req = SimpleNamespace()
    req.state = State()
    return req  # type: ignore[return-value]


# ── HTTP-level correlation-id consistency within one request ───────────────────


def test_error_response_uses_existing_request_state_correlation_id() -> None:
    request = _fake_request()
    request.state.correlation_id = "upstream-cid-123"

    resp = error_response(500, "boom", request=request)
    assert resp.body is not None
    import json as _json

    body = _json.loads(resp.body)
    assert body["correlation_id"] == "upstream-cid-123"


def test_error_response_persists_generated_id_for_reuse_within_same_request() -> None:
    """Regression test: two error_response() calls on the SAME request (e.g.
    a retry path that fails twice) must correlate to the identical id, not
    two independently-minted random ones."""
    request = _fake_request()
    assert getattr(request.state, "correlation_id", None) is None

    resp1 = error_response(500, "first failure", request=request)
    resp2 = error_response(500, "second failure", request=request)

    import json as _json

    cid1 = _json.loads(resp1.body)["correlation_id"]
    cid2 = _json.loads(resp2.body)["correlation_id"]

    assert cid1 == cid2
    assert request.state.correlation_id == cid1


def test_not_found_response_reuses_error_responses_correlation_id() -> None:
    """The two independent error helpers (errors.py and goals.py) must
    correlate to the SAME id when invoked during the same request — proving
    correlation is a property of the request, not of which helper ran."""
    request = _fake_request()

    resp = error_response(500, "boom", request=request)
    import json as _json

    cid_from_error_response = _json.loads(resp.body)["correlation_id"]

    exc = _not_found_response(request, RuntimeError("not found"))  # type: ignore[arg-type]
    assert cid_from_error_response in exc.detail
    assert request.state.correlation_id == cid_from_error_response


def test_different_requests_get_different_correlation_ids() -> None:
    """Correlation ids must not leak/collide across unrelated requests —
    each request's fallback id is independently generated."""
    request_a = _fake_request()
    request_b = _fake_request()

    resp_a = error_response(500, "boom a", request=request_a)
    resp_b = error_response(500, "boom b", request=request_b)

    import json as _json

    cid_a = _json.loads(resp_a.body)["correlation_id"]
    cid_b = _json.loads(resp_b.body)["correlation_id"]
    assert cid_a != cid_b


def test_error_response_with_no_request_still_returns_a_correlation_id() -> None:
    resp = error_response(500, "boom", request=None)
    import json as _json

    body = _json.loads(resp.body)
    assert body["correlation_id"]


# ── OTel trace/span correlation: consistent across multiple log lines ──────────


def test_trace_correlation_is_identical_across_multiple_log_lines_in_one_span() -> None:
    """A goal's execution runs inside one active span; every log line
    emitted during it (the analogue of 'every audit-adjacent log entry
    generated during that goal's execution') must carry the SAME trace_id —
    this is the mechanism that actually threads correlation through a
    goal's execution in this codebase."""
    from opentelemetry.sdk.trace import TracerProvider

    tracer = TracerProvider().get_tracer("test-goal-execution")
    with tracer.start_as_current_span("goal_execution") as span:
        ctx = span.get_span_context()
        line1 = add_trace_correlation(None, "info", {"event": "plan_ready"})
        line2 = add_trace_correlation(None, "info", {"event": "step_started"})
        line3 = add_trace_correlation(None, "info", {"event": "step_complete"})

    expected_trace_id = format(ctx.trace_id, "032x")
    assert line1["trace_id"] == expected_trace_id
    assert line2["trace_id"] == expected_trace_id
    assert line3["trace_id"] == expected_trace_id


def test_trace_correlation_differs_across_separate_spans() -> None:
    """Two different goal executions (two different spans) must NOT share a
    trace_id — cross-goal correlation leakage would be a real bug."""
    from opentelemetry.sdk.trace import TracerProvider

    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("goal_a") as span_a:
        line_a = add_trace_correlation(None, "info", {"event": "x"})
        trace_id_a = format(span_a.get_span_context().trace_id, "032x")
    with tracer.start_as_current_span("goal_b") as span_b:
        line_b = add_trace_correlation(None, "info", {"event": "y"})
        trace_id_b = format(span_b.get_span_context().trace_id, "032x")

    assert line_a["trace_id"] == trace_id_a
    assert line_b["trace_id"] == trace_id_b
    assert trace_id_a != trace_id_b


def test_safe_pattern_attributes_carries_correlation_id_with_safe_prefix() -> None:
    attrs = safe_pattern_attributes(correlation_id="cid-abc", tenant_id="should-be-dropped")
    assert attrs == {"agentverse.correlation_id": "cid-abc"}
    assert "agentverse.tenant_id" not in attrs  # tenant_id is not a safe key


# ── Audit chain: correlation_id is durably and verifiably bound to entries ─────


async def test_append_security_event_binds_correlation_id_into_the_entry_hash() -> None:
    """Two otherwise-identical security events with the SAME correlation_id
    must produce the same metadata_hash (proving it's actually included in
    what gets hashed and is reproducible/verifiable), while a DIFFERENT
    correlation_id must change the hash (proving it isn't silently dropped)."""
    audit = AuditV3()
    shared_correlation_id = uuid.uuid4().hex

    common_kwargs = dict(
        tenant_id="tenant-corr",
        object_id="goal-1",
        action="approval_issued",
        actor="user:alice",
        object_digest="digest-1",
        version_digest="v1",
        reason="policy check",
        causation_id="cause-1",
        outcome="allowed",
    )

    record1 = await audit.append_security_event(
        correlation_id=shared_correlation_id, **common_kwargs
    )
    record2 = await audit.append_security_event(
        correlation_id=shared_correlation_id, **common_kwargs
    )
    record3 = await audit.append_security_event(
        correlation_id=uuid.uuid4().hex, **common_kwargs
    )

    # Same correlation_id (+ identical other fields) -> identical metadata_hash.
    assert record1.metadata_hash == record2.metadata_hash
    # Different correlation_id -> different metadata_hash (not silently dropped).
    assert record1.metadata_hash != record3.metadata_hash


async def test_multiple_audit_entries_for_one_goal_share_correlation_id_verifiably() -> None:
    """Simulates several audit entries generated during one goal's execution
    (plan approved, budget mutated, policy compiled) that all carry the same
    correlation_id — verifies each entry's metadata_hash is independently
    reproducible from that shared correlation_id, so an operator correlating
    logs by id can cryptographically confirm every entry belongs together."""
    audit = AuditV3()
    goal_id = "goal-shared-corr"
    correlation_id = uuid.uuid4().hex
    actions = ["approval_issued", "budget_mutated", "policy_compiled"]

    records = []
    for action in actions:
        records.append(
            await audit.append_security_event(
                tenant_id="tenant-corr2",
                object_id=goal_id,
                action=action,
                actor="agent:executor",
                object_digest=f"digest-{action}",
                version_digest="v1",
                reason="goal execution step",
                correlation_id=correlation_id,
                causation_id=f"cause-{action}",
                outcome="allowed",
            )
        )

    for record in records:
        assert record.goal_id == goal_id
        # Recompute the metadata hash independently from the known
        # correlation_id + the rest of the (deterministic-except-reason-digest)
        # metadata shape to prove the stored hash really is a function of it.
        expected_metadata = {
            "object_digest": f"digest-{record.action}",
            "version_digest": "v1",
            "reason_digest": _hash_dict("goal execution step"),
            "correlation_id": correlation_id,
            "causation_id": f"cause-{record.action}",
            "outcome": "allowed",
            "timestamp_source": "utc_system_clock",
        }
        assert record.metadata_hash == _hash_dict(expected_metadata)


async def test_append_security_event_rejects_blank_correlation_id() -> None:
    audit = AuditV3()
    with pytest.raises(ValueError):
        await audit.append_security_event(
            tenant_id="t1",
            object_id="g1",
            action="approval_issued",
            actor="user:alice",
            object_digest="d1",
            version_digest="v1",
            reason="r",
            correlation_id="   ",  # blank after strip
            causation_id="c1",
            outcome="allowed",
        )
