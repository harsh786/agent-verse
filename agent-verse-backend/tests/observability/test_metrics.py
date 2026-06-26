"""Tests for production-safe Prometheus metric helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from app.observability import metrics


def _sample_names() -> set[str]:
    return {sample.name for metric in metrics.REGISTRY.collect() for sample in metric.samples}


def _sample_label_keys() -> dict[str, set[frozenset[str]]]:
    label_keys: dict[str, set[frozenset[str]]] = {}
    for metric in metrics.REGISTRY.collect():
        for sample in metric.samples:
            label_keys.setdefault(sample.name, set()).add(frozenset(sample.labels.keys()))
    return label_keys


def _dashboard() -> dict[str, object]:
    dashboard_path = (
        Path(__file__).resolve().parents[2]
        / "infra/grafana/provisioning/dashboards/agentverse.json"
    )
    return cast("dict[str, object]", json.loads(dashboard_path.read_text()))


def test_metrics_helpers_record_required_low_cardinality_samples() -> None:
    metrics.record_goal_duration(status="completed", duration_seconds=1.25, priority="high")
    metrics.record_tool_call(
        tool_name="jira_search",
        connector_name="jira",
        status="success",
        duration_seconds=0.15,
    )
    metrics.record_queue_depth(queue="goals", depth=7)
    metrics.record_llm_tokens(
        provider="OpenAI",
        model="gpt-4o-mini",
        token_type="completion",
        count=128,
    )
    metrics.record_cost_usd(scope="llm", amount=0.024)
    metrics.record_approval_wait(2.5)
    metrics.record_schedule_fire(status="success")

    names = _sample_names()
    assert "agentverse_goal_duration_seconds_count" in names
    assert "agentverse_goal_total" in names
    assert "agentverse_tool_call_total" in names
    assert "agentverse_tool_call_duration_seconds_count" in names
    assert "agentverse_queue_depth" in names
    assert "agentverse_llm_tokens_total" in names
    assert "agentverse_cost_usd_total" in names
    assert "agentverse_approval_wait_seconds_count" in names
    assert "agentverse_schedule_fire_total" in names

    label_keys = _sample_label_keys()
    assert frozenset({"status", "priority"}) in label_keys["agentverse_goal_total"]
    assert frozenset({"status", "priority"}) in label_keys[
        "agentverse_goal_duration_seconds_count"
    ]
    assert frozenset({"tool", "connector", "status"}) in label_keys[
        "agentverse_tool_call_total"
    ]
    assert frozenset({"tool", "connector", "status"}) in label_keys[
        "agentverse_tool_call_duration_seconds_count"
    ]
    assert frozenset({"queue"}) in label_keys["agentverse_queue_depth"]
    assert frozenset({"provider", "model", "type"}) in label_keys[
        "agentverse_llm_tokens_total"
    ]
    assert frozenset({"scope"}) in label_keys["agentverse_cost_usd_total"]
    assert frozenset({"status"}) in label_keys["agentverse_schedule_fire_total"]
    for keys in label_keys.values():
        all_keys = set().union(*keys)
        assert "tenant_id" not in all_keys
        assert "agent_id" not in all_keys


def test_new_metrics_helpers_exist() -> None:
    assert callable(metrics.record_queue_depth)
    assert callable(metrics.record_llm_tokens)
    assert callable(metrics.record_cost_usd)
    assert callable(metrics.record_schedule_fire)


def test_existing_goal_helpers_continue_to_work() -> None:
    metrics.record_goal_started(tenant_id="tenant-should-not-be-a-label", priority="normal")
    metrics.record_goal_completed(tenant_id="tenant-should-not-be-a-label", priority="normal")
    metrics.record_goal_failed(tenant_id="tenant-should-not-be-a-label", priority="normal")

    body, content_type = metrics.render_metrics()

    assert content_type.startswith("text/plain")
    assert b'agentverse_goal_total{priority="normal",status="started"}' in body
    assert b"tenant-should-not-be-a-label" not in body


def test_record_tool_call_buckets_untrusted_tool_and_connector_labels() -> None:
    metrics.record_tool_call(
        tool_name="delete_issue_with_secret_123",
        connector_name="a4c842921babf3d9",
        status="denied",
        duration_seconds=0.05,
    )

    body, _ = metrics.render_metrics()

    assert b"delete_issue_with_secret_123" not in body
    assert b"a4c842921babf3d9" not in body
    assert b'tool="jira"' in body
    assert b'connector="unknown"' in body


def test_goal_priority_labels_bucket_untrusted_values() -> None:
    raw_priority = "customer-controlled-priority-123"

    assert metrics._normalize_priority_label(raw_priority) == "unknown"

    metrics.record_goal_started(tenant_id="tenant-safe", priority=raw_priority)
    metrics.record_goal_completed(tenant_id="tenant-safe", priority=raw_priority)
    metrics.record_goal_failed(tenant_id="tenant-safe", priority=raw_priority)
    metrics.record_goal_duration(
        status="completed", duration_seconds=0.5, priority=raw_priority
    )

    body, _ = metrics.render_metrics()

    assert raw_priority.encode() not in body
    assert b'priority="unknown"' in body


def test_new_helper_labels_bucket_untrusted_values() -> None:
    secret = "sk-live-secret-from-user-input"

    metrics.record_queue_depth(queue=secret, depth=5)
    metrics.record_llm_tokens(
        provider=secret,
        model="tenant-model-secret-123",
        token_type="custom-secret-token-kind",
        count=10,
    )
    metrics.record_cost_usd(scope="tenant-secret-scope", amount=0.01)
    metrics.record_schedule_fire(status="tenant-secret-status")

    body, _ = metrics.render_metrics()

    assert secret.encode() not in body
    assert b"tenant-model-secret-123" not in body
    assert b"custom-secret-token-kind" not in body
    assert b"tenant-secret-scope" not in body
    assert b"tenant-secret-status" not in body
    assert b'queue="unknown"' in body
    assert b'provider="unknown"' in body
    assert b'model="unknown"' in body
    assert b'type="unknown"' in body
    assert b'scope="unknown"' in body
    assert b'status="unknown"' in body


def test_status_labels_bucket_untrusted_values() -> None:
    raw_status = "Authorization: Bearer secret"

    metrics.record_goal_duration(
        status=raw_status,
        duration_seconds=0.5,
        priority="normal",
    )
    metrics.record_tool_call(
        tool_name="jira_search",
        connector_name="jira",
        status=raw_status,
        duration_seconds=0.05,
    )
    metrics.record_schedule_fire(status=raw_status)

    body, _ = metrics.render_metrics()

    assert raw_status.encode() not in body
    assert b'status="unknown"' in body


def test_dashboard_goal_latency_uses_goal_duration_histogram() -> None:
    dashboard_text = json.dumps(_dashboard())

    assert "agentverse_goal_duration_seconds_bucket" in dashboard_text
    assert "agentverse_tool_call_duration_seconds_bucket[5m]" in dashboard_text
    assert (
        "Goal Execution Latency (p50 / p95 / p99)" in dashboard_text
        and "histogram_quantile(0.95, rate(agentverse_goal_duration_seconds_bucket[5m]))"
        in dashboard_text
    )


def test_dashboard_does_not_query_tenant_labels() -> None:
    dashboard_text = json.dumps(_dashboard())

    assert "tenant_id" not in dashboard_text


def test_dashboard_goal_started_panel_does_not_claim_active_gauge() -> None:
    dashboard = _dashboard()
    dashboard_text = json.dumps(dashboard)
    panels = cast("list[dict[str, object]]", dashboard["panels"])
    panel = next(panel for panel in panels if panel.get("id") == 3)
    targets = cast("list[dict[str, str]]", panel["targets"])

    assert "Active Goals" not in dashboard_text
    assert panel["title"] == "Goals Started (5m)"
    assert targets[0]["expr"] == (
        'sum(increase(agentverse_goal_total{status="started"}[5m])) or vector(0)'
    )


def test_dashboard_contains_phase_12_operational_panels() -> None:
    dashboard = _dashboard()
    dashboard_text = json.dumps(dashboard)
    panels = cast("list[dict[str, object]]", dashboard["panels"])
    target_exprs = " ".join(
        str(target.get("expr", ""))
        for panel in panels
        for target in cast("list[dict[str, object]]", panel.get("targets", []))
    )

    assert "Approval Wait p95" in dashboard_text
    assert "agentverse_approval_wait_seconds_bucket" in target_exprs
    assert "Connector Auth and Tool Errors" in dashboard_text
    assert 'agentverse_tool_call_total{status="error"}' in target_exprs
    assert "Worker Queue Health" in dashboard_text
    assert "agentverse_queue_depth" in target_exprs
    assert "Schedule Fires" in dashboard_text
    assert "agentverse_schedule_fire_total" in target_exprs
