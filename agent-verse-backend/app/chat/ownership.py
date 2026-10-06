"""Who may touch a chat session (CHAT-SEC-1).

A chat session belongs to the principal that created it, and only that principal
lists, reads, posts into, renames, deletes, searches, summarizes, exports or
streams it. The product has no shared or team chats (no share control in the UI
or the API), so every session is private.

Principals (:func:`principal_of`):

* a signed-in person (``TenantContext.user_id``): ``user:<user id>``;
* an API key or agent key with no person behind it: ``key:<api_key_id>``. A key
  reaches only the sessions created with that same key, never a person's;
* neither (an anonymous or malformed context): no principal, no access.

Sessions with no owner (``owner_principal IS NULL``) are the ones created by a
channel (Telegram, Slack, voice ...) or before this change. They are reachable
only through the admin routes (``/chat/admin/...``): admin-only, durably audited
before anything is returned, and an admin can assign one to a person, which
makes it that person's private session. No route reads another principal's
session.

Every repository query takes a :class:`ChatScope` (required, no default), so
each one either carries the owner predicate or says explicitly that it is a
system path (a turn already authorized, a channel, the TTL purge). The same
scope sets the ``app.chat_principal`` GUC, which the restrictive RLS policies of
``chat_sessions``, ``chat_messages`` and ``chat_artifacts`` enforce in Postgres
(migration d4f2b8c6a9e1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# The GUC value that scopes a transaction to the sessions with no owner.
UNOWNED_GUC = "unowned"


@dataclass(frozen=True, slots=True)
class ChatScope:
    """Which sessions a query may see: a principal's, the unowned ones, or all."""

    kind: Literal["system", "principal", "unowned"]
    principal: str | None = None

    @classmethod
    def of(cls, principal: str) -> ChatScope:
        if not principal:
            raise ValueError("a chat principal must be non-empty")
        return cls("principal", principal)

    @property
    def guc(self) -> str:
        """The ``app.chat_principal`` value ('' leaves the RLS owner check off)."""
        if self.kind == "principal":
            return str(self.principal)
        if self.kind == "unowned":
            return UNOWNED_GUC
        return ""

    def allows(self, owner_principal: str | None) -> bool:
        """The same rule as :meth:`predicate`, for the in-memory store."""
        if self.kind == "system":
            return True
        if self.kind == "unowned":
            return owner_principal is None
        return owner_principal is not None and owner_principal == self.principal

    def predicate(self, alias: str = "") -> tuple[str, dict[str, Any]]:
        """SQL ``AND ...`` over a ``chat_sessions`` row (``alias`` like ``"s."``)."""
        if self.kind == "principal":
            return f" AND {alias}owner_principal = :_chat_owner", {"_chat_owner": self.principal}
        if self.kind == "unowned":
            return f" AND {alias}owner_principal IS NULL", {}
        return "", {}

    def session_exists(
        self, session_col: str, tenant_param: str = "t"
    ) -> tuple[str, dict[str, Any]]:
        """SQL ``AND EXISTS`` tying a row's session to this scope (none for system)."""
        if self.kind == "system":
            return "", {}
        pred, params = self.predicate("cs_.")
        return (
            " AND EXISTS (SELECT 1 FROM chat_sessions cs_ WHERE cs_.id = "
            f"{session_col} AND cs_.tenant_id = :{tenant_param}{pred})",
            params,
        )


SYSTEM_SCOPE = ChatScope("system")
UNOWNED_SCOPE = ChatScope("unowned")


def principal_of(ctx: Any) -> str | None:
    """The chat principal of a request's tenant context, or None (no access)."""
    user_id = getattr(ctx, "user_id", None)
    if user_id:
        return f"user:{user_id}"
    key_id = getattr(ctx, "api_key_id", None)
    if key_id:
        return f"key:{key_id}"
    return None


def scope_of(ctx: Any) -> ChatScope | None:
    principal = principal_of(ctx)
    return ChatScope.of(principal) if principal else None
