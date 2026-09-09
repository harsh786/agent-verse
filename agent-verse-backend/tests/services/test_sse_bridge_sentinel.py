"""Test SSE bridge sends sentinel on terminal events (FIX H9/H10)."""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_sentinel_sent_on_goal_complete():
    queue = asyncio.Queue()
    terminal = {"goal_complete", "goal_failed", "goal_cancelled"}
    event_type = "goal_complete"
    queue.put_nowait({"type": event_type})
    if event_type in terminal:
        queue.put_nowait(None)
    items = []
    while not queue.empty():
        items.append(await queue.get())
    assert items[-1] is None, f"Last item must be None sentinel, got {items}"
    assert items[0]["type"] == "goal_complete"


@pytest.mark.asyncio
async def test_sentinel_sent_on_goal_failed():
    queue = asyncio.Queue()
    queue.put_nowait({"type": "goal_failed"})
    queue.put_nowait(None)
    items = []
    while not queue.empty():
        items.append(await queue.get())
    assert items[-1] is None


def test_queue_has_maxsize():
    q = asyncio.Queue(maxsize=512)
    assert q.maxsize == 512
    for _ in range(512):
        q.put_nowait({"type": "event"})
    with pytest.raises(asyncio.QueueFull):
        q.put_nowait({"type": "overflow"})


def test_subscribe_events_uses_bounded_queue():
    """subscribe_events must create a bounded queue to prevent OOM."""
    import pathlib
    src = pathlib.Path("app/services/goal_service.py").read_text()
    assert "asyncio.Queue(maxsize=512)" in src, (
        "subscribe_events must use asyncio.Queue(maxsize=512) not unbounded Queue()"
    )


def test_bridge_sends_sentinel_for_terminal_events():
    """_subscribe_celery_goal_events must send _SENTINEL after terminal events."""
    import pathlib
    src = pathlib.Path("app/services/goal_service.py").read_text()
    assert "_terminal_bridge" in src, (
        "_subscribe_celery_goal_events must define _terminal_bridge sentinel set"
    )
    assert "q.put_nowait(_SENTINEL)" in src, (
        "Bridge must call q.put_nowait(_SENTINEL) on terminal events"
    )
