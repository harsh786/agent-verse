"""PostgresPersonalizationStore — durable per-principal profiles (Phase 11).

Backs the personalization layer with the ``chat_personal_profiles`` table
(migration 0131) so tone / standing instructions / preferences persist across
sessions, restarts and channels. RLS tenant context + explicit tenant filter.

The ``principal_id`` is ``tenant_id`` (or ``tenant:user``); we derive the tenant
from it so the store matches the ``PersonalizationStore`` protocol (which passes
only ``principal_id``).
"""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.personalization import PersonalProfile
from app.db.rls import sqlalchemy_rls_context


def _tenant_of(principal_id: str) -> str:
    """The tenant a principal belongs to (principal_id is ``tenant`` or ``tenant:user``)."""
    return principal_id.split(":", 1)[0]


class PostgresPersonalizationStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self, principal_id: str) -> PersonalProfile | None:
        tenant_id = _tenant_of(principal_id)
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        "SELECT * FROM chat_personal_profiles "
                        "WHERE principal_id = :p AND tenant_id = :t"
                    ),
                    {"p": principal_id, "t": tenant_id},
                )
            ).mappings().one_or_none()
            if row is None:
                return None
            return PersonalProfile(
                principal_id=principal_id,
                tone=row["tone"],
                standing_instructions=list(row["standing_instructions"] or []),
                preferences=dict(row["preferences"] or {}),
                updated_at=row["updated_at"],
            )

    async def _ensure(self, principal_id: str) -> PersonalProfile:
        prof = await self.get(principal_id)
        return prof or PersonalProfile(principal_id=principal_id)

    async def _upsert(self, prof: PersonalProfile) -> None:
        tenant_id = _tenant_of(prof.principal_id)
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO chat_personal_profiles "
                    "(principal_id, tenant_id, tone, standing_instructions, preferences, "
                    " updated_at) VALUES (:p, :t, :tone, CAST(:si AS jsonb), "
                    "CAST(:pref AS jsonb), now()) "
                    "ON CONFLICT (principal_id) DO UPDATE SET "
                    "tone = EXCLUDED.tone, "
                    "standing_instructions = EXCLUDED.standing_instructions, "
                    "preferences = EXCLUDED.preferences, updated_at = now()"
                ),
                {
                    "p": prof.principal_id,
                    "t": tenant_id,
                    "tone": prof.tone,
                    "si": json.dumps(prof.standing_instructions),
                    "pref": json.dumps(prof.preferences),
                },
            )

    async def add_standing_instruction(
        self, principal_id: str, instruction: str
    ) -> PersonalProfile:
        prof = await self._ensure(principal_id)
        lowered = instruction.strip().lower()
        prof.standing_instructions = [
            s for s in prof.standing_instructions if s.strip().lower() != lowered
        ]
        prof.standing_instructions.append(instruction.strip())
        await self._upsert(prof)
        return prof

    async def set_tone(self, principal_id: str, tone: str) -> PersonalProfile:
        prof = await self._ensure(principal_id)
        prof.tone = tone.strip() or None
        await self._upsert(prof)
        return prof

    async def set_preference(
        self, principal_id: str, key: str, value: str
    ) -> PersonalProfile:
        prof = await self._ensure(principal_id)
        prof.preferences[key.strip()] = value.strip()
        await self._upsert(prof)
        return prof
