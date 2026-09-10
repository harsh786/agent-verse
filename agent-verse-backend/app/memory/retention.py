"""Retention-policy → TTL resolution for canonical memory records.

Every :class:`~app.memory.contracts.MemoryRecord` carries a
``retention_policy_id``.  This module maps that policy to an absolute
``expires_at`` deadline **at write time** so the (kind-agnostic) retention purge
paths can physically reclaim expired rows.

Why this exists — the TTL gap it closes
----------------------------------------
Both purge paths already delete uniformly across every ``memory_kind``:

* :meth:`app.memory.repository.InMemoryMemoryRepository.purge_expired` /
  :meth:`app.memory.postgres_repository.PostgresMemoryRepository.purge_expired`
  (driven by ``app.scaling.memory_tasks.purge_expired_memories``), and
* ``app.scaling.tasks._delete_expired_records`` — the scheduled
  ``DELETE FROM memory_records WHERE expires_at IS NOT NULL AND expires_at < NOW()``.

Both, however, only remove rows **whose ``expires_at`` is set**.  Previously the
``write()`` paths never assigned ``expires_at`` (it was hard-coded ``None``), so
*no record of any kind ever expired* and both purges reclaimed nothing.  Applying
this resolver at write time makes the retention deadline real and uniform across
episodic, procedural, reflexion, execution, long_term, and every other kind.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# Policies that intentionally never expire (retained until an explicit erasure
# request or a GDPR sweep). ``resolve_expires_at`` returns ``None`` for these.
PERMANENT_POLICIES: frozenset[str] = frozenset(
    {"permanent", "keep-forever", "legal-hold", "none"}
)

# Explicit per-policy retention windows, in days. Any policy not listed here
# falls back to ``DEFAULT_RETENTION_DAYS`` so a finite TTL is *always* assigned
# (fail-closed against unbounded growth) unless the policy is explicitly
# permanent above.
RETENTION_POLICY_DAYS: dict[str, int] = {
    "default": 90,
    "standard": 90,
    "reflexion-standard": 180,
    "episodic-standard": 180,
    "procedural-standard": 365,
    "execution-standard": 30,
    "long_term-standard": 365,
    "knowledge_graph-standard": 365,
    "prospective-standard": 30,
    "compatibility-backfill-v1": 365,
    "ephemeral": 1,
    "session": 1,
}

DEFAULT_RETENTION_DAYS = 90


def retention_days(policy_id: str) -> int | None:
    """Return the retention window in days for ``policy_id``.

    ``None`` means "never expires" (a permanent policy). Unknown policies fall
    back to :data:`DEFAULT_RETENTION_DAYS` so retention is always bounded.
    """
    if policy_id in PERMANENT_POLICIES:
        return None
    return RETENTION_POLICY_DAYS.get(policy_id, DEFAULT_RETENTION_DAYS)


def resolve_expires_at(policy_id: str, created_at: datetime) -> datetime | None:
    """Compute the absolute expiry deadline for a record written at ``created_at``.

    Returns ``None`` for permanent policies (record never expires). Applied
    uniformly across every ``memory_kind`` by both repository ``write()`` paths.
    """
    days = retention_days(policy_id)
    if days is None:
        return None
    return created_at + timedelta(days=days)


__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "PERMANENT_POLICIES",
    "RETENTION_POLICY_DAYS",
    "resolve_expires_at",
    "retention_days",
]
