from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI

from app.api.coordination_group_chat import router as group_chat_router
from app.api.coordination_handoffs import CreateHandoffRequest
from app.api.coordination_handoffs import router as handoff_router
from app.api.coordination_transcript import router as transcript_router
from app.coordination.handoffs.models import HandoffState
from app.coordination.transcript.models import TranscriptMessage

FIXTURE = Path("tests/contracts/fixtures/program07_coordination.json")


def test_program07_fixture_matches_public_contract_and_accessibility_states() -> None:
    fixture = json.loads(FIXTURE.read_text())
    assert fixture["schema_version"] == 1
    CreateHandoffRequest.model_validate(fixture["create_handoff_request"])
    assert set(fixture["handoff_states"]) == {state.value for state in HandoffState}
    assert set(fixture["message_fields"]) <= set(TranscriptMessage.model_fields)
    assert set(fixture["product_states"]) >= {
        "empty",
        "awaiting_human",
        "reconnecting",
        "backpressured",
        "unauthorized",
    }
    assert all(fixture["accessibility"].values())

    app = FastAPI()
    app.include_router(handoff_router)
    app.include_router(transcript_router)
    app.include_router(group_chat_router)
    openapi = app.openapi()
    operation_ids = {
        operation["operationId"]
        for path in openapi["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    assert set(fixture["operations"].values()) <= operation_ids
    # FastAPI 0.116+ keeps included routers lazy, so WebSocket routes are not
    # flattened into ``app.routes``.  Assert against the public router itself.
    # The router has both an HTTP GET describe-endpoint and an APIWebSocketRoute
    # at the same path; filter to only WebSocket routes for this assertion.
    from starlette.routing import WebSocketRoute
    websocket_paths = [
        route.path for route in group_chat_router.routes if isinstance(route, WebSocketRoute)
    ]
    assert websocket_paths.count(fixture["transport"]["websocket_path"]) == 1


def test_program07_fixture_has_all_versioned_events() -> None:
    events = set(json.loads(FIXTURE.read_text())["events"])
    assert len(events) == 14
    assert all(event.endswith(".v1") for event in events)
