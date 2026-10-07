import json
from pathlib import Path

from app.orchestration.strategy_registry import build_default_registry

ROOT = Path(__file__).parents[3]


def test_generated_manifest_exactly_matches_runtime_registry() -> None:
    manifest = json.loads((ROOT / "docs/generated/agent-pattern-capabilities.json").read_text())
    ids = [row["capability_id"] for row in manifest["capabilities"]]
    runtime_ids = sorted(item.strategy_id for item in build_default_registry().list_all())
    assert sorted(ids) == runtime_ids
    assert len(ids) == len(set(ids))
    for row in manifest["capabilities"]:
        assert row["owner"]["program"]
        assert row["source_evidence"]
        if row["certification_state"] == "certified":
            assert row["evidence_links"]


def test_generated_reference_links_to_truth_without_hand_authored_counts() -> None:
    reference = (ROOT / "docs/generated/agent-pattern-capabilities.md").read_text()
    assert "Generated from the runtime registry" in reference
    assert "Certification is evidence-derived" in reference


def test_unrunnable_patterns_are_marked_unavailable_in_the_manifest() -> None:
    """a01-F016-03: rewoo / llm_compiler / lats / codeact stay registered but are not
    goal-runnable; the generated truth says so (the UI hides them, POST /goals refuses)."""
    manifest = json.loads((ROOT / "docs/generated/agent-pattern-capabilities.json").read_text())
    by_id = {row["capability_id"]: row for row in manifest["capabilities"]}
    for strategy_id in ("rewoo", "llm_compiler", "lats", "codeact"):
        assert by_id[strategy_id]["goal_selectable"] is False, strategy_id
        assert by_id[strategy_id]["availability"] == "experimental", strategy_id
    assert by_id["react"]["goal_selectable"] is True
    assert by_id["react"]["availability"] == "available"
