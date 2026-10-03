"""TRUST-05: the worker's compliance autonomy ceiling fails closed.

If the bundle lookup raised (DB timeout, missing table) the worker only logged
it and kept the agent's own autonomy (e.g. fully-autonomous); with no DB the
ceiling was skipped entirely. The API path answers ``supervised`` in both cases.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.scaling import tasks


def test_no_database_means_supervised() -> None:
    assert tasks._worker_compliance_ceiling(None, "t1") == "supervised"


def test_lookup_error_means_supervised(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.governance.compliance_bundles as cb

    async def _boom(store: Any, tenant_id: str) -> str:
        raise TimeoutError("db timeout")

    monkeypatch.setattr(cb, "effective_max_autonomy_for", _boom)
    assert tasks._worker_compliance_ceiling(object(), "t1") == "supervised"


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("bounded-autonomous", "bounded-autonomous"),
        ("fully-autonomous", "fully-autonomous"),
        ("bogus", "supervised"),
    ],
)
def test_stored_ceiling_is_used(
    monkeypatch: pytest.MonkeyPatch, stored: str, expected: str
) -> None:
    import app.governance.compliance_bundles as cb

    async def _ceiling(store: Any, tenant_id: str) -> str:
        return stored

    monkeypatch.setattr(cb, "effective_max_autonomy_for", _ceiling)
    assert tasks._worker_compliance_ceiling(object(), "t1") == expected


def test_clamped_agent_mode_never_widens() -> None:
    from app.services.goal_service import clamp_autonomy_mode

    assert clamp_autonomy_mode("fully-autonomous", "supervised") == "supervised"
