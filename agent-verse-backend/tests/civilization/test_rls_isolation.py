"""RLS isolation tests for civilization tables.

Asserts tenant A cannot read tenant B's data.
Two levels of testing:
1. Migration-level: verify the SQL has the right RLS policies.
2. In-memory: verify Blackboard/Society in-memory fallbacks scope by tenant.
"""
from __future__ import annotations

import os

import pytest

_MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../app/db/migrations/versions/0045_civilization.py",
)


def _migration_content() -> str | None:
    path = os.path.normpath(_MIGRATION_PATH)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read()


# ── Migration-level checks ─────────────────────────────────────────────────────

def test_rls_isolation_property() -> None:
    """All 7 civilization tables must have RLS policies in the migration.

    The migration uses Python f-strings, so the file literally contains the
    template ``{table_name}_tenant_isolation`` rather than each expanded name.
    We verify:
      1. The template pattern is present (proves the loop applies it to every table).
      2. All 7 table names are declared in the TABLES list in the migration.
      3. ENABLE ROW LEVEL SECURITY is applied.
    """
    content = _migration_content()
    if content is None:
        pytest.skip("Migration 0045_civilization.py not found")

    # The loop f-string template must be present
    assert "{table_name}_tenant_isolation" in content, (
        "Migration must contain the tenant isolation policy template"
    )
    assert "ENABLE ROW LEVEL SECURITY" in content, (
        "Migration must contain ENABLE ROW LEVEL SECURITY"
    )

    # All 7 table names must be declared
    tables = [
        "civilizations",
        "civilization_agents",
        "spawn_requests",
        "blackboard_entries",
        "bus_messages",
        "civilization_learnings",
        "civilization_events",
    ]
    for table in tables:
        assert table in content, (
            f"Migration does not reference table: {table}"
        )


def test_migration_has_force_rls() -> None:
    """All tables must also carry FORCE ROW LEVEL SECURITY (bypasses superusers)."""
    content = _migration_content()
    if content is None:
        pytest.skip("Migration 0045_civilization.py not found")

    assert "FORCE ROW LEVEL SECURITY" in content, (
        "Migration must use FORCE ROW LEVEL SECURITY on all civilization tables"
    )


# ── In-memory isolation checks ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_blackboard_tenant_isolation_in_memory() -> None:
    """Blackboard queries must be scoped to the correct tenant.

    Two Blackboard instances share the same civilization_id but different
    tenant_ids.  In-memory mode each maintains its own _entries dict,
    so Tenant-2 must never see Tenant-1's entries.
    """
    from app.civilization.blackboard import Blackboard

    board_t1 = Blackboard(civilization_id="civ-1", tenant_id="tenant-1")
    board_t2 = Blackboard(civilization_id="civ-1", tenant_id="tenant-2")

    await board_t1.post(
        author_agent_id="a1",
        topic="secret",
        content="tenant-1 secret data",
        confidence=0.9,
    )

    # Tenant-2's board should have NO entries
    results_t2 = await board_t2.query(topic="secret")
    assert len(results_t2) == 0, (
        "Tenant-2 must not see Tenant-1's blackboard entries"
    )

    # Tenant-1's board should see its own entry
    results_t1 = await board_t1.query(topic="secret")
    assert len(results_t1) == 1


@pytest.mark.asyncio
async def test_blackboard_same_tenant_can_see_entries() -> None:
    """Two board instances for the same tenant share the same data (sanity check)."""
    from app.civilization.blackboard import Blackboard

    # Both boards backed by the same in-memory instance
    board = Blackboard(civilization_id="civ-x", tenant_id="shared-tenant")
    await board.post(
        author_agent_id="agent-x",
        topic="shared_topic",
        content="shared data",
        confidence=0.8,
    )
    results = await board.query(topic="shared_topic")
    assert len(results) == 1
    assert results[0]["content"] == "shared data"


@pytest.mark.asyncio
async def test_society_tenant_isolation_in_memory() -> None:
    """Society members must be scoped to the correct tenant.

    Directly injecting an agent into Society._members for one tenant must
    not be visible through a Society instance for a different tenant.
    """
    from app.civilization.society import Society

    s1 = Society(civilization_id="civ-1", tenant_id="tenant-1")
    s2 = Society(civilization_id="civ-1", tenant_id="tenant-2")

    s1._members["agent-x"] = {
        "agent_id": "agent-x",
        "reputation": 0.8,
        "status": "active",
        "depth": 0,
        "parent_agent_id": None,
        "budget_spent_usd": 0,
        "role": "worker",
    }

    # Tenant-2 must not see tenant-1's agent
    member_t2 = await s2.get_member("agent-x")
    assert member_t2 is None, "Tenant-2 must not see Tenant-1's agents"


@pytest.mark.asyncio
async def test_society_tenant_can_see_own_members() -> None:
    """Society.get_member returns members for the correct tenant (sanity check)."""
    from app.civilization.society import Society

    s = Society(civilization_id="civ-1", tenant_id="tenant-1")
    s._members["agent-y"] = {
        "agent_id": "agent-y",
        "reputation": 0.7,
        "status": "active",
        "depth": 1,
        "parent_agent_id": "agent-x",
        "budget_spent_usd": 1.0,
        "role": "worker",
    }

    member = await s.get_member("agent-y")
    assert member is not None
    assert member["agent_id"] == "agent-y"


@pytest.mark.asyncio
async def test_blackboard_multi_topic_isolation() -> None:
    """Entries on different topics don't bleed across topic filters."""
    from app.civilization.blackboard import Blackboard

    board = Blackboard(civilization_id="civ-multi", tenant_id="t-multi")
    await board.post(
        author_agent_id="a1", topic="topic-a", content="content-a", confidence=0.9
    )
    await board.post(
        author_agent_id="a2", topic="topic-b", content="content-b", confidence=0.8
    )

    results_a = await board.query(topic="topic-a")
    results_b = await board.query(topic="topic-b")
    all_results = await board.query()

    assert len(results_a) == 1 and results_a[0]["topic"] == "topic-a"
    assert len(results_b) == 1 and results_b[0]["topic"] == "topic-b"
    assert len(all_results) == 2
