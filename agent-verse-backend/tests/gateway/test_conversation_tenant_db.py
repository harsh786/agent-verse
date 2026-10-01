"""ID-ONLY-LOOKUPS: gateway conversation context is read for the caller's tenant only.

``ConversationManager.get_context`` read ``gateway_conversations`` by id alone
and never set the tenant RLS context: on the NOBYPASSRLS application role it
found nothing, on a SUPERUSER / BYPASSRLS role it returned any tenant's turns.
It (and ``get_or_create`` / ``add_turn``) now scope the session to the tenant
and filter on ``tenant_id`` explicitly.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.app_role import AppRoleSpec, ensure_app_role
from app.gateway.conversation import ConversationManager

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def seeded(pg_url: str) -> AsyncIterator[dict[str, Any]]:
    spec = AppRoleSpec(role=f"gw_app_{uuid.uuid4().hex[:6]}", password="gw-pw")
    owner = create_async_engine(pg_url)
    async with owner.connect() as conn:
        await conn.run_sync(ensure_app_role, spec)
        await conn.commit()
    a, b, conv = uuid.uuid4().hex, uuid.uuid4().hex, str(uuid.uuid4())
    turn = {"role": "user", "content": "secret-of-a", "timestamp": "t", "channel": "rest"}
    async with owner.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO gateway_conversations (id, tenant_id, channel, turns) "
                "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), 'rest', CAST(:turns AS jsonb))"
            ),
            {"id": conv, "tid": a, "turns": json.dumps([turn])},
        )
    app = create_async_engine(
        make_url(pg_url)
        .set(username=spec.role, password=spec.password)
        .render_as_string(hide_password=False)
    )
    yield {
        "a": a,
        "b": b,
        "conv": conv,
        "superuser": async_sessionmaker(owner, expire_on_commit=False),
        "app": async_sessionmaker(app, expire_on_commit=False),
    }
    await app.dispose()
    await owner.dispose()


@pytest.mark.parametrize("role", ["app", "superuser"])
async def test_get_context_is_tenant_scoped(seeded: dict[str, Any], role: str) -> None:
    factory = seeded[role]
    async with factory() as session:
        mine = await ConversationManager(session).get_context(
            seeded["conv"], tenant_id=seeded["a"]
        )
    assert [t.content for t in mine] == ["secret-of-a"]
    async with factory() as session:
        theirs = await ConversationManager(session).get_context(
            seeded["conv"], tenant_id=seeded["b"]
        )
    assert theirs == []


async def test_add_turn_works_on_the_least_privilege_role_and_never_crosses(
    seeded: dict[str, Any],
) -> None:
    async with seeded["app"]() as session:
        await ConversationManager(session).add_turn(
            seeded["conv"], seeded["a"], "assistant", "reply", "rest"
        )
    for factory in (seeded["app"], seeded["superuser"]):
        async with factory() as session:
            await ConversationManager(session).add_turn(
                seeded["conv"], seeded["b"], "user", "hijack", "rest"
            )
    async with seeded["superuser"]() as session:
        turns = await ConversationManager(session).get_context(
            seeded["conv"], tenant_id=seeded["a"]
        )
    assert [t.content for t in turns] == ["secret-of-a", "reply"]
