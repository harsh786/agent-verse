"""A Celery task loop's end disposes EVERY task engine still reachable, not just the
current module singleton.

run_goal captures ``get_session_factory()`` once and reuses it across several
``_run_async`` loops. ``dispose_task_engine`` used to dispose only the engine the
module currently pointed at; after the first reset, the captured factory's engine
was never disposed again, so a connection it opened in loop B stayed pooled after
loop B closed. The next loop checked it out and failed ("Event loop is closed" /
"attached to a different loop"): the worker's policy load failed closed and its
checkpoint read failed the goal (``checkpoint_unavailable``) for agent-bound goals.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import app.db.session as sess


async def test_engine_of_a_captured_factory_is_disposed_at_every_loop_end() -> None:
    first, second = MagicMock(dispose=AsyncMock()), MagicMock(dispose=AsyncMock())
    saved = (sess._engine, sess._session_factory)
    sess._engine, sess._session_factory = None, None
    try:
        with patch("app.db.session._make_engine", side_effect=[first, second]):
            captured = sess.get_session_factory()  # held across loops, like run_goal
            await sess.dispose_task_engine()  # end of loop A
            sess.get_session_factory()  # loop B builds a fresh engine
            await sess.dispose_task_engine()  # end of loop B
        assert captured is not None
        assert first.dispose.await_count == 2, "captured factory's engine leaked a loop"
        assert second.dispose.await_count == 1
    finally:
        sess._engine, sess._session_factory = saved
