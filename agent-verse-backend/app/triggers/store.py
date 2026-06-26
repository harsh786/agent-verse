"""In-memory schedule store — CRUD for trigger specs, per-tenant.

In production this is backed by PostgreSQL (schedules table).

When ``db_session_factory`` is supplied, mutations are also persisted to
PostgreSQL. Most synchronous mutations use fire-and-forget asyncio tasks;
async durable paths await persistence and raise failures to callers.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from collections.abc import Awaitable
from typing import Any

from app.tenancy.context import TenantContext
from app.triggers.models import TriggerSpec

_log = logging.getLogger(__name__)
_SECRET_REDIS_FIELDS = frozenset({"webhook_token", "token", "password", "api_key", "secret"})


def _strip_secret_redis_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key.lower() not in _SECRET_REDIS_FIELDS}


class ScheduleStore:
    """Per-tenant schedule registry."""

    def __init__(self, db_session_factory: Any = None, redis: Any = None) -> None:
        # Key: (tenant_id, schedule_id) → schedule record
        self._data: dict[tuple[str, str], dict[str, Any]] = {}
        self._db = db_session_factory
        self._redis = redis
        self._db_tasks: set[asyncio.Future[None]] = set()
        self._redis_tasks: set[asyncio.Future[None]] = set()

    @staticmethod
    def _redis_key(tenant_id: str, schedule_id: str) -> str:
        return f"schedule:{tenant_id}:{schedule_id}"

    @staticmethod
    def _redis_payload(rec: dict[str, Any], tenant_id: str) -> dict[str, Any]:
        spec = rec["spec"]
        return _strip_secret_redis_fields({
            "schedule_id": rec["schedule_id"],
            "tenant_id": tenant_id,
            "goal_id": rec["goal_id"],
            "agent_id": rec.get("agent_id", ""),
            "goal_template": rec.get("goal_template", ""),
            "trigger_type": spec.trigger_type.value,
            "cron_expression": spec.cron_expression or "",
            "timezone": spec.timezone or "UTC",
            "interval_seconds": spec.interval_seconds or 0,
            "event_channel": spec.event_channel or "",
            "fire_at_iso": spec.fire_at_iso or "",
            "condition": spec.condition or "",
            "description": spec.description or "",
            "paused": bool(rec.get("paused", False)),
        })

    async def _await_redis_call(
        self, awaitable: Awaitable[Any], *, strict: bool = False
    ) -> None:
        try:
            await awaitable
        except Exception as exc:
            _log.warning("Redis schedule write failed: %s", exc)
            if strict:
                raise

    def _track_redis_call(self, result: Any) -> None:
        if not inspect.isawaitable(result):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._await_redis_call(result))
        self._redis_tasks.add(task)
        task.add_done_callback(self._redis_tasks.discard)

    def _redis_call(self, method_name: str, *args: Any) -> None:
        if self._redis is None:
            return
        try:
            method = getattr(self._redis, method_name)
            result = method(*args)
            self._track_redis_call(result)
        except Exception as exc:
            _log.warning("Redis schedule %s failed: %s", method_name, exc)

    def _write_redis_schedule(self, tenant_id: str, rec: dict[str, Any]) -> None:
        self._redis_call(
            "set",
            self._redis_key(tenant_id, rec["schedule_id"]),
            json.dumps(self._redis_payload(rec, tenant_id)),
        )

    def _delete_redis_schedule(self, tenant_id: str, schedule_id: str) -> None:
        self._redis_call("delete", self._redis_key(tenant_id, schedule_id))

    async def _write_redis_schedule_async(
        self, tenant_id: str, rec: dict[str, Any], *, strict: bool = False
    ) -> None:
        if self._redis is None:
            return
        try:
            result = self._redis.set(
                self._redis_key(tenant_id, rec["schedule_id"]),
                json.dumps(self._redis_payload(rec, tenant_id)),
            )
        except Exception as exc:
            _log.warning("Redis schedule set failed: %s", exc)
            if strict:
                raise
            return
        if inspect.isawaitable(result):
            await self._await_redis_call(result, strict=strict)

    async def _delete_redis_schedule_async(
        self, tenant_id: str, schedule_id: str, *, strict: bool = False
    ) -> None:
        if self._redis is None:
            return
        try:
            result = self._redis.delete(self._redis_key(tenant_id, schedule_id))
        except Exception as exc:
            _log.warning("Redis schedule delete failed: %s", exc)
            if strict:
                raise
            return
        if inspect.isawaitable(result):
            await self._await_redis_call(result, strict=strict)

    def create(
        self,
        *,
        goal_id: str,
        spec: TriggerSpec,
        tenant_ctx: TenantContext,
        agent_id: str = "",
        goal_template: str = "",
    ) -> str:
        sched_id = uuid.uuid4().hex
        rec = {
            "schedule_id": sched_id,
            "goal_id": goal_id,
            "agent_id": agent_id,
            "goal_template": goal_template,
            "spec": spec,
            "paused": False,
        }
        self._data[(tenant_ctx.tenant_id, sched_id)] = rec
        self._write_redis_schedule(tenant_ctx.tenant_id, rec)
        if self._db is not None:
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(
                    self._db_create(
                        sched_id,
                        goal_id,
                        spec,
                        tenant_ctx.tenant_id,
                        agent_id,
                        goal_template,
                    )
                )
                self._db_tasks.add(task)
                task.add_done_callback(self._db_tasks.discard)
            except RuntimeError:
                pass  # No running loop (e.g., in sync test context)
        return sched_id

    async def create_async(
        self,
        *,
        goal_id: str,
        spec: TriggerSpec,
        tenant_ctx: TenantContext,
        agent_id: str = "",
        goal_template: str = "",
    ) -> str:
        sched_id = uuid.uuid4().hex
        rec = {
            "schedule_id": sched_id,
            "goal_id": goal_id,
            "agent_id": agent_id,
            "goal_template": goal_template,
            "spec": spec,
            "paused": False,
        }
        db_created = False
        if self._db is not None:
            await self._db_create(
                sched_id,
                goal_id,
                spec,
                tenant_ctx.tenant_id,
                agent_id,
                goal_template,
                strict=True,
            )
            db_created = True
        try:
            await self._write_redis_schedule_async(tenant_ctx.tenant_id, rec, strict=True)
        except Exception:
            if db_created:
                await self._db_delete_schedule(
                    sched_id, tenant_ctx.tenant_id, strict=True
                )
            raise
        self._data[(tenant_ctx.tenant_id, sched_id)] = rec
        return sched_id

    async def _db_create(
        self,
        sched_id: str,
        goal_id: str,
        spec: TriggerSpec,
        tenant_id: str,
        agent_id: str,
        goal_template: str,
        *,
        strict: bool = False,
    ) -> None:
        if self._db is None:
            return
        try:
            from app.db.models.scheduling import Schedule
            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                row = Schedule(
                    id=sched_id,
                    tenant_id=tenant_id,
                    agent_id=agent_id or None,
                    goal_id_template=goal_template or goal_id,
                    trigger_type=spec.trigger_type.value,
                    cron_expression=spec.cron_expression or "",
                    timezone=spec.timezone or "UTC",
                    interval_seconds=spec.interval_seconds or 0,
                    webhook_token=spec.webhook_token or "",
                    event_channel=spec.event_channel or "",
                    fire_at_iso=spec.fire_at_iso or "",
                    condition=spec.condition or "",
                    description=spec.description or "",
                    paused=False,
                )
                session.add(row)
        except Exception as exc:
            _log.warning("DB schedule create failed: %s", exc)
            if strict:
                raise

    def get(self, schedule_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
        return self._data.get((tenant_ctx.tenant_id, schedule_id))

    def list_all(self, *, tenant_ctx: TenantContext) -> list[dict[str, Any]]:
        return [
            rec
            for (tid, _), rec in self._data.items()
            if tid == tenant_ctx.tenant_id
        ]

    def delete(self, schedule_id: str, *, tenant_ctx: TenantContext) -> bool:
        key = (tenant_ctx.tenant_id, schedule_id)
        if key not in self._data:
            return False
        del self._data[key]
        self._delete_redis_schedule(tenant_ctx.tenant_id, schedule_id)
        if self._db is not None:
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(
                    self._db_delete_schedule(schedule_id, tenant_ctx.tenant_id)
                )
                self._db_tasks.add(task)
                task.add_done_callback(self._db_tasks.discard)
            except RuntimeError:
                pass
        return True

    async def delete_async(self, schedule_id: str, *, tenant_ctx: TenantContext) -> bool:
        key = (tenant_ctx.tenant_id, schedule_id)
        if key not in self._data:
            return False
        if self._db is not None:
            await self._db_delete_schedule(
                schedule_id, tenant_ctx.tenant_id, strict=True
            )
        await self._delete_redis_schedule_async(
            tenant_ctx.tenant_id, schedule_id, strict=True
        )
        del self._data[key]
        return True

    def pause(self, schedule_id: str, *, tenant_ctx: TenantContext) -> bool:
        rec = self.get(schedule_id, tenant_ctx=tenant_ctx)
        if rec is None:
            return False
        rec["paused"] = True
        self._write_redis_schedule(tenant_ctx.tenant_id, rec)
        if self._db is not None:
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(
                    self._db_update_paused(schedule_id, tenant_ctx.tenant_id, True)
                )
                self._db_tasks.add(task)
                task.add_done_callback(self._db_tasks.discard)
            except RuntimeError:
                pass
        return True

    def resume(self, schedule_id: str, *, tenant_ctx: TenantContext) -> bool:
        rec = self.get(schedule_id, tenant_ctx=tenant_ctx)
        if rec is None:
            return False
        rec["paused"] = False
        self._write_redis_schedule(tenant_ctx.tenant_id, rec)
        if self._db is not None:
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(
                    self._db_update_paused(schedule_id, tenant_ctx.tenant_id, False)
                )
                self._db_tasks.add(task)
                task.add_done_callback(self._db_tasks.discard)
            except RuntimeError:
                pass
        return True

    async def _db_update_paused(
        self, schedule_id: str, tenant_id: str, paused: bool
    ) -> None:
        if self._db is None:
            return
        try:
            from sqlalchemy import update

            from app.db.models.scheduling import Schedule
            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    update(Schedule)
                    .where(
                        Schedule.id == schedule_id,
                        Schedule.tenant_id == tenant_id,
                    )
                    .values(paused=paused)
                )
        except Exception as exc:
            _log.warning("DB schedule update paused failed: %s", exc)

    async def _db_delete_schedule(
        self, schedule_id: str, tenant_id: str, *, strict: bool = False
    ) -> None:
        if self._db is None:
            return
        try:
            from sqlalchemy import delete

            from app.db.models.scheduling import Schedule
            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    delete(Schedule).where(
                        Schedule.id == schedule_id,
                        Schedule.tenant_id == tenant_id,
                    )
                )
        except Exception as exc:
            _log.warning("DB schedule delete failed: %s", exc)
            if strict:
                raise

    async def sync_from_db(self) -> int:
        """Load schedules from PostgreSQL into memory.

        Returns the number of new entries loaded (skips already-present keys).
        Returns 0 immediately when no ``db_session_factory`` is configured.
        """
        if self._db is None:
            return 0
        try:
            from sqlalchemy import select

            from app.db.models.scheduling import Schedule
            from app.triggers.models import TriggerSpec, TriggerType

            loaded = 0
            async with self._db() as session:
                result = await session.execute(select(Schedule))
                rows = result.scalars().all()
                for row in rows:
                    key = (row.tenant_id, row.id)
                    if key not in self._data:
                        try:
                            ttype = TriggerType(row.trigger_type)
                        except ValueError:
                            ttype = TriggerType.ONCE
                        spec = TriggerSpec(
                            trigger_type=ttype,
                            cron_expression=row.cron_expression or "",
                            timezone=row.timezone or "UTC",
                            interval_seconds=row.interval_seconds or 0,
                            webhook_token=row.webhook_token or "",
                            event_channel=row.event_channel or "",
                            fire_at_iso=row.fire_at_iso or "",
                            condition=row.condition or "",
                            description=row.description or "",
                        )
                        self._data[key] = {
                            "schedule_id": row.id,
                            "goal_id": row.goal_id_template,
                            "agent_id": str(row.agent_id or ""),
                            "goal_template": row.goal_id_template,
                            "spec": spec,
                            "paused": row.paused,
                        }
                        self._write_redis_schedule(row.tenant_id, self._data[key])
                        loaded += 1
            return loaded
        except Exception as exc:
            _log.warning("DB schedule sync failed: %s", exc)
            return 0
