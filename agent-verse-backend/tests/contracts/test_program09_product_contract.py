from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI

from app.api.coordination_auction import router as auction_router
from app.api.coordination_camel import router as camel_router
from app.api.coordination_generative import router as generative_router
from app.api.coordination_swarm import router as swarm_router

FIXTURE = Path("tests/contracts/fixtures/program09_coordination.json")


def test_program09_product_contract_is_complete_and_versioned() -> None:
    fixture = json.loads(FIXTURE.read_text())
    assert fixture["schema_version"] == 1
    assert all(event.endswith(".v1") for event in fixture["events"])
    assert len(fixture["events"]) == len(set(fixture["events"]))
    assert all(fixture["security"].values())
    assert all(fixture["accessibility"].values())
    assert {"empty", "paused", "reconnecting", "backpressured", "unauthorized"} <= set(
        fixture["product_states"]
    )
    app = FastAPI()
    for router in (camel_router, generative_router, swarm_router, auction_router):
        app.include_router(router)
    operations = {
        operation["operationId"]
        for path in app.openapi()["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    assert set(fixture["operations"]) <= operations
