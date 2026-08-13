from app.observability.tracing import safe_pattern_attributes


def test_safe_pattern_attributes_allow_correlation_but_strip_content_and_tenant() -> None:
    attributes = safe_pattern_attributes(
        event="handoff",
        correlation_id="corr-1",
        status="success",
        tenant_id="tenant-1",
        prompt="private reasoning",
        secret="token",
    )
    assert attributes == {
        "agentverse.event": "handoff",
        "agentverse.correlation_id": "corr-1",
        "agentverse.status": "success",
    }
