"""WorkflowVariableStore — mutable workflow variables.

Variables are distinct from step outputs:
  - Step outputs: written once when step completes, immutable after
  - Variables:    can be overwritten by any set_variable step

Variables are stored in WorkflowState["vars"] and accessed via {{vars.X}}.
"""

from __future__ import annotations

from typing import Any

from app.workflow.state import WorkflowState


class WorkflowVariableStore:
    """Manages mutable vars in WorkflowState."""

    def set(self, state: WorkflowState, name: str, value: Any) -> dict[str, Any]:
        """Return a state update dict that sets the variable."""
        current = dict(state.get("vars") or {})
        current[name] = value
        return {"vars": current}

    def get(self, state: WorkflowState, name: str, default: Any = None) -> Any:
        """Read a variable from state."""
        return (state.get("vars") or {}).get(name, default)

    def get_all(self, state: WorkflowState) -> dict[str, Any]:
        """Return all variables."""
        return dict(state.get("vars") or {})
