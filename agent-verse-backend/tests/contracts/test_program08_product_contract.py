import json
from pathlib import Path

from fastapi import FastAPI

from app.api.coordination_magentic import router as magentic_router
from app.api.coordination_moa import router as moa_router


def test_program08_product_fixture_matches_stable_api_and_states() -> None:
    fixture = json.loads(
        Path("tests/contracts/fixtures/program08_coordination.json").read_text()
    )
    app = FastAPI()
    app.include_router(magentic_router)
    app.include_router(moa_router)
    operation_ids = {
        operation["operationId"]
        for path in app.openapi()["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    assert set(fixture["operations"]) <= operation_ids
    assert len(fixture["magentic_events"]) == 7
    assert len(fixture["moa_events"]) == 7
    assert all(item.endswith(".v1") for item in fixture["magentic_events"])
    assert all(item.endswith(".v1") for item in fixture["moa_events"])
    assert all(fixture["accessibility"].values())
    assert {"stalled", "awaiting_human", "degraded", "unauthorized"} <= set(
        fixture["product_states"]
    )
