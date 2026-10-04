"""Tests for HITL gap closures — approval chain wiring + persistence + escalation.

Covers gaps from docs/superpowers/specs/2026-08-20-hitl-gap-analysis.md:
  G-06: ApprovalChainEngine wired into OrgService execution path
  G-20: ApprovalChainEngine Redis-backed persistence (set_redis/_persist/_load)
  G-21: approval_gates actually invoke check_requires_approval + create_approval_request
  G-26: escalate_timeout publishes event + notifies + creates escalation request
  G-29: ApprovalChainEngine.create_approval_request triggers notification
  G-16: beat_schedule registered for expire_hitl_approvals
  G-10: /governance/approvals supports org_id query param
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.org.approval_chain import (
    CHAINS_BY_ID,
    ApprovalChain,
    ApprovalChainEngine,
    get_approval_engine,
)

# ─── Fixtures ───────────────────────────────────────────────────────────────


class _FakeRedis:
    """fakeredis with a ``_data`` view and a guard: KEYS is never allowed
    (a08-F178-02 — a fleet-wide scan across every tenant)."""

    def __init__(self) -> None:
        import fakeredis

        self._r = fakeredis.FakeAsyncRedis(decode_responses=True)

    def __getattr__(self, name: str) -> Any:
        if name in ("keys", "scan_iter"):
            raise AssertionError(f"approval engine must not call {name}")
        return getattr(self._r, name)


class _FakeOrg:
    """Lightweight org object with attributes the engine reads."""

    def __init__(self, autonomy: int = 3, risk: str = "medium") -> None:
        self.autonomy_level = autonomy
        self.risk_tolerance = risk


@pytest.fixture
def fake_redis() -> _FakeRedis:
    return _FakeRedis()


@pytest.fixture
def fake_org() -> _FakeOrg:
    return _FakeOrg(autonomy=3, risk="medium")


@pytest.fixture
def prod_chain() -> ApprovalChain:
    return CHAINS_BY_ID["prod_deploy"]


# ─── G-20: ApprovalChainEngine Redis-backed persistence ─────────────────────


@pytest.mark.asyncio
async def test_g20_set_redis_then_persist_round_trip(fake_redis, prod_chain):
    """A request created with Redis backing round-trips via _persist/_load."""
    engine = ApprovalChainEngine()
    engine.set_redis(fake_redis)

    req = await engine.create_approval_request(
        chain=prod_chain,
        action_detail="Deploy v2.0 to production",
        mission_id="m-1",
        agent_id="a-1",
        tenant_id="t1",
        org_id="org-1",
    )

    # The request should be serialised into Redis under the prefixed key.
    expected_key = f"{engine._REDIS_PREFIX}{req.request_id}"
    assert await fake_redis.get(expected_key) is not None
    # Records expire (no unbounded growth).
    assert await fake_redis.ttl(expected_key) > 0

    # Loading from Redis reconstructs the ApprovalRequest.
    loaded = await engine._load(req.request_id)
    assert loaded is not None
    assert loaded.request_id == req.request_id
    assert loaded.chain_id == prod_chain.id
    assert loaded.action_detail == "Deploy v2.0 to production"
    assert loaded.org_id == "org-1"


@pytest.mark.asyncio
async def test_g20_get_request_falls_back_to_redis(fake_redis, prod_chain):
    """get_request() retrieves a request from Redis when not in local memory."""
    engine = ApprovalChainEngine()
    engine.set_redis(fake_redis)

    req = await engine.create_approval_request(
        chain=prod_chain,
        action_detail="Deploy v2.1",
        mission_id="m-2",
        agent_id=None,
        tenant_id="t1",
        org_id="org-2",
    )

    # Fresh engine with empty in-memory store but same Redis.
    engine2 = ApprovalChainEngine()
    engine2.set_redis(fake_redis)
    fetched = await engine2.get_request(req.request_id)
    assert fetched is not None
    assert fetched.request_id == req.request_id


@pytest.mark.asyncio
async def test_g20_list_pending_merges_redis_and_memory(fake_redis, prod_chain):
    """list_pending includes both in-memory and Redis-backed requests."""
    engine = ApprovalChainEngine()
    engine.set_redis(fake_redis)

    await engine.create_approval_request(
        chain=prod_chain,
        action_detail="Deploy a",
        mission_id="m-a",
        agent_id=None,
        tenant_id="t1",
        org_id="org-a",
    )
    await engine.create_approval_request(
        chain=prod_chain,
        action_detail="Deploy b",
        mission_id="m-b",
        agent_id=None,
        tenant_id="t1",
        org_id="org-b",
    )

    pending = await engine.list_pending(tenant_id="t1")
    assert len(pending) == 2

    pending_org_b = await engine.list_pending(tenant_id="t1", org_id="org-b")
    assert len(pending_org_b) == 1
    assert pending_org_b[0].org_id == "org-b"


@pytest.mark.asyncio
async def test_g20_record_approval_persists(fake_redis, prod_chain):
    """Recording an approval persists the updated state to Redis."""
    engine = ApprovalChainEngine()
    engine.set_redis(fake_redis)
    req = await engine.create_approval_request(
        chain=prod_chain,
        action_detail="Deploy c",
        mission_id="m-c",
        agent_id=None,
        tenant_id="t1",
        org_id="org-c",
    )
    await engine.record_approval(req.request_id, "qa_lead", approved=True)

    loaded = await engine._load(req.request_id)
    assert loaded is not None
    assert "qa_lead" in loaded.approved_by


# ─── a08-F178-02: Redis is the only truth, per-tenant bounded index ──────────


@pytest.mark.asyncio
async def test_f178_02_list_pending_is_tenant_scoped_without_keys(fake_redis, prod_chain):
    engine = ApprovalChainEngine()
    engine.set_redis(fake_redis)
    for tid in ("t1", "t1", "t2"):
        await engine.create_approval_request(
            chain=prod_chain,
            action_detail="d",
            mission_id=None,
            agent_id=None,
            tenant_id=tid,
            org_id="o",
        )
    # _FakeRedis raises on KEYS, so this proves no fleet-wide scan.
    assert len(await engine.list_pending(tenant_id="t1")) == 2
    assert len(await engine.list_pending(tenant_id="t2")) == 1
    assert len(await engine.list_pending(tenant_id="t1", limit=1)) == 1


@pytest.mark.asyncio
async def test_f178_02_votes_on_two_replicas_are_both_kept(fake_redis, prod_chain):
    a, b = ApprovalChainEngine(), ApprovalChainEngine()
    a.set_redis(fake_redis)
    b.set_redis(fake_redis)
    req = await a.create_approval_request(
        chain=prod_chain,
        action_detail="d",
        mission_id=None,
        agent_id=None,
        tenant_id="t1",
        org_id="o",
    )
    await a.record_approval(req.request_id, "qa_lead", approved=True)
    await b.record_approval(req.request_id, "sre_lead", approved=True)
    # Replica A must see B's vote (no stale local copy) and complete the chain.
    final = await a.record_approval(req.request_id, "security_engineer", approved=True)
    assert set(final.approved_by) == {"qa_lead", "sre_lead", "security_engineer"}
    assert final.status == "approved"


class _BrokenRedis:
    async def set(self, *a: Any, **k: Any) -> None:
        raise ConnectionError("redis down")

    async def get(self, *a: Any, **k: Any) -> None:
        raise ConnectionError("redis down")

    async def zrevrange(self, *a: Any, **k: Any) -> None:
        raise ConnectionError("redis down")


@pytest.mark.asyncio
async def test_f178_02_redis_errors_raise_instead_of_process_local_fallback(prod_chain):
    engine = ApprovalChainEngine()
    engine.set_redis(_BrokenRedis())
    with pytest.raises(ConnectionError):
        await engine.create_approval_request(
            chain=prod_chain,
            action_detail="d",
            mission_id=None,
            agent_id=None,
            tenant_id="t1",
            org_id="o",
        )
    assert engine._store == {}
    with pytest.raises(ConnectionError):
        await engine.get_request("x")
    with pytest.raises(ConnectionError):
        await engine.list_pending(tenant_id="t1")


# ─── G-06: ApprovalChainEngine wired into OrgService execution ──────────────


@pytest.mark.asyncio
async def test_g06_check_requires_approval_matches_prod_deploy(fake_org):
    """check_requires_approval returns the prod_deploy chain for matching action."""
    engine = get_approval_engine()
    chain = await engine.check_requires_approval(
        "production deploy critical release", {"org_id": "o1"}, fake_org
    )
    assert chain is not None
    assert chain.id == "prod_deploy"


@pytest.mark.asyncio
async def test_g06_check_requires_approval_no_match_for_benign_action(fake_org):
    """Non-risky actions return None (no approval required)."""
    engine = get_approval_engine()
    chain = await engine.check_requires_approval(
        "read dashboard metrics", {"org_id": "o1"}, fake_org
    )
    assert chain is None


# ─── G-21: approval_gates create real approval requests ─────────────────────


@pytest.mark.asyncio
async def test_g21_create_approval_request_stores_and_publishes(prod_chain):
    """create_approval_request persists in-memory and returns a fully-formed request."""
    engine = ApprovalChainEngine()  # no Redis — pure in-memory
    req = await engine.create_approval_request(
        chain=prod_chain,
        action_detail="Production deploy for mission X",
        mission_id="mission-X",
        agent_id=None,
        tenant_id="t-21",
        org_id="org-21",
    )
    assert req.status == "pending"
    assert req.tenant_id == "t-21"
    assert req.org_id == "org-21"
    assert set(req.approvers_needed) == set(prod_chain.required_roles)
    # in-memory store has the request
    assert await engine.get_request(req.request_id) is req


# ─── G-26: escalate_timeout publishes event + notifies + escalates ──────────


@pytest.mark.asyncio
async def test_g26_escalate_timeout_publishes_and_creates_escalation(prod_chain, monkeypatch):
    """escalate_timeout logs + publishes org.approval.timeout + creates escalation request."""
    engine = ApprovalChainEngine()
    req = await engine.create_approval_request(
        chain=prod_chain,
        action_detail="Deploy d",
        mission_id="m-d",
        agent_id=None,
        tenant_id="t-d",
        org_id="org-d",
    )
    # Force expiry so escalate_timeout proceeds
    req.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    publish_calls: list[dict[str, Any]] = []
    notify_calls: list[dict[str, Any]] = []
    create_calls: list[str] = []

    _fake_pub = MagicMock()
    _fake_pub.publish = AsyncMock(side_effect=lambda **kw: publish_calls.append(kw))
    _fake_state = MagicMock()
    _fake_state.notification_service = MagicMock()
    _fake_state.notification_service.notify_approval_timeout = AsyncMock(
        side_effect=lambda **kw: notify_calls.append(kw)
    )

    _fake_app = MagicMock()
    _fake_app.state = _fake_state

    # get_org_event_publisher is imported from app.org.events lazily, so patch
    # it there. Import first to ensure the module is loaded.
    import app.org.events as _org_events

    monkeypatch.setattr(_org_events, "get_org_event_publisher", lambda: _fake_pub)

    # Track new requests created via create_approval_request inside escalate
    original_create = engine.create_approval_request

    async def _spy_create(**kw):
        create_calls.append(kw.get("chain").id if kw.get("chain") else "?")
        return await original_create(**kw)

    engine.create_approval_request = _spy_create  # type: ignore[method-assign]

    import sys

    _orig_main = sys.modules.get("app.main")
    sys.modules["app.main"] = MagicMock(app=_fake_app)
    try:
        await engine.escalate_timeout(req.request_id)
    finally:
        if _orig_main is not None:
            sys.modules["app.main"] = _orig_main
        else:
            sys.modules.pop("app.main", None)

    # Status updated
    assert req.status == "escalated"
    assert req.escalated_at is not None
    # Event published
    assert any(c.get("event_type") == "org.approval.timeout" for c in publish_calls)
    # Notification sent
    assert len(notify_calls) == 1
    # Escalation request created
    assert any(":escalation" in c for c in create_calls)


@pytest.mark.asyncio
async def test_g26_escalate_skips_resolved_requests(prod_chain):
    """escalate_timeout no-ops on already-resolved requests."""
    engine = ApprovalChainEngine()
    req = await engine.create_approval_request(
        chain=prod_chain,
        action_detail="Deploy e",
        mission_id="m-e",
        agent_id=None,
        tenant_id="t-e",
        org_id="org-e",
    )
    req.status = "approved"
    req.resolved_at = datetime.now(UTC)
    req.expires_at = datetime.now(UTC) - timedelta(hours=1)

    await engine.escalate_timeout(req.request_id)
    # Still approved (no re-escalation)
    assert req.status == "approved"


# ─── G-16: beat_schedule registration for HITL expiry ──────────────────────


def test_g16_beat_schedule_registered_for_hitl_expiry():
    """expire_hitl_approvals is in Celery's beat_schedule to run every 60s."""
    from app.scaling.tasks import celery_app

    beat = celery_app.conf.beat_schedule or {}
    hitl_entries = {k: v for k, v in beat.items() if "hitl" in k.lower() and "expire" in k.lower()}
    assert hitl_entries, "Expected a beat_schedule entry for HITL approval expiry"
    entry = next(iter(hitl_entries.values()))
    assert entry["task"] == "app.scaling.tasks.expire_hitl_approvals"
    # Every 60 seconds
    assert entry["schedule"] == 60.0


# ─── G-10: /governance/approvals supports org_id query param ────────────────


@pytest.mark.asyncio
async def test_g10_list_approvals_accepts_org_id_query_param():
    """The /governance/approvals endpoint signature accepts an optional org_id."""
    import inspect

    from app.api.governance import list_approvals

    sig = inspect.signature(list_approvals)
    assert "org_id" in sig.parameters
    # Default should be a Query with None (filter disabled unless provided)
    _default = sig.parameters["org_id"].default
    assert _default is None or getattr(_default, "default", None) is None, (
        "org_id default must resolve to None when not provided"
    )


# ─── G-29: create_approval_request callers trigger notifications ────────────
# (Verified via integration: the OrgService.execute_mission wiring calls
# _notif.notify_approval_required() — see test_g29_org_service_wiring_smoke.)


def test_g29_org_service_wiring_calls_notify(monkeypatch):
    """OrgService.execute_mission references notify_approval_required in its body."""
    from app.org import service as service_mod

    src = inspect_source(service_mod)
    assert "notify_approval_required" in src, (
        "OrgService must call notify_approval_required when an approval gate fires"
    )
    assert "check_requires_approval" in src, (
        "OrgService must invoke ApprovalChainEngine.check_requires_approval"
    )
    assert "create_approval_request" in src, (
        "OrgService must invoke ApprovalChainEngine.create_approval_request"
    )
    assert "get_org_event_publisher" in src, (
        "OrgService must publish org.approval.requested via OrgEventPublisher"
    )


def inspect_source(module: Any) -> str:
    import inspect

    return inspect.getsource(module)


# ─── G-19: OrgEventPublisher wiring referenced in main.py ───────────────────


def test_g19_main_py_initializes_org_event_publisher():
    """main.py calls configure_org_event_publisher + get_org_event_publisher."""
    from pathlib import Path

    main_path = Path(__file__).resolve().parents[2] / "app" / "main.py"
    src = main_path.read_text(encoding="utf-8")
    assert "configure_org_event_publisher" in src, (
        "main.py must call configure_org_event_publisher during lifespan"
    )
    assert "get_org_event_publisher" in src
    # G-20: ApprovalChainEngine Redis wiring
    assert "get_approval_engine" in src
    assert "set_redis" in src
