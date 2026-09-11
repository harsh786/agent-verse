"""Regression: VoiceAlertManager subscribe/unsubscribe on list-backed queues.

The subscriber store is a ``dict[str, list[Queue]]`` but unsubscribe used the
set method ``.discard()``, raising AttributeError and killing the SSE teardown —
which churned the connection for every client on the page. Unsubscribe must
remove by value and drop the tenant key once its last stream disconnects.
"""

from __future__ import annotations

import asyncio

from app.voice.alerts import VoiceAlertManager


def test_unsubscribe_removes_queue_without_error() -> None:
    mgr = VoiceAlertManager(redis=None)
    q1 = mgr.subscribe("t1")
    q2 = mgr.subscribe("t1")
    assert mgr._subscribers["t1"] == [q1, q2]

    mgr.unsubscribe("t1", q1)  # must not raise (regression: .discard on a list)
    assert mgr._subscribers["t1"] == [q2]

    # Last subscriber gone → tenant key is cleaned up entirely.
    mgr.unsubscribe("t1", q2)
    assert "t1" not in mgr._subscribers


def test_unsubscribe_is_idempotent_and_safe_for_unknown() -> None:
    mgr = VoiceAlertManager(redis=None)
    q = mgr.subscribe("t2")
    mgr.unsubscribe("t2", q)
    # Removing again, or for an unknown tenant/queue, is a no-op — never raises.
    mgr.unsubscribe("t2", q)
    mgr.unsubscribe("nope", asyncio.Queue())
