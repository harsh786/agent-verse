from __future__ import annotations

import json
from pathlib import Path

from app.orchestration.strategy_registry import build_default_registry

INVENTORY = Path("../docs/architecture/agent-pattern-capability-inventory.json")


def test_inventory_owns_every_registry_capability_once() -> None:
    rows = json.loads(INVENTORY.read_text())["capabilities"]
    ids = [row["canonical_id"] for row in rows]
    registry_ids = {item.strategy_id for item in build_default_registry().list_all()}

    assert len(ids) == len(set(ids))
    assert registry_ids <= set(ids)


def test_inventory_rows_have_valid_evidence_owner_and_final_state() -> None:
    rows = json.loads(INVENTORY.read_text())["capabilities"]
    required = {
        "canonical_id",
        "aliases",
        "learning_phase",
        "current_registry_state",
        "actual_production_state",
        "source_evidence",
        "owner",
        "intended_final_state",
        "deprecation_rationale",
        "dependencies",
        "readiness_requirements",
        "certification_requirements",
    }
    for row in rows:
        assert required <= row.keys()
        assert row["owner"]["program"]
        assert row["owner"]["task"]
        assert Path("..").joinpath(row["source_evidence"]).exists()
        assert row["certification_requirements"]


def test_aliases_resolve_to_their_inventory_owner() -> None:
    rows = json.loads(INVENTORY.read_text())["capabilities"]
    aliases = {
        alias: row["canonical_id"] for row in rows for alias in row["aliases"]
    }

    assert aliases["cot"] == "chain_of_thought"
    assert aliases["plan_and_execute"] == "plan_execute"
