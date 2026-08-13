"""One-way translation of legacy workflow modes into registered strategies."""

from __future__ import annotations

WORKFLOW_MODE_STRATEGIES = {
    "single_agent": "react",
    "auto_route": "react",
    "workflow": "workflow_dag",
    "dag": "workflow_dag",
    "static": "workflow_dag",
}


def strategy_for_workflow_mode(workflow_mode: str) -> str:
    try:
        return WORKFLOW_MODE_STRATEGIES[workflow_mode]
    except KeyError as exc:
        raise ValueError(f"unsupported workflow mode: {workflow_mode}") from exc
