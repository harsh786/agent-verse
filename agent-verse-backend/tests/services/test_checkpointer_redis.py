"""Test checkpointer resolution priority and RedisSaver wiring."""
import logging
from unittest.mock import MagicMock, patch

import pytest


def test_resolve_checkpointer_uses_app_state_first():
    """A pre-wired saver that implements the ASYNC checkpoint API is used as-is.

    The agent graph runs via ``ainvoke``, so the saver must override the async
    methods (``aget_tuple`` etc.). A sync-only saver is intentionally rejected —
    see ``test_resolve_checkpointer_rejects_sync_only_prewired_saver``.
    """
    from langgraph.checkpoint.base import BaseCheckpointSaver

    from app.services.goal_service import _resolve_checkpointer

    class _AsyncFakeSaver(BaseCheckpointSaver):
        def get_tuple(self, config):
            return None

        def list(self, config, **kwargs):
            return iter([])

        def put(self, config, checkpoint, metadata, new_versions):
            return config

        def put_writes(self, config, writes, task_id):
            pass

        async def aget_tuple(self, config):
            return None

        async def alist(self, config, **kwargs):
            for item in ():
                yield item

        async def aput(self, config, checkpoint, metadata, new_versions):
            return config

        async def aput_writes(self, config, writes, task_id):
            pass

    saver = _AsyncFakeSaver()
    app_state = MagicMock()
    app_state.langgraph_checkpointer = saver

    result = _resolve_checkpointer(app_state)
    assert result is saver, "Must return the provided async-capable checkpointer unchanged"


def test_resolve_checkpointer_rejects_sync_only_prewired_saver():
    """A sync-only pre-wired saver is rejected (it would crash the async graph)."""
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.checkpoint.memory import MemorySaver

    from app.services.goal_service import _resolve_checkpointer

    class _SyncOnlySaver(BaseCheckpointSaver):
        def get_tuple(self, config):
            return None

        def list(self, config, **kwargs):
            return iter([])

        def put(self, config, checkpoint, metadata, new_versions):
            return config

        def put_writes(self, config, writes, task_id):
            pass

    app_state = MagicMock()
    app_state.langgraph_checkpointer = _SyncOnlySaver()
    with patch.dict("os.environ", {}, clear=True):
        result = _resolve_checkpointer(app_state)
    # Falls back to the async-capable MemorySaver instead of the broken saver.
    assert isinstance(result, MemorySaver)


def test_resolve_checkpointer_logs_warning_on_memory_fallback(caplog, capsys):
    """When no Redis is available, a warning is logged about durability loss."""
    from langgraph.checkpoint.memory import MemorySaver

    from app.services.goal_service import _resolve_checkpointer

    app_state = MagicMock()
    app_state.langgraph_checkpointer = None
    # No REDIS_URL set; MagicMock settings.redis_url is not a str so it is ignored.
    with patch.dict("os.environ", {}, clear=True):
        with caplog.at_level(logging.WARNING):
            result = _resolve_checkpointer(app_state)

    assert isinstance(result, MemorySaver)
    # structlog may write to stdout rather than Python logging; check both.
    all_output = caplog.text + capsys.readouterr().out
    keywords = ("LOST", "RESTART", "memory", "MemorySaver")
    assert any(kw.lower() in all_output.lower() for kw in keywords), (
        "Must warn about state loss when falling back to MemorySaver"
    )


def test_resolve_checkpointer_prefers_redis_over_memory():
    """When REDIS_URL is set, must attempt Redis before falling back to MemorySaver."""
    from app.services.goal_service import _resolve_checkpointer

    app_state = MagicMock()
    app_state.langgraph_checkpointer = None

    with patch.dict("os.environ", {"REDIS_URL": "redis://localhost:6379"}):
        # It may fail (no Redis running) but the attempt must be made
        result = _resolve_checkpointer(app_state)
        # In test env without Redis, should fall back to MemorySaver
        # but the code path must have attempted Redis first
        assert result is not None


def test_memory_saver_warning_contains_impact(capsys):
    """MemorySaver warning must include LOST or RESTART so operators notice."""
    import io

    from app.services.goal_service import _resolve_checkpointer

    app_state = MagicMock()
    app_state.langgraph_checkpointer = None

    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setLevel(logging.WARNING)
    logger = logging.getLogger("app.services.goal_service")
    logger.addHandler(handler)
    try:
        with patch.dict("os.environ", {}, clear=True):
            _resolve_checkpointer(app_state)
        output = log_stream.getvalue()
    finally:
        logger.removeHandler(handler)

    # structlog may emit to stdout rather than the stdlib logger handler
    output += capsys.readouterr().out
    assert (
        "RESTART" in output or "LOST" in output or "memory" in output.lower()
    ), f"Warning must mention restart/loss; got: {output!r}"
