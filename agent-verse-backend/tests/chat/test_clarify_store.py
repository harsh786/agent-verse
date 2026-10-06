"""The chat clarify-round cap is shared across replicas and restarts (Redis), not per process."""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis

from app.chat.clarify_store import (
    DEFAULT_CLARIFY_TTL_SECONDS,
    InMemoryClarifyRoundStore,
    RedisClarifyRoundStore,
)
from app.chat.intent import IntentRouter
from app.chat.service import ChatService

UNDERSPECIFIED = "Deploy it"  # goal verb, no target -> CLARIFY until the cap


async def test_two_replicas_share_one_clarify_streak() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store_a = RedisClarifyRoundStore(redis)
    store_b = RedisClarifyRoundStore(redis)
    replica_a = ChatService()
    replica_b = ChatService()
    replica_a.attach_engine(clarify_store=store_a)
    replica_b.attach_engine(clarify_store=store_b)

    session = await replica_a.acreate_session("t1")
    # Replica B serves the same session (the in-memory session store is per
    # instance here, so mirror the row; in production both read Postgres).
    replica_b._sessions[session.id] = session
    replica_b._messages = replica_a._messages

    intents = []
    for i in range(IntentRouter.MAX_CLARIFY_ROUNDS + 1):
        svc = replica_a if i % 2 == 0 else replica_b
        res = await svc.adispatch(session.id, "t1", UNDERSPECIFIED)
        intents.append(res["intent"])

    # Three clarifying rounds total across BOTH replicas, then the cap forces GOAL.
    assert intents == ["CLARIFY"] * IntentRouter.MAX_CLARIFY_ROUNDS + ["GOAL"]


async def test_restart_keeps_the_streak() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    before = ChatService()
    before.attach_engine(clarify_store=RedisClarifyRoundStore(redis))
    session = await before.acreate_session("t1")
    for _ in range(IntentRouter.MAX_CLARIFY_ROUNDS):
        assert (await before.adispatch(session.id, "t1", UNDERSPECIFIED))["intent"] == "CLARIFY"

    after = ChatService()  # a fresh process with the same Redis
    after.attach_engine(clarify_store=RedisClarifyRoundStore(redis))
    after._sessions[session.id] = session
    after._messages = before._messages
    assert (await after.adispatch(session.id, "t1", UNDERSPECIFIED))["intent"] == "GOAL"


async def test_redis_key_has_ttl_and_resets_on_resolved_turn() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = RedisClarifyRoundStore(redis)
    assert await store.increment("t1", "s1") == 1
    assert await store.increment("t1", "s1") == 2
    key = "agentverse:chat:clarify:t1:s1"
    ttl = await redis.ttl(key)
    assert 0 < ttl <= DEFAULT_CLARIFY_TTL_SECONDS
    # Tenant-scoped: another tenant's session with the same id is independent.
    assert await store.get("t2", "s1") == 0
    await store.reset("t1", "s1")
    assert await store.get("t1", "s1") == 0
    assert await redis.exists(key) == 0


async def test_resolved_turn_clears_the_shared_streak() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    svc = ChatService()
    svc.attach_engine(clarify_store=RedisClarifyRoundStore(redis))
    session = await svc.acreate_session("t1")
    assert (await svc.adispatch(session.id, "t1", UNDERSPECIFIED))["intent"] == "CLARIFY"
    assert await redis.get(f"agentverse:chat:clarify:t1:{session.id}") == "1"
    res = await svc.adispatch(session.id, "t1", "what is the capital of France?")
    assert res["intent"] == "QA"
    assert await redis.get(f"agentverse:chat:clarify:t1:{session.id}") is None


class _BrokenRedis:
    def __getattr__(self, name: str) -> Any:
        raise ConnectionError("redis down")


async def test_redis_error_degrades_without_failing_the_turn() -> None:
    store = RedisClarifyRoundStore(_BrokenRedis())
    assert await store.get("t1", "s1") == 0
    assert await store.increment("t1", "s1") == 1
    assert await store.get("t1", "s1") == 1  # local degrade keeps the cap in-replica
    await store.reset("t1", "s1")
    assert await store.get("t1", "s1") == 0


async def test_default_store_is_in_memory_when_no_redis_is_wired() -> None:
    svc = ChatService()
    assert isinstance(svc._clarify_store, InMemoryClarifyRoundStore)
    session = await svc.acreate_session("t1")
    intents = [
        (await svc.adispatch(session.id, "t1", UNDERSPECIFIED))["intent"]
        for _ in range(IntentRouter.MAX_CLARIFY_ROUNDS + 1)
    ]
    assert intents[-1] == "GOAL"
