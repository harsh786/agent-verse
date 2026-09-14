"""Grantex-style delegated authorization for agents.

Primitives that make governance first-class at the tool-execution boundary:

* :class:`~app.governance.grants.models.Grant` — scoped, time-limited, revocable
  authority delegated to an agent (grantee) by a human/org (grantor);
* :class:`~app.governance.grants.store.InMemoryGrantStore` — issue/get/revoke/list
  (the Postgres-backed repo + migration is the persistence follow-up, mirroring
  the coordination InMemory*/Postgres* repository pattern);
* :func:`~app.governance.grants.enforcer.check_grant` — the allow/deny decision a
  tool call must pass, composing with tool-risk, policy, and HITL gates.
"""

from app.governance.grants.delegation import DelegationError, mint_delegation
from app.governance.grants.enforcer import GrantDecision, check_grant, enforce_tool_call
from app.governance.grants.models import Grant, scope_matches
from app.governance.grants.store import GrantStore, InMemoryGrantStore

__all__ = [
    "DelegationError",
    "Grant",
    "GrantDecision",
    "GrantStore",
    "InMemoryGrantStore",
    "check_grant",
    "enforce_tool_call",
    "mint_delegation",
    "scope_matches",
]
