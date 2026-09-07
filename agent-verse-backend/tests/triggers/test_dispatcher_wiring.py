"""WT-3: TriggerDispatcher must be wired onto app.state (was never instantiated)."""

from __future__ import annotations

from app.main import create_app
from app.triggers.dispatcher import TriggerDispatcher


def test_dispatcher_wired_in_memory_phase():
    """FAILS TODAY: app.state.trigger_dispatcher is never set -> fires return 503."""
    app = create_app(manage_pools=False)
    dispatcher = getattr(app.state, "trigger_dispatcher", None)
    assert isinstance(dispatcher, TriggerDispatcher), "dispatcher must be on app.state"
    # It shares the app's goal_service so fires reach the real create_goal path.
    assert dispatcher._goal_service is app.state.goal_service
