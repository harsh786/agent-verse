"""Security: DeletionOrchestrator must refuse unsafe subject_ref (mass-deletion guard).

An empty/short/wildcard subject_ref would ILIKE-match — and permanently delete —
unrelated subjects' or the whole tenant's data. execute_deletion must refuse.
"""

from __future__ import annotations

import pytest

from app.lifecycle.deletion_orchestrator import DeletionOrchestrator


@pytest.mark.parametrize("bad", ["", "  ", "ab", "%", "%%", "a%", "x\\y", None])
async def test_execute_deletion_refuses_unsafe_subject_ref(bad):
    orch = DeletionOrchestrator()  # no db needed; the guard runs first
    with pytest.raises(ValueError):
        await orch.execute_deletion("t1", bad)  # type: ignore[arg-type]


async def test_verify_deleted_returns_empty_for_unsafe_subject_ref():
    orch = DeletionOrchestrator()
    # Must not run a broad scan for an unsafe ref.
    assert await orch.verify_deleted("t1", "%") == {}
    assert await orch.verify_deleted("t1", "") == {}
