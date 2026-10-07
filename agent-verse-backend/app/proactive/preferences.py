"""Stored proactive-outreach preferences — the opt-in record (a10-F227-02).

Proactive outreach is OPT-IN: a principal is only ever contacted after a record
for it exists in ``proactive_preferences`` (tenant-scoped, FORCE RLS). The record
holds the consent controls ``app.chat.proactive.evaluate_proactive`` applies:
``enabled``, the allowed channels, quiet hours and the principal's timezone, and
``max_per_day`` (enforced by the shared daily cap). Deleting the record opts the
principal out entirely.

Before this, the wired engine had no preferences provider, so every principal got
the permissive default (enabled, no quiet hours) and nothing a principal or
operator chose was stored anywhere.

The store resolves ``app.state.db_session_factory`` on every call (the lifespan
binds it after the routers are built). Without one (unit tests, a dev run without
Postgres) it keeps records in memory. DB errors propagate: the engine then sends
nothing (``preferences_unavailable``) and the API answers 503.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.chat.proactive import ProactivePreferences

_TABLE = "proactive_preferences"
_COLUMNS = "enabled, channels, quiet_start, quiet_end, timezone, max_per_day"


def _from_row(row: Any) -> ProactivePreferences:
    quiet = None
    if row.quiet_start is not None and row.quiet_end is not None:
        quiet = (int(row.quiet_start), int(row.quiet_end))
    return ProactivePreferences(
        enabled=bool(row.enabled),
        quiet_hours=quiet,
        max_per_day=int(row.max_per_day),
        channels=frozenset(str(c) for c in (row.channels or [])),
        timezone=str(row.timezone or "UTC"),
    )


class ProactivePreferencesStore:
    def __init__(self, state: Any = None) -> None:
        self._state = state
        self._memory: dict[tuple[str, str], tuple[ProactivePreferences, str | None]] = {}

    def _db(self) -> Any:
        return getattr(self._state, "db_session_factory", None) if self._state else None

    async def get(self, tenant_id: str, principal_id: str) -> ProactivePreferences | None:
        """The principal's stored preferences, or None when it never opted in."""
        db = self._db()
        if db is None:
            found = self._memory.get((tenant_id, principal_id))
            return found[0] if found else None
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
            row = (
                await session.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM {_TABLE} "
                        "WHERE tenant_id = :t AND principal_id = :p"
                    ),
                    {"t": tenant_id, "p": principal_id},
                )
            ).first()
        return None if row is None else _from_row(row)

    async def put(
        self,
        tenant_id: str,
        principal_id: str,
        prefs: ProactivePreferences,
        *,
        updated_by: str | None = None,
    ) -> None:
        db = self._db()
        if db is None:
            self._memory[(tenant_id, principal_id)] = (prefs, updated_by)
            return
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        quiet = prefs.quiet_hours
        params = {
            "t": tenant_id,
            "p": principal_id,
            "enabled": prefs.enabled,
            "channels": sorted(prefs.channels),
            "qs": quiet[0] if quiet else None,
            "qe": quiet[1] if quiet else None,
            "tz": prefs.timezone,
            "max": prefs.max_per_day,
            "by": updated_by,
            "now": datetime.now(UTC),
        }
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
            await session.execute(
                text(
                    f"INSERT INTO {_TABLE} (tenant_id, principal_id, {_COLUMNS}, "
                    "updated_by, updated_at) VALUES "
                    "(:t, :p, :enabled, :channels, :qs, :qe, :tz, :max, :by, :now) "
                    "ON CONFLICT (tenant_id, principal_id) DO UPDATE SET "
                    "enabled = EXCLUDED.enabled, channels = EXCLUDED.channels, "
                    "quiet_start = EXCLUDED.quiet_start, quiet_end = EXCLUDED.quiet_end, "
                    "timezone = EXCLUDED.timezone, max_per_day = EXCLUDED.max_per_day, "
                    "updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at"
                ),
                params,
            )

    async def delete(self, tenant_id: str, principal_id: str) -> bool:
        """Opt the principal out entirely; False when it had no record."""
        db = self._db()
        if db is None:
            return self._memory.pop((tenant_id, principal_id), None) is not None
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
            result = await session.execute(
                text(f"DELETE FROM {_TABLE} WHERE tenant_id = :t AND principal_id = :p"),
                {"t": tenant_id, "p": principal_id},
            )
        return bool(getattr(result, "rowcount", 0))
