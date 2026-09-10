"""Self-improvement robustness: applying an optimization must persist the live
agent config even if the history/experiment bookkeeping fails afterwards.

The improved config (UPDATE agents SET config=...) is what the next agent run
reads back; it lives in its own committed transaction, so a failure in the
best-effort history INSERT can never roll it back.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.intelligence.self_optimizer_v2 import SelfOptimizerV2


class _FakeDb:
    """Async-context DB stand-in; optionally raises on execute (simulated outage)."""

    def __init__(self, fail_on_execute: bool = False) -> None:
        self.fail = fail_on_execute
        self.executed: list[str] = []
        self.committed = False

    async def __aenter__(self) -> _FakeDb:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        self.executed.append(str(stmt))
        if self.fail:
            raise RuntimeError("history DB is down")
        m = MagicMock()
        m.fetchone = MagicMock(return_value=None)
        return m

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_history_failure_does_not_negate_live_apply() -> None:
    txns: list[_FakeDb] = []

    def factory() -> _FakeDb:
        # 1st transaction = the CRITICAL config apply (succeeds);
        # 2nd transaction = best-effort history bookkeeping (fails).
        db = _FakeDb(fail_on_execute=(len(txns) >= 1))
        txns.append(db)
        return db

    opt = SelfOptimizerV2(AsyncMock(), factory, AsyncMock())
    opt._read_current_agent_config_with_session = AsyncMock(  # type: ignore[method-assign]
        return_value={"system_prompt": "old"}
    )
    opt._state = AsyncMock()

    applied = await opt.apply_suggestion("t1", "a1", "exp1", {"system_prompt": "improved"})

    # The apply reports success — the config WAS persisted despite the history failure.
    assert applied is True
    # The critical UPDATE ran and committed in the first transaction.
    assert any("UPDATE agents" in e for e in txns[0].executed)
    assert txns[0].committed is True
    # The second (history) transaction is the one that failed — proving isolation.
    assert len(txns) == 2 and txns[1].committed is False


@pytest.mark.asyncio
async def test_critical_apply_failure_reports_false() -> None:
    def factory() -> _FakeDb:
        return _FakeDb(fail_on_execute=True)  # even the critical apply fails

    opt = SelfOptimizerV2(AsyncMock(), factory, AsyncMock())
    opt._read_current_agent_config_with_session = AsyncMock(return_value={})  # type: ignore[method-assign]
    opt._state = AsyncMock()

    applied = await opt.apply_suggestion("t1", "a1", "exp1", {"system_prompt": "x"})
    assert applied is False  # a genuine apply failure is honestly reported
