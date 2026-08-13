from __future__ import annotations

import pytest

from app.main import create_app


@pytest.fixture(scope="module")
def schema() -> dict:
    return create_app(manage_pools=False).openapi()


def test_coordination_openapi_has_stable_operations_and_models(schema: dict) -> None:
    paths = schema["paths"]
    expected = {
        ("post", "/api/v1/coordination/sessions"): "create_coordination_session",
        ("get", "/api/v1/coordination/sessions/{session_id}"): "get_coordination_session",
        ("post", "/api/v1/coordination/sessions/{session_id}/cancel"): (
            "cancel_coordination_session"
        ),
        ("post", "/api/v1/coordination/sessions/{session_id}/resume"): (
            "resume_coordination_session"
        ),
        ("get", "/api/v1/coordination/sessions/{session_id}/events"): (
            "replay_coordination_events"
        ),
        ("get", "/api/v1/coordination/sessions/{session_id}/group-chat/ws"): (
            "describe_coordination_group_chat_websocket"
        ),
    }
    for (method, path), operation_id in expected.items():
        assert paths[path][method]["operationId"] == operation_id

    components = schema["components"]["schemas"]
    assert "CoordinationSessionRead" in components
    assert "CoordinationTransitionRead" in components
    assert "CanonicalTransitionRequest" in components


def test_coordination_stream_and_websocket_are_documented(schema: dict) -> None:
    event_response = schema["paths"][
        "/api/v1/coordination/sessions/{session_id}/events"
    ]["get"]["responses"]["200"]
    assert "text/event-stream" in event_response["content"]

    websocket = schema["paths"][
        "/api/v1/coordination/sessions/{session_id}/group-chat/ws"
    ]["get"]
    assert "426" in websocket["responses"]
