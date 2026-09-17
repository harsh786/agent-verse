from __future__ import annotations

import json
from pathlib import Path

from app.api.goals import GoalRequest
from app.orchestration.strategy_registry import build_default_registry


def test_legacy_and_additive_goal_contracts_coexist() -> None:
    assert GoalRequest(goal="legacy").strategy_override is None
    assert GoalRequest(goal="v2", strategy_override="react").strategy_override == "react"


def test_openapi_contains_strategy_runtime_routes_and_fields() -> None:
    schema = json.loads(Path("openapi.json").read_text(encoding="utf-8"))
    assert "/strategies" in schema["paths"]
    assert "/strategies/{strategy_id}/readiness" in schema["paths"]
    assert "/goals/{goal_id}/explain" in schema["paths"]
    goal_request = schema["components"]["schemas"]["GoalRequest"]["properties"]
    assert {"strategy_override", "auxiliary_strategies", "pattern_limits"} <= set(
        goal_request
    )


def test_capability_documentation_is_generated_from_registry() -> None:
    documentation = Path("../docs/CAPABILITIES.md").read_text(encoding="utf-8")
    for capability in build_default_registry().list_all():
        assert f"| `{capability.strategy_id}` |" in documentation


def test_runbook_documents_operational_rollout_and_rollback_controls() -> None:
    runbook = Path("../RUNBOOK.md").read_text(encoding="utf-8")
    for phrase in (
        "shadow mismatch",
        "readiness failure",
        "evidence expiry",
        "canary expansion",
        "STRATEGY_RUNTIME_V2_KILL_SWITCH",
        "alembic downgrade 0095_raft_lifecycle",
    ):
        assert phrase.lower() in runbook.lower()
