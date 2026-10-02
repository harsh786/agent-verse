"""Deterministic idempotency keys for side-effecting workflow steps (WF-14).

A worker can die while a step is executing. The run is re-dispatched (stuck-run
sweep / broker redelivery), completed steps are skipped, and the step that was
in flight runs again. The receiver can only recognise that repeat if both
executions carry the same key, so the key depends on nothing but the run, the
step and (inside a foreach body) the item index.
"""

from __future__ import annotations

from typing import Any


def step_idempotency_key(state: Any, step_id: str) -> str:
    """``wf:<run_id>:<step_id>`` (``:<index>`` for a foreach item)."""
    st = state if isinstance(state, dict) else {}
    key = f"wf:{st.get('run_id') or ''}:{step_id}"
    foreach = st.get("_foreach_ctx")
    if isinstance(foreach, dict) and foreach.get("index") is not None:
        key += f":{foreach['index']}"
    return key
