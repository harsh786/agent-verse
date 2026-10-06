"""Schedule store — CRUD for trigger specs, per-tenant.

In production this is backed by PostgreSQL (schedules table), which is the
source of truth; the in-process dict is only a write-through cache.

When ``db_session_factory`` is supplied, mutations are also persisted to
PostgreSQL. The legacy synchronous methods (``get``/``list_all``/``pause``/…)
only see this process's cache and use fire-and-forget persistence; request
handlers must use the ``*_async`` methods, which read through to Postgres and
await durable writes. With ``strict=True`` (and on every durable write) a
database failure raises :class:`ScheduleStoreUnavailableError` instead of falling
back to the cache.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import json
import logging
import uuid
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any

from app.tenancy.context import TenantContext
from app.triggers.models import TriggerSpec

_log = logging.getLogger(__name__)
_SECRET_REDIS_FIELDS = frozenset({"webhook_token", "token", "password", "api_key", "secret"})


def _iso_or_none(value: Any) -> str | None:
    """A datetime (or ISO string) as an ISO string for the Redis mirror."""
    if value is None or value == "":
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def spec_config(spec: TriggerSpec) -> dict[str, Any]:
    """Family-specific fields the beat loop reads, keyed exactly as the loop
    expects them (bridging spec field names, e.g. ``file_drop_path`` →
    ``file_watch_path``). Carried in the schedule's ``config`` JSONB and merged
    into the discovered schedule dict so ``sched.get("rss_url")`` etc. work.
    """
    cfg: dict[str, Any] = {}
    if getattr(spec, "file_drop_path", ""):
        cfg["file_watch_path"] = spec.file_drop_path
        cfg["file_pattern"] = getattr(spec, "file_pattern", "") or "*"
    if getattr(spec, "rss_url", ""):
        cfg["rss_url"] = spec.rss_url
    if getattr(spec, "poll_url", ""):
        cfg["poll_url"] = spec.poll_url
        cfg["poll_method"] = getattr(spec, "poll_method", "") or "GET"
        cfg["poll_jsonpath"] = getattr(spec, "poll_jsonpath", "") or ""
        cfg["poll_expected_value"] = getattr(spec, "poll_expected_value", "") or ""
    # Family A time offsets (relative_delay / deadline) — beat needs these.
    if getattr(spec, "relative_offset_seconds", 0):
        cfg["relative_offset_seconds"] = int(spec.relative_offset_seconds)
    if getattr(spec, "deadline_warning_seconds", 0):
        cfg["deadline_warning_seconds"] = int(spec.deadline_warning_seconds)
    if getattr(spec, "db_table", ""):
        cfg["db_table"] = spec.db_table
    # Widen: persist every OTHER set family-specific field (S3/Sheets/SharePoint/
    # GitHub/Jira/DB/IoT/price/log/… — ~90 fields on TriggerSpec) so a schedule's
    # full config survives rehydration, not just the handful mapped above. Core
    # Schedule columns and secrets are stored elsewhere and excluded.
    for _f in dataclasses.fields(spec):
        name = _f.name
        if name in _CONFIG_EXCLUDE or name in cfg or name == "file_drop_path":
            continue
        value = getattr(spec, name, None)
        if value in (None, "", 0, 0.0, [], {}, False):
            continue
        # Skip fields still at their declared default — they rehydrate to that
        # default anyway, so persisting them only bloats the config.
        if _f.default is not dataclasses.MISSING and value == _f.default:
            continue
        if _f.default_factory is not dataclasses.MISSING and value == _f.default_factory():
            continue
        cfg[name] = value
    return cfg


# Fields NOT carried in the schedules.config column: core Schedule columns (stored
# in their own columns) and webhook secrets (never persisted in plaintext config).
_CONFIG_EXCLUDE: frozenset[str] = frozenset({
    "trigger_type", "cron_expression", "timezone", "interval_seconds", "webhook_token",
    "event_channel", "fire_at_iso", "condition", "description", "goal_template",
    "webhook_signature_secret", "webhook_signature_secret_previous",
    "webhook_signature_grace_until",
})


# Reverse of spec_config's remapping: the persisted config is keyed for the beat
# loop (e.g. file_drop_path is stored as file_watch_path), so map those keys back
# onto the spec's own field names when rehydrating from the DB.
_CONFIG_KEY_TO_SPEC_FIELD: dict[str, str] = {"file_watch_path": "file_drop_path"}


def apply_config_to_spec(spec: TriggerSpec, config: Any) -> None:
    """Restore family-specific config (persisted by ``spec_config``) onto a spec.

    Without this, a TriggerSpec rehydrated from the ``schedules`` table on a
    restart or another replica loses every family field (file-watch path, RSS/poll
    URL, db_table, time offsets, …): they read blank from the ScheduleStore and the
    schedule API, even though the beat firing path reads them straight from the
    same ``config`` column.
    """
    if not isinstance(config, dict):
        return
    for cfg_key, cfg_val in config.items():
        if not isinstance(cfg_key, str):
            continue
        field = _CONFIG_KEY_TO_SPEC_FIELD.get(cfg_key, cfg_key)
        if hasattr(spec, field):
            # A bad value must never break schedule sync.
            with contextlib.suppress(Exception):
                setattr(spec, field, cfg_val)


# A secret that cannot be decrypted must never degrade to "no secret" (which
# means "accept unsigned deliveries"): it becomes this unmatchable value, so
# signature verification fails closed.
_UNDECRYPTABLE_SECRET = "\x00undecryptable-webhook-secret\x00"


def encrypt_webhook_secret(secret: str, tenant_vault: Any = None) -> str:
    """Fernet-encrypt a webhook signing secret for the ``schedules`` row.

    With ``tenant_vault`` (the tenant's own envelope key, TENANT-ENVELOPE-ALL)
    it is sealed with that key (``tv1:``). Raises when no vault key is available
    (production without a key) — a signing secret is never written in plaintext.
    """
    if not secret:
        return ""
    from app.providers.tenant_vault import seal_for_tenant

    return seal_for_tenant(tenant_vault, secret)


def decrypt_webhook_secret(ciphertext: str, tenant_vault: Any = None) -> str:
    """Open a stored secret; a ``tv1:`` value without its tenant key (or any value
    that cannot be decrypted) becomes the unmatchable sentinel — fail closed."""
    if not ciphertext:
        return ""
    try:
        from app.providers.tenant_vault import open_for_tenant

        return open_for_tenant(tenant_vault, ciphertext)
    except Exception as exc:
        _log.error("webhook secret decrypt failed (failing closed): %s", type(exc).__name__)
        return _UNDECRYPTABLE_SECRET


def _strip_secret_redis_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key.lower() not in _SECRET_REDIS_FIELDS}


class ScheduleStoreUnavailableError(RuntimeError):
    """The durable schedule store (Postgres) could not be read or written.

    Raised by the strict read paths and the durable write paths so callers fail
    CLOSED (the API answers 503) instead of serving this process's cache, which
    on a multi-replica deployment may be stale or simply missing the schedule.
    Subclasses ``RuntimeError`` so existing broad handlers keep working; the
    message carries the underlying error.
    """


def bind_refs_to_spec(spec: TriggerSpec, *, agent_id: str = "", goal_template: str = "") -> None:
    """Fold the record-level agent/goal references onto the spec in place.

    ``agent_id`` and ``goal_template`` are trigger-level references, but every
    dispatch path (the manual fire/simulate API, the celery beat schedule loop,
    and all ~13 category consumers — goal-chain, event, condition, conversational,
    data/file, monitoring, IoT, …) resolves the goal text and the routed agent
    from the *spec* it is handed. Binding the refs onto the spec here makes the
    spec self-contained, so a trigger that merely references an agent (with no
    goal template) routes to that agent and runs the agent's own goal on EVERY
    trigger type — not just the handful whose consumer happened to read the record.

    The referenced agent is the agent to RUN, bound as the ``agent_id`` instance
    attribute (like ``trigger_id``; it is a first-class Schedule column, so it is
    not persisted in the config JSONB). It is never folded onto
    ``watch_agent_id``: for goal-event triggers that field is the SOURCE filter
    ("only goals run by this agent"), and conflating the two made "when any goal
    completes, run agent X" watch only X's goals and re-fire on its own goal.

    An explicit spec-level value always wins over the record-level ref.
    """
    if agent_id and not (getattr(spec, "agent_id", "") or "").strip():
        spec.agent_id = agent_id  # type: ignore[attr-defined]
    if goal_template and not (getattr(spec, "goal_template", "") or "").strip():
        spec.goal_template = goal_template


# TRG-30: with a DB the in-process dict is only a cache; every DB read used to
# add its rows to it forever. It is bounded (oldest-written entries go first).
_CACHE_MAX_ENTRIES = 10_000


class _BoundedCache(dict[tuple[str, str], dict[str, Any]]):
    """Insertion-ordered dict that evicts its oldest entries beyond ``maxsize``
    (``None`` = unbounded, for the in-memory mode where it IS the store)."""

    def __init__(self, maxsize: int | None) -> None:
        super().__init__()
        self.maxsize = maxsize

    def __setitem__(self, key: tuple[str, str], value: dict[str, Any]) -> None:
        if key in self:
            super().__delitem__(key)  # re-insert at the end (most recent)
        super().__setitem__(key, value)
        if self.maxsize is not None:
            while len(self) > self.maxsize:
                super().__delitem__(next(iter(self)))


class ScheduleStore:
    """Per-tenant schedule registry."""

    def __init__(
        self,
        db_session_factory: Any = None,
        redis: Any = None,
        system_db_session_factory: Any = None,
    ) -> None:
        # Key: (tenant_id, schedule_id) → schedule record. With a DB configured
        # this is only a CACHE: the ``*_async`` read paths re-read the tenant's
        # rows from Postgres (the source of truth) so a trigger created, paused,
        # edited or deleted on another replica is seen here too.
        self._data: dict[tuple[str, str], dict[str, Any]] = _BoundedCache(
            _CACHE_MAX_ENTRIES if db_session_factory is not None else None
        )
        self._db = db_session_factory
        self._redis = redis
        # Maintenance (BYPASSRLS) factory for the cross-tenant startup load. The
        # load used to run on the tenant factory with no GUC, so under the
        # NOBYPASSRLS application role it saw zero rows and every replica
        # started with an empty trigger registry.
        self._system_db = system_db_session_factory
        self._db_tasks: set[asyncio.Future[None]] = set()
        self._redis_tasks: set[asyncio.Future[None]] = set()

    async def _tenant_vault(self, tenant_id: str) -> Any:
        """The tenant's envelope key (``None`` = none / no DB); raises when unreadable."""
        from app.providers.tenant_vault import ensure_tenant_vault

        return await ensure_tenant_vault(self._db, tenant_id)

    @staticmethod
    def _redis_key(tenant_id: str, schedule_id: str) -> str:
        return f"schedule:{tenant_id}:{schedule_id}"

    @staticmethod
    def _redis_payload(rec: dict[str, Any], tenant_id: str) -> dict[str, Any]:
        spec = rec["spec"]
        return _strip_secret_redis_fields(
            {
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
                # B1-1: the beat fires no slot at or before this instant.
                "armed_at": _iso_or_none(rec.get("armed_at") or rec.get("created_at")),
                # Family-specific fields (file_watch_path, rss_url, poll_url, …)
                # merged so the beat loop can read them from the schedule dict.
                **spec_config(spec),
            }
        )

    async def _await_redis_call(self, awaitable: Awaitable[Any], *, strict: bool = False) -> None:
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
        # Make the spec self-contained so every dispatch path (API fire, beat,
        # all category consumers) routes to the referenced agent / runs its goal.
        bind_refs_to_spec(spec, agent_id=agent_id, goal_template=goal_template)
        # trigger_id is an instance attribute (not a dataclass field) read by the
        # dispatcher to derive a per-trigger idempotency key. Without it every
        # trigger dispatches as "unknown" and distinct triggers dedup against
        # each other. (The beat path sets this the same way.)
        spec.trigger_id = sched_id  # type: ignore[attr-defined]
        rec = {
            "schedule_id": sched_id,
            "goal_id": goal_id,
            "agent_id": agent_id,
            "goal_template": goal_template,
            "spec": spec,
            "paused": False,
            "created_at": datetime.now(UTC),
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
        quota_plan: str | None = None,
    ) -> str:
        """Durably create a schedule (DB first, then Redis, then the cache).

        ``quota_plan`` enforces ``PLAN_MAX_TRIGGERS`` for that plan. With a DB
        the count is taken from ``schedules`` inside the INSERT's transaction
        under a per-tenant advisory lock, so concurrent creates on different
        replicas cannot both slip under the cap; without a DB the in-memory
        registry is the only (and therefore authoritative) count. Raises
        :class:`~app.triggers.quota.TriggerQuotaExceeded`.
        """
        if quota_plan is not None and self._db is None:
            from app.triggers.quota import TriggerQuotaEnforcer

            TriggerQuotaEnforcer().check_create(
                len(self.list_all(tenant_ctx=tenant_ctx)), quota_plan
            )
        sched_id = uuid.uuid4().hex
        bind_refs_to_spec(spec, agent_id=agent_id, goal_template=goal_template)
        spec.trigger_id = sched_id  # type: ignore[attr-defined]
        rec = {
            "schedule_id": sched_id,
            "goal_id": goal_id,
            "agent_id": agent_id,
            "goal_template": goal_template,
            "spec": spec,
            "paused": False,
            "created_at": datetime.now(UTC),
        }
        rec["armed_at"] = rec["created_at"]
        db_created = False
        if self._db is not None:
            create_kwargs: dict[str, Any] = {"strict": True}
            if quota_plan is not None:
                create_kwargs["quota_plan"] = quota_plan
            await self._db_create(
                sched_id,
                goal_id,
                spec,
                tenant_ctx.tenant_id,
                agent_id,
                goal_template,
                **create_kwargs,
            )
            db_created = True
        try:
            await self._write_redis_schedule_async(tenant_ctx.tenant_id, rec, strict=True)
        except Exception:
            if db_created:
                await self._db_delete_schedule(sched_id, tenant_ctx.tenant_id, strict=True)
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
        quota_plan: str | None = None,
    ) -> None:
        if self._db is None:
            return
        from app.triggers.quota import TriggerQuotaEnforcer, TriggerQuotaExceeded

        try:
            from sqlalchemy import text

            from app.db.models.scheduling import Schedule
            from app.db.rls import sqlalchemy_rls_context

            tenant_vault = await self._tenant_vault(tenant_id)
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                if quota_plan is not None:
                    # Serialise this tenant's creates, then count the durable
                    # rows — the quota used to be defined but never checked.
                    await session.execute(
                        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                        {"k": f"trigger_quota:{tenant_id}"},
                    )
                    current = (
                        await session.execute(
                            text("SELECT count(*) FROM schedules WHERE tenant_id = :t"),
                            {"t": tenant_id},
                        )
                    ).scalar_one()
                    TriggerQuotaEnforcer().check_create(int(current or 0), quota_plan)
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
                    config=spec_config(spec),
                    paused=False,
                    webhook_signature_secret_enc=encrypt_webhook_secret(
                        getattr(spec, "webhook_signature_secret", "") or "", tenant_vault
                    ),
                )
                session.add(row)
        except TriggerQuotaExceeded:
            raise
        except Exception as exc:
            _log.warning("DB schedule create failed: %s", exc)
            if strict:
                raise ScheduleStoreUnavailableError(f"schedule create failed: {exc}") from exc

    async def update_secret_async(
        self,
        schedule_id: str,
        *,
        new_secret: str,
        tenant_id: str,
        grace_period_seconds: int = 0,
    ) -> bool:
        """Rotate a webhook signing secret, retaining the previous one for a grace
        window so in-flight deliveries signed with the old secret still verify.

        Returns True if a record was updated. With a DB this is durable and
        STRICT: the rotation used to be written to a ``webhook_signature_secret``
        column that did not exist, without RLS, and the failure was swallowed —
        so the API handed out a "new" secret that no other replica (and no
        restart) ever knew about. Both secrets are stored encrypted.
        """
        from datetime import timedelta

        rec = await self._get_for_tenant_async(tenant_id, schedule_id, strict=True)
        if rec is None:
            return False
        spec = rec["spec"]
        prev = getattr(spec, "webhook_signature_secret", "") or ""
        grace_until = datetime.now(UTC) + timedelta(seconds=max(0, grace_period_seconds))
        if self._db is not None:
            tenant_vault = await self._tenant_vault(tenant_id)
            updated = await self._db_update_values(
                schedule_id,
                tenant_id,
                {
                    "webhook_signature_secret_enc": encrypt_webhook_secret(
                        new_secret, tenant_vault
                    ),
                    "webhook_signature_secret_prev_enc": encrypt_webhook_secret(
                        prev, tenant_vault
                    ),
                    "webhook_secret_grace_until": grace_until,
                },
            )
            if not updated:
                self._data.pop((tenant_id, schedule_id), None)
                return False
        spec.webhook_signature_secret = new_secret
        rec["previous_webhook_secret"] = prev
        rec["secret_grace_until"] = grace_until.timestamp()
        return True

    async def _db_update_values(
        self, schedule_id: str, tenant_id: str, values: dict[str, Any]
    ) -> bool:
        """Strict, RLS-scoped UPDATE of one schedule row. True if a row matched."""
        if self._db is None:
            return True
        from sqlalchemy import update

        from app.db.models.scheduling import Schedule
        from app.db.rls import sqlalchemy_rls_context

        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                result = await session.execute(
                    update(Schedule)
                    .where(Schedule.id == schedule_id, Schedule.tenant_id == tenant_id)
                    .values(**values)
                )
        except Exception as exc:
            _log.warning("DB schedule update failed: %s", exc)
            raise ScheduleStoreUnavailableError(f"schedule update failed: {exc}") from exc
        rowcount = getattr(result, "rowcount", None)
        return not isinstance(rowcount, int) or rowcount > 0

    async def set_paused_async(
        self, schedule_id: str, *, paused: bool, tenant_ctx: TenantContext
    ) -> dict[str, Any] | None:
        """Durably pause/resume: DB row, then the Redis copy the beat reads, then
        the cache. Returns the updated record, or None if it does not exist.

        ``POST /triggers/{id}/resume`` used to flip only the in-memory record,
        so Redis and Postgres stayed paused: the beat never fired the trigger
        again, and a restart reloaded it as paused.
        """
        tenant_id = tenant_ctx.tenant_id
        rec = await self._get_for_tenant_async(tenant_id, schedule_id, strict=True)
        if rec is None:
            return None
        values: dict[str, Any] = {"paused": paused, "next_fire_at": None}
        if not paused:
            # B1-1: a resumed schedule fires no slot from while it was paused.
            values["armed_at"] = datetime.now(UTC)
        if self._db is not None and not await self._db_update_values(
            # Resuming resets next_fire_at so the beat re-evaluates it next tick (TRG-15).
            schedule_id, tenant_id, values
        ):
            self._data.pop((tenant_id, schedule_id), None)
            return None
        rec["paused"] = paused
        if "armed_at" in values:
            rec["armed_at"] = values["armed_at"]
        await self._write_redis_schedule_async(tenant_id, rec, strict=True)
        return rec

    async def update_async(
        self,
        schedule_id: str,
        *,
        tenant_ctx: TenantContext,
        goal_template: str | None = None,
        paused: bool | None = None,
        spec: TriggerSpec | None = None,
    ) -> dict[str, Any] | None:
        """Durably apply a partial update (PATCH). Returns the record or None.

        PATCH used to mutate only this process's record: nothing reached Redis
        (the beat kept firing the old cron/template) or Postgres (a restart or
        another replica reverted the edit). A replacement spec keeps the
        existing webhook token / signing secret unless it sets new ones, so an
        edit cannot silently disable a webhook's URL or its signature check.
        """
        tenant_id = tenant_ctx.tenant_id
        rec = await self._get_for_tenant_async(tenant_id, schedule_id, strict=True)
        if rec is None:
            return None
        new_rec = dict(rec)
        if goal_template is not None:
            new_rec["goal_template"] = goal_template
        if paused is not None:
            new_rec["paused"] = paused
        old_spec: TriggerSpec = rec["spec"]
        new_spec = spec if spec is not None else old_spec
        if spec is not None:
            if not spec.webhook_token:
                spec.webhook_token = old_spec.webhook_token
            if not spec.webhook_signature_secret:
                spec.webhook_signature_secret = old_spec.webhook_signature_secret
        if goal_template is not None:
            # The record-level template is what every dispatch path renders.
            new_spec.goal_template = goal_template
        bind_refs_to_spec(
            new_spec,
            agent_id=str(new_rec.get("agent_id") or ""),
            goal_template=str(new_rec.get("goal_template") or ""),
        )
        new_spec.trigger_id = schedule_id  # type: ignore[attr-defined]
        new_rec["spec"] = new_spec
        if spec is not None or (paused is False and rec.get("paused")):
            # B1-1: a re-timed or resumed schedule fires no slot from before now
            # (an edit used to replay every slot of the new cron since the last fire).
            new_rec["armed_at"] = datetime.now(UTC)
        if self._db is not None:
            values: dict[str, Any] = {
                "goal_id_template": new_rec.get("goal_template") or new_rec.get("goal_id") or "",
                "paused": bool(new_rec.get("paused", False)),
                "trigger_type": new_spec.trigger_type.value,
                "cron_expression": new_spec.cron_expression or "",
                "timezone": new_spec.timezone or "UTC",
                "interval_seconds": new_spec.interval_seconds or 0,
                "webhook_token": new_spec.webhook_token or "",
                "event_channel": new_spec.event_channel or "",
                "fire_at_iso": new_spec.fire_at_iso or "",
                "condition": new_spec.condition or "",
                "description": new_spec.description or "",
                "config": spec_config(new_spec),
                # An edit may move the next fire earlier: re-evaluate next tick.
                "next_fire_at": None,
            }
            if "armed_at" in new_rec:
                values["armed_at"] = new_rec["armed_at"]
            if spec is not None:
                values["webhook_signature_secret_enc"] = encrypt_webhook_secret(
                    new_spec.webhook_signature_secret or "",
                    await self._tenant_vault(tenant_id),
                )
            if not await self._db_update_values(schedule_id, tenant_id, values):
                self._data.pop((tenant_id, schedule_id), None)
                return None
        rec.clear()
        rec.update(new_rec)
        await self._write_redis_schedule_async(tenant_id, rec, strict=True)
        return rec

    # ── DB read-through (Postgres is the source of truth) ────────────────────

    def _record_from_row(self, row: Any, tenant_vault: Any = None) -> dict[str, Any]:
        from app.triggers.models import TriggerType

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
            webhook_signature_secret=decrypt_webhook_secret(
                str(getattr(row, "webhook_signature_secret_enc", "") or ""), tenant_vault
            ),
        )
        # Rehydrate the family-specific config (file-watch path, RSS/poll URL,
        # db_table, time offsets, …) that spec_config persisted on create.
        apply_config_to_spec(spec, getattr(row, "config", None))
        row_agent = str(row.agent_id or "")
        row_goal_tmpl = row.goal_id_template or ""
        # Keep the rehydrated spec self-contained across restarts / replicas so
        # agent-referencing triggers still route + run the agent's goal.
        bind_refs_to_spec(spec, agent_id=row_agent, goal_template=row_goal_tmpl)
        spec.trigger_id = row.id  # type: ignore[attr-defined]
        grace_dt = getattr(row, "webhook_secret_grace_until", None)
        return {
            "schedule_id": row.id,
            "goal_id": row.goal_id_template,
            "agent_id": row_agent,
            "goal_template": row_goal_tmpl,
            "spec": spec,
            "paused": bool(row.paused),
            "created_at": getattr(row, "created_at", None),
            "last_fired_at": getattr(row, "last_fired_at", None),
            "next_fire_at": getattr(row, "next_fire_at", None),
            "armed_at": getattr(row, "armed_at", None),
            "previous_webhook_secret": decrypt_webhook_secret(
                str(getattr(row, "webhook_signature_secret_prev_enc", "") or ""), tenant_vault
            ),
            "secret_grace_until": (
                grace_dt.timestamp() if isinstance(grace_dt, datetime) else 0.0
            ),
        }

    async def _db_fetch_tenant(
        self,
        tenant_id: str,
        *,
        schedule_id: str | None = None,
        trigger_type: str | None = None,
        webhook_token: str | None = None,
        strict: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]] | None:
        """Read this tenant's schedule rows under its RLS context and refresh the
        cache from them. Returns None when there is no DB, or when the read
        failed and ``strict`` is False (callers then fall back to the cache).
        With ``strict`` a failed read raises :class:`ScheduleStoreUnavailableError`
        so the caller fails closed instead of trusting a possibly stale cache."""
        if self._db is None:
            return None
        try:
            from sqlalchemy import select

            from app.db.models.scheduling import Schedule
            from app.db.rls import sqlalchemy_rls_context

            stmt = select(Schedule).where(Schedule.tenant_id == tenant_id)
            if schedule_id is not None:
                stmt = stmt.where(Schedule.id == schedule_id)
            if trigger_type is not None:
                stmt = stmt.where(Schedule.trigger_type == trigger_type)
            if webhook_token is not None:
                stmt = stmt.where(Schedule.webhook_token == webhook_token)
            if limit is not None:
                # Paginate in SQL (TRG-30), in a stable order.
                stmt = stmt.order_by(Schedule.created_at, Schedule.id).limit(limit).offset(offset)
            tenant_vault = await self._tenant_vault(tenant_id)
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                rows = list((await session.execute(stmt)).scalars().all())
            records = [self._record_from_row(row, tenant_vault) for row in rows]
        except Exception as exc:
            _log.warning("DB schedule read failed tenant=%s: %s", tenant_id, exc)
            if strict:
                raise ScheduleStoreUnavailableError(f"schedule read failed: {exc}") from exc
            return None
        await self._rewrap_secrets(tenant_id, rows, records, tenant_vault)
        for rec in records:
            self._data[(tenant_id, rec["schedule_id"])] = rec
        if schedule_id is not None and not records:
            # Deleted elsewhere — drop the stale cache entry.
            self._data.pop((tenant_id, schedule_id), None)
        return records

    async def _rewrap_secrets(
        self,
        tenant_id: str,
        rows: list[Any],
        records: list[dict[str, Any]],
        tenant_vault: Any,
    ) -> None:
        """Lazy re-wrap: re-seal platform-vault (or replaced-tenant-key) webhook
        secrets with the tenant's current key. Compare-and-swap on the stored
        ciphertext (a concurrent rotation wins); never re-seals the undecryptable
        sentinel; best effort (the read already succeeded)."""
        from app.providers.tenant_vault import needs_rewrap

        if tenant_vault is None:
            return
        for row, rec in zip(rows, records, strict=True):
            cur = str(getattr(row, "webhook_signature_secret_enc", "") or "")
            prev = str(getattr(row, "webhook_signature_secret_prev_enc", "") or "")
            if not (needs_rewrap(tenant_vault, cur) or needs_rewrap(tenant_vault, prev)):
                continue
            cur_plain = getattr(rec["spec"], "webhook_signature_secret", "") or ""
            prev_plain = str(rec.get("previous_webhook_secret") or "")
            if _UNDECRYPTABLE_SECRET in (cur_plain, prev_plain):
                continue
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
                            Schedule.id == row.id,
                            Schedule.tenant_id == tenant_id,
                            Schedule.webhook_signature_secret_enc == cur,
                            Schedule.webhook_signature_secret_prev_enc == prev,
                        )
                        .values(
                            webhook_signature_secret_enc=encrypt_webhook_secret(
                                cur_plain, tenant_vault
                            ),
                            webhook_signature_secret_prev_enc=encrypt_webhook_secret(
                                prev_plain, tenant_vault
                            ),
                        )
                    )
            except Exception as exc:
                _log.warning("webhook secret re-wrap failed schedule=%s: %s", row.id, exc)

    async def _get_for_tenant_async(
        self, tenant_id: str, schedule_id: str, *, strict: bool = False
    ) -> dict[str, Any] | None:
        fetched = await self._db_fetch_tenant(tenant_id, schedule_id=schedule_id, strict=strict)
        if fetched is not None:
            return fetched[0] if fetched else None
        return self._data.get((tenant_id, schedule_id))

    async def get_async(
        self, schedule_id: str, *, tenant_ctx: TenantContext, strict: bool = False
    ) -> dict[str, Any] | None:
        """``get`` with DB read-through, so a trigger created/edited/deleted on
        another replica is seen here (``get`` only knows this process).

        ``strict=True`` raises :class:`ScheduleStoreUnavailableError` when the DB
        read fails instead of answering from this process's cache."""
        return await self._get_for_tenant_async(
            tenant_ctx.tenant_id, schedule_id, strict=strict
        )

    async def get_by_webhook_token_async(
        self, token: str, *, tenant_ctx: TenantContext, strict: bool = False
    ) -> dict[str, Any] | None:
        """The tenant's ``webhook`` trigger whose token is ``token`` (paused or
        not), or None. With a DB the ``schedules`` row is the source of truth,
        so a token minted on another replica resolves here too; the stored
        token is re-confirmed in constant time either way."""
        import hmac

        from app.triggers.models import TriggerType

        if not token:
            return None
        fetched = await self._db_fetch_tenant(
            tenant_ctx.tenant_id,
            trigger_type=TriggerType.WEBHOOK.value,
            webhook_token=token,
            strict=strict,
        )
        candidates = fetched if fetched is not None else self.list_all(tenant_ctx=tenant_ctx)
        for rec in candidates:
            spec = rec.get("spec")
            if spec is None or getattr(spec, "trigger_type", None) != TriggerType.WEBHOOK:
                continue
            stored = str(getattr(spec, "webhook_token", "") or "")
            if stored and hmac.compare_digest(stored.encode(), token.encode()):
                return rec
        return None

    async def list_all_async(
        self,
        *,
        tenant_ctx: TenantContext,
        strict: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """The tenant's schedules; ``limit``/``offset`` paginate in SQL (TRG-30)."""
        fetched = await self._db_fetch_tenant(
            tenant_ctx.tenant_id, strict=strict, limit=limit, offset=offset
        )
        if fetched is not None and limit is not None:
            return fetched  # a page: cannot prune the cache from it
        if fetched is None and limit is not None:
            return self.list_all(tenant_ctx=tenant_ctx)[offset : offset + limit]
        if fetched is not None:
            live = {rec["schedule_id"] for rec in fetched}
            for key in [k for k in self._data if k[0] == tenant_ctx.tenant_id]:
                if key[1] not in live:
                    del self._data[key]
            return fetched
        return self.list_all(tenant_ctx=tenant_ctx)

    def get(self, schedule_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
        return self._data.get((tenant_ctx.tenant_id, schedule_id))

    def list_all(self, *, tenant_ctx: TenantContext) -> list[dict[str, Any]]:
        return [rec for (tid, _), rec in self._data.items() if tid == tenant_ctx.tenant_id]

    def find_by_type(self, trigger_type: str, *, tenant_id: str) -> list[dict[str, Any]]:
        """Return all enabled triggers of a given type for a tenant (in-memory)."""
        result = []
        for (tid, _), rec in self._data.items():
            if tid != tenant_id:
                continue
            if rec.get("paused", False):
                continue
            spec = rec.get("spec")
            if spec is not None and hasattr(spec, "trigger_type"):
                tt = spec.trigger_type
                rec_type = tt.value if hasattr(tt, "value") else str(tt)
            else:
                rec_type = str(rec.get("trigger_type", ""))
            if rec_type == trigger_type:
                result.append(rec)
        return result

    async def find_tenant_by_webhook_token(
        self, token: str, *, system_db: Any = None
    ) -> str | None:
        """Resolve the tenant owning a webhook token — the PRE-AUTH lookup for
        third-party typed-webhook delivery, where the caller has no API key.

        With ``system_db`` — the maintenance/BYPASSRLS factory, the only one that
        can see a row before its tenant is known — the indexed
        ``schedules.webhook_token`` lookup is the ONLY source (TRG-27): it used
        to be preceded by a linear compare against every cached schedule, and a
        DB error fell back to this replica's cache, answering 404 (permanent to
        senders) for a live trigger. A DB error now raises
        :class:`ScheduleStoreUnavailableError` (the caller answers 503). The
        stored token is re-confirmed in constant time. Without a DB (in-memory
        mode) the cache is authoritative and is searched; a token held by more
        than one tenant there is ambiguous and resolves to nobody. Only the
        tenant id leaves this method.
        """
        import hmac

        if not token:
            return None
        if system_db is not None:
            return await self._db_find_tenant_by_webhook_token(token, system_db)
        owners: set[str] = set()
        for (tid, _), rec in self._data.items():
            stored = str(getattr(rec.get("spec"), "webhook_token", "") or "")
            if stored and hmac.compare_digest(stored.encode(), token.encode()):
                owners.add(tid)
        return owners.pop() if len(owners) == 1 else None

    @staticmethod
    async def _db_find_tenant_by_webhook_token(token: str, system_db: Any) -> str | None:
        import hmac

        try:
            from sqlalchemy import text

            from app.db.rls import system_session

            async with system_db() as session, session.begin(), system_session(session):
                row = (
                    await session.execute(
                        text(
                            "SELECT tenant_id, webhook_token FROM schedules "
                            "WHERE webhook_token = :tok AND webhook_token <> '' LIMIT 1"
                        ),
                        {"tok": token},
                    )
                ).fetchone()
        except Exception as exc:
            _log.warning("webhook token lookup failed: %s", exc)
            raise ScheduleStoreUnavailableError(f"webhook token lookup failed: {exc}") from exc
        if row is None:
            return None
        if not hmac.compare_digest(str(row[1] or "").encode(), token.encode()):
            return None
        return str(row[0])

    async def find_by_type_async(
        self,
        trigger_type: str | None = None,
        *,
        tenant_id: str,
        strict: bool = False,
        **_: object,
    ) -> list[dict[str, Any]]:
        """Async version of find_by_type for use in consumers.

        ``strict`` raises :class:`ScheduleStoreUnavailableError` on a DB outage
        instead of falling back to this replica's cache (TRG-27).

        Accepts both positional and keyword ``trigger_type`` for ergonomics.

        With a DB the tenant's rows of that type are re-read first: this used to
        consult only this process's memory, so an inbound webhook/event for a
        trigger created on ANOTHER replica resolved its tenant from the DB
        (``find_tenant_by_webhook_token``) and then still found no trigger (404).
        """
        if trigger_type is None:
            return []
        fetched = await self._db_fetch_tenant(tenant_id, trigger_type=trigger_type, strict=strict)
        if fetched is not None:
            return [rec for rec in fetched if not rec.get("paused", False)]
        return self.find_by_type(trigger_type, tenant_id=tenant_id)

    def delete(self, schedule_id: str, *, tenant_ctx: TenantContext) -> bool:
        key = (tenant_ctx.tenant_id, schedule_id)
        if key not in self._data:
            return False
        del self._data[key]
        self._delete_redis_schedule(tenant_ctx.tenant_id, schedule_id)
        if self._db is not None:
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(self._db_delete_schedule(schedule_id, tenant_ctx.tenant_id))
                self._db_tasks.add(task)
                task.add_done_callback(self._db_tasks.discard)
            except RuntimeError:
                pass
        return True

    async def delete_async(self, schedule_id: str, *, tenant_ctx: TenantContext) -> bool:
        key = (tenant_ctx.tenant_id, schedule_id)
        if self._db is None:
            if key not in self._data:
                return False
        else:
            # Postgres decides existence, not the cache: a cache miss may be a
            # trigger created on another replica, and a cache HIT may be one
            # already deleted elsewhere (a 404, not a second delete). The read
            # is strict so an outage fails closed.
            rec = await self._get_for_tenant_async(tenant_ctx.tenant_id, schedule_id, strict=True)
            if rec is None:
                return False
            await self._db_delete_schedule(schedule_id, tenant_ctx.tenant_id, strict=True)
        await self._delete_redis_schedule_async(tenant_ctx.tenant_id, schedule_id, strict=True)
        self._data.pop(key, None)
        return True

    async def delete_for_agent_async(
        self, agent_id: str, *, tenant_ctx: TenantContext
    ) -> list[str]:
        """Delete every schedule of *agent_id* in Postgres, Redis and the cache (TRG-31).

        Agent deletion used to walk ``list_all`` — this replica's cache — so a
        schedule created on another replica survived and kept firing goals for
        the deleted agent. Postgres decides which rows go (one ``DELETE ...
        RETURNING`` under the tenant's RLS context). Must run BEFORE the agent
        row is deleted: ``schedules.agent_id`` is ``ON DELETE SET NULL``.
        Raises :class:`ScheduleStoreUnavailableError` on an outage.
        """
        tenant_id = tenant_ctx.tenant_id

        async def _evict(schedule_ids: list[str]) -> None:
            # The beat fires from the Redis mirror, so its keys must go too.
            try:
                for schedule_id in schedule_ids:
                    await self._delete_redis_schedule_async(tenant_id, schedule_id, strict=True)
                    self._data.pop((tenant_id, schedule_id), None)
            except Exception as exc:
                raise ScheduleStoreUnavailableError(f"schedule cache evict failed: {exc}") from exc

        if self._db is None:
            ids = [
                sid
                for (tid, sid), rec in self._data.items()
                if tid == tenant_id and rec.get("agent_id") == agent_id
            ]
            await _evict(ids)
            return ids
        try:
            from sqlalchemy import delete, select

            from app.db.models.scheduling import Schedule
            from app.db.rls import sqlalchemy_rls_context

            where = (Schedule.tenant_id == tenant_id, Schedule.agent_id == agent_id)
            # Evict the Redis keys BEFORE the rows go: if eviction fails the rows
            # are still there, so a retry finds (and evicts) them again.
            async with self._db() as session, sqlalchemy_rls_context(session, tenant_id):
                rows = await session.execute(select(Schedule.id).where(*where))
                ids = [str(r[0]) for r in rows]
        except Exception as exc:
            raise ScheduleStoreUnavailableError(f"agent schedule lookup failed: {exc}") from exc
        await _evict(ids)
        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                result = await session.execute(
                    delete(Schedule).where(*where).returning(Schedule.id)
                )
                deleted = [str(r[0]) for r in result.fetchall()]
        except Exception as exc:
            raise ScheduleStoreUnavailableError(f"agent schedule delete failed: {exc}") from exc
        # A schedule created between the lookup and the delete.
        await _evict([sid for sid in deleted if sid not in ids])
        return deleted

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

    async def _db_update_paused(self, schedule_id: str, tenant_id: str, paused: bool) -> None:
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
                    .values(
                        paused=paused,
                        **({} if paused else {"armed_at": datetime.now(UTC), "next_fire_at": None}),
                    )
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
                raise ScheduleStoreUnavailableError(f"schedule delete failed: {exc}") from exc

    async def _startup_tenant_vault(self, row: Any) -> Any:
        """The row's tenant key when it holds ``tv1:`` secrets (loaded under that
        tenant's RLS context). Unreadable → ``None``: the secret then opens to the
        unmatchable sentinel (fail closed) and the next tenant read retries."""
        from app.providers.tenant_vault import is_tenant_encrypted

        if not any(
            is_tenant_encrypted(str(getattr(row, col, "") or ""))
            for col in ("webhook_signature_secret_enc", "webhook_signature_secret_prev_enc")
        ):
            return None
        try:
            return await self._tenant_vault(str(row.tenant_id))
        except Exception as exc:
            _log.warning("tenant vault unreadable at startup tenant=%s: %s", row.tenant_id, exc)
            return None

    async def sync_from_db(self) -> int:
        """Load schedules from PostgreSQL into memory (startup warm-up).

        Returns the number of new entries loaded (skips already-present keys).
        Returns 0 immediately when no ``db_session_factory`` is configured.

        This is cross-tenant by nature, so with a ``system_db_session_factory``
        it runs on that maintenance (BYPASSRLS) factory under ``system_session``.
        It used to run on the tenant factory with no RLS context: under the
        least-privilege NOBYPASSRLS role the query saw zero rows, so every
        replica booted with an empty registry. (Without a system factory — unit
        tests, RLS-less dev DBs — the legacy plain-session query is kept.)
        """
        if self._db is None:
            return 0
        try:
            from contextlib import AsyncExitStack

            from sqlalchemy import select

            from app.db.models.scheduling import Schedule

            loaded = 0
            async with AsyncExitStack() as stack:
                if self._system_db is not None:
                    from app.db.rls import system_session

                    session = await stack.enter_async_context(self._system_db())
                    await stack.enter_async_context(session.begin())
                    await stack.enter_async_context(system_session(session))
                else:
                    session = await stack.enter_async_context(self._db())
                result = await session.execute(select(Schedule))
                rows = result.scalars().all()
                for row in rows:
                    key = (row.tenant_id, row.id)
                    if key not in self._data:
                        self._data[key] = self._record_from_row(
                            row, await self._startup_tenant_vault(row)
                        )
                        self._write_redis_schedule(row.tenant_id, self._data[key])
                        loaded += 1
            return loaded
        except Exception as exc:
            _log.warning("DB schedule sync failed: %s", exc)
            return 0


async def create_schedules_atomically(
    store: Any,
    specs: list[TriggerSpec],
    *,
    goal_id: str,
    tenant_ctx: TenantContext,
    agent_id: str = "",
    goal_template: str = "",
    quota_plan: str | None = None,
) -> list[str]:
    """Create every spec or none (TRG-10).

    NL and chat requests can yield several schedules; each create enforces
    ``PLAN_MAX_TRIGGERS`` on its own, so a quota refusal (or an outage) part way
    through used to leave the first schedules behind. On any failure the ones
    already created are deleted and the error is re-raised.
    """
    created: list[str] = []
    try:
        for spec in specs:
            schedule_id = await store.create_async(
                goal_id=goal_id,
                spec=spec,
                tenant_ctx=tenant_ctx,
                agent_id=agent_id,
                goal_template=goal_template,
                quota_plan=quota_plan,
            )
            created.append(str(schedule_id))
    except BaseException:
        for schedule_id in created:
            try:
                await store.delete_async(schedule_id, tenant_ctx=tenant_ctx)
            except Exception as exc:
                _log.warning("schedule batch rollback failed for %s: %s", schedule_id, exc)
        raise
    return created
