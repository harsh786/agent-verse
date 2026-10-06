"""QA-21: POST /costs/predict is a read-only estimate and needs costs:read.

Regression: ("POST", "/costs") -> costs:admin also covered the read-only
predict endpoint, so viewer/operator keys (and keys scoped to costs:read)
could not get a pre-run cost estimate. The longer, more specific prefix wins.
"""

from __future__ import annotations

import pytest

from app.auth.scope_enforcement import ScopeEnforcementMiddleware, scopes_for_roles

_req = ScopeEnforcementMiddleware._required_scope


def test_predict_requires_costs_read() -> None:
    assert _req("POST", "/costs/predict") == "costs:read"


def test_other_cost_writes_still_need_costs_admin() -> None:
    assert _req("PUT", "/costs/budgets") == "costs:admin"
    assert _req("POST", "/costs/budgets") == "costs:admin"
    assert _req("DELETE", "/costs/budgets") == "costs:admin"


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_roles_with_cost_read_may_predict(role: str) -> None:
    assert _req("POST", "/costs/predict") in scopes_for_roles((role,))
