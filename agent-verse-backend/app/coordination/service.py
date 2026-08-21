"""Authorized command boundary for canonical coordination state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from app.coordination.contracts import AuthorizationContext
from app.coordination.store import AcceptedTransition, CoordinationSessionRecord
from app.tenancy.context import TenantContext


class SessionAdmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    civilization_id: str
    goal_id: str
    policy_snapshot: dict[str, Any]
    budget_snapshot: dict[str, Any]
    authorization: AuthorizationContext


class CoordinationCommandStore(Protocol):
    async def create_session(
        self, tenant_ctx: TenantContext, **values: Any
    ) -> CoordinationSessionRecord: ...

    async def transition_session(
        self, tenant_ctx: TenantContext, **values: Any
    ) -> AcceptedTransition: ...

    async def get_session(
        self, tenant_ctx: TenantContext, *, session_id: str
    ) -> CoordinationSessionRecord: ...


class CoordinationService:
    """Validate authority and immutable admission inputs before persistence."""

    def __init__(self, store: CoordinationCommandStore) -> None:
        self._store = store

    async def create_session(
        self, tenant_ctx: TenantContext, admission: SessionAdmission
    ) -> CoordinationSessionRecord:
        self._require(admission.authorization, "coordination:create")
        return await self._store.create_session(
            tenant_ctx,
            civilization_id=admission.civilization_id,
            goal_id=admission.goal_id,
            policy_snapshot=deepcopy(admission.policy_snapshot),
            budget_snapshot=deepcopy(admission.budget_snapshot),
        )

    async def start_session(
        self,
        tenant_ctx: TenantContext,
        session_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> AcceptedTransition:
        return await self._transition(
            tenant_ctx,
            session_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            target_state="active",
        )

    async def get_session(
        self, tenant_ctx: TenantContext, session_id: str
    ) -> CoordinationSessionRecord:
        if not session_id:
            raise ValueError("session ID is required")
        return await self._store.get_session(tenant_ctx, session_id=session_id)

    async def cancel_session(
        self,
        tenant_ctx: TenantContext,
        session_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> AcceptedTransition:
        return await self._transition(
            tenant_ctx,
            session_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            target_state="cancelling",
        )

    async def resume_session(
        self,
        tenant_ctx: TenantContext,
        session_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> AcceptedTransition:
        return await self._transition(
            tenant_ctx,
            session_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            target_state="active",
        )

    async def complete_session(
        self,
        tenant_ctx: TenantContext,
        session_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> AcceptedTransition:
        return await self._transition(
            tenant_ctx,
            session_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            target_state="completed",
        )

    async def fail_session(
        self,
        tenant_ctx: TenantContext,
        session_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> AcceptedTransition:
        return await self._transition(
            tenant_ctx,
            session_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            target_state="failed",
        )

    async def _transition(
        self,
        tenant_ctx: TenantContext,
        session_id: str,
        **values: Any,
    ) -> AcceptedTransition:
        if not session_id or not values.get("idempotency_key"):
            raise ValueError("session and idempotency key are required")
        return await self._store.transition_session(tenant_ctx, session_id=session_id, **values)

    @staticmethod
    def _require(authorization: AuthorizationContext, permission: str) -> None:
        if permission not in authorization.permissions:
            raise PermissionError(f"{permission} permission is required")


__all__ = ["CoordinationCommandStore", "CoordinationService", "SessionAdmission"]
