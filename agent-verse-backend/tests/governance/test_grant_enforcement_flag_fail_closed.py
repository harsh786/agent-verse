"""GRANT-07: a settings error must not silently turn Grantex enforcement OFF.

``_agent_grants_enforced`` (API + worker) and ``gate_from_app_state`` (workflow
gate) answered False on any exception reading settings — fail-open against the
'default ON' posture. They now answer True (enforce) when the flag is unreadable.
"""

from __future__ import annotations

from typing import Any

import pytest

# Imported before get_settings is patched: a module first imported inside the
# patch would bind the broken function for the rest of the session.
import app.agent.tool_gate
import app.db.session
import app.services.goal_service  # noqa: F401


def _broken_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.config as config

    def _boom() -> Any:
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(config, "get_settings", _boom)


def test_goal_service_flag_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.goal_service import _agent_grants_enforced

    _broken_settings(monkeypatch)
    assert _agent_grants_enforced() is True


def test_workflow_gate_flag_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent.tool_gate import gate_from_app_state

    _broken_settings(monkeypatch)
    gate = gate_from_app_state(None, agent_id="a1")
    assert gate._enforce_grants is True
