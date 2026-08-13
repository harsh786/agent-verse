from app.observability.metrics import (
    record_coordination_event,
    record_strategy_execution,
    render_metrics,
)


def test_pattern_metrics_use_bounded_labels_without_tenant_or_session() -> None:
    record_strategy_execution(
        family="multi_agent", strategy="magentic_one", status="success",
        duration_seconds=0.2, cost_usd=0.01, tokens=20,
    )
    record_coordination_event("handoff", "success")
    output = render_metrics()[0].decode()
    expected = (
        'agentverse_strategy_execution_total{family="multi_agent",status="success",'
        'strategy="magentic_one"}'
    )
    assert expected in output
    assert 'agentverse_coordination_event_total{event="handoff",status="success"}' in output
    assert "tenant_id=" not in output
    assert "session_id=" not in output


def test_unknown_pattern_labels_collapse_to_unknown() -> None:
    record_strategy_execution(
        family="tenant-secret", strategy="run-123", status="custom",
        duration_seconds=0, cost_usd=0, tokens=0,
    )
    output = render_metrics()[0].decode()
    assert 'family="unknown",status="unknown",strategy="unknown"' in output
