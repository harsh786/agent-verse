"""Goal event emission and subscription management.

Extracted from GoalService to reduce the God-class size.
Handles: _dispatch_event, subscribe_events, _persist_event,
         _events_for_replay, _list_persisted_events, get_events
"""

from __future__ import annotations

# This module is intentionally thin - it documents the intent to extract
# event handling from GoalService. The actual extraction requires careful
# refactoring to avoid circular imports and maintain backward compatibility.
#
# The extraction plan:
# 1. GoalEventEmitter: _dispatch_event, _persist_event
# 2. GoalEventReader: get_events, _events_for_replay, _list_persisted_events
# 3. GoalEventSubscriber: subscribe_events, _event_key, _merge_events_without_duplicates
#
# Current status: Methods documented here, still implemented in GoalService.
# Migration tracked in: docs/refactoring/goal_service_decomp.md
# Re-export the event key utilities for use by tests and other services
from app.services.goal_service import GoalService


def get_event_key(event: dict) -> str:  # type: ignore[type-arg]
    """Get dedup key for an event."""
    return GoalService._event_key(event)  # type: ignore[attr-defined]
