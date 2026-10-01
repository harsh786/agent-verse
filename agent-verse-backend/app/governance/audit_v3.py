"""
Audit v3 — World-Class Immutable Audit System
===============================================
Improvements over v2:
1. Tool args + actor + IP + metadata ALL included in hash (was partial)
2. Audit export API (JSON/CSV) for compliance evidence
3. Tamper-verification endpoint (recomputes and checks chain integrity)
4. Delegation lineage in every record
5. Single-writer lock to prevent chain forks

Hash input (canonical JSON, deterministic):
{
  "previous_hash": "...",
  "timestamp": "ISO8601",
  "tenant_id": "...",
  "goal_id": "...",
  "action": "...",
  "tool_name": "...",
  "tool_args_hash": "sha256(sorted(tool_args))",  # hash of args, not raw args
  "actor": "user:alice | agent:xyz | system",
  "actor_ip": "...",
  "delegation_chain_hash": "sha256(chain)",
  "metadata_hash": "sha256(metadata)"
}
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

_AGENT_PATTERN_AUDIT_ACTIONS = frozenset(
    {
        "approval_issued",
        "approval_consumed",
        "policy_compiled",
        "budget_mutated",
        "replay_requested",
        "redrive_requested",
        "key_rotated",
        "context_disclosed",
        "memory_quarantined",
        "memory_deleted",
        "bid_unseal",
        "governor_authority_changed",
        "feature_flag_changed",
        "rollout_decided",
        "audit_accessed",
        "break_glass",
    }
)


@dataclass
class AuditRecord:
    """A single audit record with full context for tamper-proof chain."""

    id: str
    tenant_id: str
    goal_id: str
    action: str
    tool_name: str
    tool_args_hash: str  # sha256 of sorted tool args (not raw args — privacy)
    actor: str  # "user:alice", "agent:xyz", "system"
    actor_ip: str
    delegation_chain_hash: str
    previous_hash: str
    entry_hash: str  # hash of this record (includes previous_hash)
    timestamp: str
    metadata_hash: str
    sequence: int


def _hash_dict(d: Any) -> str:
    """Deterministic SHA-256 hash of any dict/value."""
    canonical = json.dumps(d, sort_keys=True, default=str).encode()
    return hashlib.sha256(canonical).hexdigest()


def compute_entry_hash(
    *,
    previous_hash: str,
    timestamp: str,
    tenant_id: str,
    goal_id: str,
    action: str,
    tool_name: str,
    tool_args_hash: str,
    actor: str,
    actor_ip: str,
    delegation_chain_hash: str,
    metadata_hash: str,
) -> str:
    """Compute the hash for an audit entry — MUST be deterministic."""
    payload = {
        "previous_hash": previous_hash,
        "timestamp": timestamp,
        "tenant_id": tenant_id,
        "goal_id": goal_id,
        "action": action,
        "tool_name": tool_name,
        "tool_args_hash": tool_args_hash,
        "actor": actor,
        "actor_ip": actor_ip,
        "delegation_chain_hash": delegation_chain_hash,
        "metadata_hash": metadata_hash,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class AuditV3:
    """
    Immutable append-only audit log with complete hash chain.

    Every record includes:
    - Full tool arguments (hashed for privacy, not raw)
    - Actor identity (user/agent) and IP
    - Delegation lineage chain hash
    - Previous record hash (chain integrity)
    """

    def __init__(self, db_factory: Any = None, redis: Any = None) -> None:
        self._db = db_factory
        self._redis = redis
        # In-memory buffer: tenant_id → last hash
        self._chain_tips: dict[str, str] = {}
        self._sequence: dict[str, int] = {}
        # In-memory buffer for non-DB environments
        self._records: list[AuditRecord] = []

    def _get_previous_hash(self, tenant_id: str) -> str:
        return self._chain_tips.get(tenant_id, "genesis")

    def _next_sequence(self, tenant_id: str) -> int:
        n = self._sequence.get(tenant_id, 0) + 1
        self._sequence[tenant_id] = n
        return n

    async def append_security_event(
        self,
        *,
        tenant_id: str,
        object_id: str,
        action: str,
        actor: str,
        object_digest: str,
        version_digest: str,
        reason: str,
        correlation_id: str,
        causation_id: str,
        outcome: str,
    ) -> AuditRecord:
        """Append a complete, privacy-preserving governance event to the existing chain."""
        if action not in _AGENT_PATTERN_AUDIT_ACTIONS:
            raise ValueError(f"unsupported audited action: {action}")
        required = {
            "tenant_id": tenant_id,
            "object_id": object_id,
            "actor": actor,
            "object_digest": object_digest,
            "version_digest": version_digest,
            "reason": reason,
            "correlation_id": correlation_id,
            "causation_id": causation_id,
            "outcome": outcome,
        }
        if any(not value.strip() for value in required.values()):
            raise ValueError("audit security-event fields must be non-empty")
        return await self.append(
            tenant_id=tenant_id,
            goal_id=object_id,
            action=action,
            actor=actor,
            metadata={
                "object_digest": object_digest,
                "version_digest": version_digest,
                "reason_digest": _hash_dict(reason),
                "correlation_id": correlation_id,
                "causation_id": causation_id,
                "outcome": outcome,
                "timestamp_source": "utc_system_clock",
            },
        )

    @staticmethod
    def authorize_break_glass(approvers: tuple[str, ...]) -> tuple[str, str]:
        """Require dual control before break-glass authority can be exercised."""
        distinct = tuple(dict.fromkeys(item.strip() for item in approvers if item.strip()))
        if len(distinct) < 2:
            raise PermissionError("break-glass requires two distinct approvers")
        return distinct[0], distinct[1]

    async def append(
        self,
        *,
        tenant_id: str,
        goal_id: str,
        action: str,
        tool_name: str = "",
        tool_args: dict[str, Any] | None = None,
        actor: str = "system",
        actor_ip: str = "",
        delegation_chain: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditRecord:
        """Append an audit record to the chain. Returns the new record.

        With a ``db_factory`` the record is appended to the durable per-tenant
        ``audit_chain`` (``PersistentAuditChain``: monotonic seq, race-safe across
        replicas, verifiable with :meth:`averify_chain`) and any failure RAISES —
        it used to INSERT columns ``audit_events`` does not have and swallow the
        error, so every record (incl. the GDPR deletion audit) was lost. Process
        memory is not the chain then: the returned record carries the stored
        seq / prev hash / record hash. Without a DB the in-memory chain is used.
        """
        ts = datetime.now(UTC).isoformat()
        tool_args_hash = _hash_dict(tool_args or {})
        delegation_chain_hash = _hash_dict(delegation_chain or {})
        metadata_hash = _hash_dict(metadata or {})

        if self._db is not None:
            from app.governance.audit_chain_store import PersistentAuditChain

            record_id = uuid.uuid4().hex
            stored = await PersistentAuditChain(self._db).append(
                tenant_id,
                {
                    "kind": "audit_v3",
                    "id": record_id,
                    "goal_id": goal_id,
                    "action": action,
                    "tool_name": tool_name,
                    "tool_args_hash": tool_args_hash,
                    "actor": actor,
                    "actor_ip": actor_ip,
                    "delegation_chain_hash": delegation_chain_hash,
                    "metadata_hash": metadata_hash,
                    "metadata": metadata or {},
                },
            )
            logger.debug("audit_appended", tenant=tenant_id, action=action, seq=stored["seq"])
            return AuditRecord(
                id=record_id,
                tenant_id=tenant_id,
                goal_id=goal_id,
                action=action,
                tool_name=tool_name,
                tool_args_hash=tool_args_hash,
                actor=actor,
                actor_ip=actor_ip,
                delegation_chain_hash=delegation_chain_hash,
                previous_hash=str(stored["prev_hash"]),
                entry_hash=str(stored["record_hash"]),
                timestamp=str(stored["at"]),
                metadata_hash=metadata_hash,
                sequence=int(stored["seq"]),
            )

        previous_hash = self._get_previous_hash(tenant_id)
        entry_hash = compute_entry_hash(
            previous_hash=previous_hash,
            timestamp=ts,
            tenant_id=tenant_id,
            goal_id=goal_id,
            action=action,
            tool_name=tool_name,
            tool_args_hash=tool_args_hash,
            actor=actor,
            actor_ip=actor_ip,
            delegation_chain_hash=delegation_chain_hash,
            metadata_hash=metadata_hash,
        )

        record = AuditRecord(
            id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            goal_id=goal_id,
            action=action,
            tool_name=tool_name,
            tool_args_hash=tool_args_hash,
            actor=actor,
            actor_ip=actor_ip,
            delegation_chain_hash=delegation_chain_hash,
            previous_hash=previous_hash,
            entry_hash=entry_hash,
            timestamp=ts,
            metadata_hash=metadata_hash,
            sequence=self._next_sequence(tenant_id),
        )
        self._chain_tips[tenant_id] = entry_hash
        self._records.append(record)
        logger.debug("audit_appended", tenant=tenant_id, action=action, hash=entry_hash[:16])
        return record

    async def averify_chain(self, tenant_id: str) -> dict[str, Any]:
        """Verify the tenant's STORED chain (the in-memory one without a DB).

        Raises when the chain cannot be read: an unreadable chain is never
        reported as verified.
        """
        if self._db is None:
            return self._verify_single_chain(tenant_id)
        from app.governance.audit_chain_store import PersistentAuditChain

        ok, broken_seq, checked, tip = await PersistentAuditChain(self._db).verify_detailed(
            tenant_id
        )
        result: dict[str, Any] = {
            "valid": ok,
            "records_checked": checked,
            "broken_at": broken_seq,
            "chain_tip_hash": tip if ok else None,
        }
        if not ok:
            result["reason"] = f"hash chain broken at seq {broken_seq}"
        return result

    def record(
        self,
        *,
        tenant_id: str,
        goal_id: str,
        action: str,
        tool_name: str = "",
        tool_args: dict[str, Any] | None = None,
        actor: str = "system",
        actor_ip: str = "",
        delegation_chain: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,  # absorb extra kwargs like risk_level
    ) -> AuditRecord:
        """Synchronous record() — convenience wrapper for non-async callers."""
        ts = datetime.now(UTC).isoformat()
        previous_hash = self._get_previous_hash(tenant_id)
        tool_args_hash = _hash_dict(tool_args or {})
        delegation_chain_hash = _hash_dict(delegation_chain or {})
        metadata_hash = _hash_dict(metadata or {})

        entry_hash = compute_entry_hash(
            previous_hash=previous_hash,
            timestamp=ts,
            tenant_id=tenant_id,
            goal_id=goal_id,
            action=action,
            tool_name=tool_name,
            tool_args_hash=tool_args_hash,
            actor=actor,
            actor_ip=actor_ip,
            delegation_chain_hash=delegation_chain_hash,
            metadata_hash=metadata_hash,
        )

        audit_record = AuditRecord(
            id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            goal_id=goal_id,
            action=action,
            tool_name=tool_name,
            tool_args_hash=tool_args_hash,
            actor=actor,
            actor_ip=actor_ip,
            delegation_chain_hash=delegation_chain_hash,
            previous_hash=previous_hash,
            entry_hash=entry_hash,
            timestamp=ts,
            metadata_hash=metadata_hash,
            sequence=self._next_sequence(tenant_id),
        )
        self._chain_tips[tenant_id] = entry_hash
        self._records.append(audit_record)
        logger.debug("audit_recorded", tenant=tenant_id, action=action, hash=entry_hash[:16])
        return audit_record

    def verify_chain(self, tenant_id: str | None = None) -> bool | dict[str, Any]:
        """
        Verify the hash chain integrity for a tenant.
        Recomputes hashes and detects any tampered records.

        When called without arguments (tenant_id=None), verifies ALL tenants
        and returns True if all chains are intact, False otherwise.
        """
        # No-arg call: verify all tenants, return bool
        if tenant_id is None:
            tenants = {r.tenant_id for r in self._records}
            if not tenants:
                return True
            for tid in tenants:
                result = self._verify_single_chain(tid)
                if not result["valid"]:
                    return False
            return True

        # Single-tenant call: return full dict (backward compat)
        return self._verify_single_chain(tenant_id)

    def _verify_single_chain(self, tenant_id: str) -> dict[str, Any]:
        """Internal: verify chain for a single tenant, always returns a dict."""
        records = [r for r in self._records if r.tenant_id == tenant_id]
        records.sort(key=lambda r: r.sequence)

        if not records:
            return {"valid": True, "records_checked": 0, "broken_at": None}

        # Verify chain
        previous_hash = "genesis"
        for record in records:
            if record.previous_hash != previous_hash:
                return {
                    "valid": False,
                    "records_checked": record.sequence,
                    "broken_at": record.id,
                    "reason": (
                        f"Chain break at sequence {record.sequence}: "
                        f"expected prev_hash={previous_hash[:16]}..., "
                        f"got={record.previous_hash[:16]}..."
                    ),
                }
            # Recompute entry hash
            expected = compute_entry_hash(
                previous_hash=previous_hash,
                timestamp=record.timestamp,
                tenant_id=tenant_id,
                goal_id=record.goal_id,
                action=record.action,
                tool_name=record.tool_name,
                tool_args_hash=record.tool_args_hash,
                actor=record.actor,
                actor_ip=record.actor_ip,
                delegation_chain_hash=record.delegation_chain_hash,
                metadata_hash=record.metadata_hash,
            )
            if expected != record.entry_hash:
                return {
                    "valid": False,
                    "records_checked": record.sequence,
                    "broken_at": record.id,
                    "reason": (f"Hash mismatch at sequence {record.sequence}: record was tampered"),
                }
            previous_hash = record.entry_hash

        return {"valid": True, "records_checked": len(records), "broken_at": None}

    def export_records(
        self,
        tenant_id: str,
        *,
        fmt: str = "json",
        goal_id: str | None = None,
    ) -> str:
        """Export audit records as JSON or CSV."""
        records = [r for r in self._records if r.tenant_id == tenant_id]
        if goal_id:
            records = [r for r in records if r.goal_id == goal_id]
        records.sort(key=lambda r: r.sequence)

        if fmt == "csv":
            buf = io.StringIO()
            writer = csv.DictWriter(
                buf,
                fieldnames=[
                    "id",
                    "sequence",
                    "timestamp",
                    "tenant_id",
                    "goal_id",
                    "action",
                    "tool_name",
                    "actor",
                    "actor_ip",
                    "entry_hash",
                    "previous_hash",
                ],
            )
            writer.writeheader()
            for r in records:
                writer.writerow(
                    {
                        "id": r.id,
                        "sequence": r.sequence,
                        "timestamp": r.timestamp,
                        "tenant_id": r.tenant_id,
                        "goal_id": r.goal_id,
                        "action": r.action,
                        "tool_name": r.tool_name,
                        "actor": r.actor,
                        "actor_ip": r.actor_ip,
                        "entry_hash": r.entry_hash,
                        "previous_hash": r.previous_hash,
                    }
                )
            return buf.getvalue()
        else:
            return json.dumps(
                [
                    {
                        "id": r.id,
                        "sequence": r.sequence,
                        "timestamp": r.timestamp,
                        "tenant_id": r.tenant_id,
                        "goal_id": r.goal_id,
                        "action": r.action,
                        "tool_name": r.tool_name,
                        "actor": r.actor,
                        "actor_ip": r.actor_ip,
                        "entry_hash": r.entry_hash,
                        "previous_hash": r.previous_hash,
                    }
                    for r in records
                ],
                indent=2,
            )

    def export_worm_bundle(self, tenant_id: str) -> str:
        """Export chain evidence with a deterministic manifest for immutable retention."""
        records = json.loads(self.export_records(tenant_id))
        manifest = {
            "tenant_digest": f"sha256:{_hash_dict(tenant_id)}",
            "record_count": len(records),
            "chain_tip": self._chain_tips.get(tenant_id, "genesis"),
            "retention_lock": "compliance",
            "records": records,
        }
        manifest["manifest_digest"] = f"sha256:{_hash_dict(manifest)}"
        return json.dumps(manifest, sort_keys=True)


# Module-level singleton
_audit_v3 = AuditV3()


# ---------------------------------------------------------------------------
# Persistent chain: WAL writer/flusher + verifier over ``audit_events``
# ---------------------------------------------------------------------------
# These used to be no-op "compat shims": AuditWriter.write discarded events,
# AuditFlusher.run slept forever (while the lifespan spawned it as if it were the
# flusher) and HashChainVerifier queried v3 columns that audit_events does not
# have, swallowed the error and answered verified=True. The durable, per-tenant
# hash chain that actually exists is the audit_v2 WAL pipeline (Redis WAL ->
# AuditFlusher -> audit_events with prev_hash/event_hash, per-tenant RLS), so
# the v3 names now ARE those implementations and the verifier checks that chain.


def _load_v2() -> Any:
    import warnings

    with warnings.catch_warnings():
        # audit_v2 warns on import that callers should use audit_v3; this module
        # is the sanctioned re-export, so the warning is noise here.
        warnings.simplefilter("ignore", DeprecationWarning)
        from app.governance import audit_v2 as _v2

    return _v2


_v2_module = _load_v2()
AuditWriter = _v2_module.AuditWriter
AuditFlusher = _v2_module.AuditFlusher


class AuditChainVerificationError(RuntimeError):
    """The chain could not be read, so integrity is UNKNOWN (never "verified")."""


class HashChainVerifier:
    """Verify the tenant's ``audit_events`` hash chain (written by AuditFlusher).

    Each row's ``event_hash`` is recomputed from its content and stored
    ``prev_hash``; the rows must then form ONE unbroken linked list:

    * a modified row fails its own hash recomputation;
    * a deleted row leaves its successor pointing at a hash that no longer exists;
    * a fork (two rows claiming the same predecessor) is reported too.

    A window that starts mid-chain is anchored on the predecessor hash, which must
    exist before ``from_date``. Any read error raises
    :class:`AuditChainVerificationError` — an unreadable chain is never reported
    as verified.
    """

    async def verify(
        self,
        db: Any,
        tenant_id: str,
        from_date: Any,
        to_date: Any,
    ) -> dict[str, Any]:
        from sqlalchemy import text

        audit_event_cls = _v2_module.AuditEvent
        try:
            # audit_events is FORCE ROW LEVEL SECURITY: without the tenant GUC a
            # NOBYPASSRLS session sees zero rows and "verifies" an empty chain.
            await db.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            rows = (
                await db.execute(
                    text(
                        """
                        SELECT id, event_type, resource_id, action, status,
                               created_at, prev_hash, event_hash
                        FROM audit_events
                        WHERE tenant_id = :tenant_id
                          AND created_at BETWEEN :from_date AND :to_date
                        ORDER BY created_at ASC, id ASC
                        """
                    ),
                    {"tenant_id": tenant_id, "from_date": from_date, "to_date": to_date},
                )
            ).fetchall()
        except Exception as exc:
            logger.error("audit_chain_verify_read_failed", tenant_id=tenant_id, error=str(exc))
            raise AuditChainVerificationError("audit chain could not be read") from exc

        def _broken(row_id: str | None, reason: str, verified: int) -> dict[str, Any]:
            return {
                "verified": False,
                "verified_events": verified,
                "broken_chain_at": row_id,
                "reason": reason,
                "chain_tip_hash": None,
            }

        if not rows:
            return {
                "verified": True,
                "verified_events": 0,
                "broken_chain_at": None,
                "chain_tip_hash": None,
            }

        # 1. Per-row integrity.
        by_prev: dict[str, list[Any]] = {}
        hashes: set[str] = set()
        for row in rows:
            created = row.created_at
            if hasattr(created, "astimezone"):
                created_iso = created.astimezone(UTC).isoformat()
            else:
                created_iso = str(created)
            ae = audit_event_cls(
                id=str(row.id),
                tenant_id=tenant_id,
                event_type=row.event_type,
                resource_id=str(row.resource_id) if row.resource_id else None,
                action=row.action,
                status=row.status,
                created_at=created_iso,
            )
            prev = str(row.prev_hash or "")
            if ae.compute_hash(prev) != row.event_hash:
                return _broken(str(row.id), "event hash mismatch (row modified)", 0)
            by_prev.setdefault(prev, []).append(row)
            hashes.add(str(row.event_hash))

        # 2. Linkage: exactly one entry point, no forks, no dangling predecessors.
        for children in by_prev.values():
            if len(children) > 1:
                return _broken(str(children[1].id), "chain fork (shared predecessor)", 0)
        entry_points = [p for p in by_prev if p not in hashes]
        if len(entry_points) != 1:
            # >1 entry point ⇒ some row's predecessor is missing (deleted row).
            dangling = sorted(
                (by_prev[p][0] for p in entry_points), key=lambda r: (r.created_at, r.id)
            )
            return _broken(str(dangling[-1].id), "missing predecessor (row deleted)", 0)
        anchor = entry_points[0]
        if anchor:
            # Window starts mid-chain: the predecessor must precede the window.
            try:
                exists = (
                    await db.execute(
                        text(
                            "SELECT 1 FROM audit_events WHERE tenant_id = :tid "
                            "AND event_hash = :h AND created_at < :from_date LIMIT 1"
                        ),
                        {"tid": tenant_id, "h": anchor, "from_date": from_date},
                    )
                ).scalar()
            except Exception as exc:
                raise AuditChainVerificationError("audit chain anchor unreadable") from exc
            if not exists:
                first = by_prev[anchor][0]
                return _broken(str(first.id), "missing predecessor (row deleted)", 0)

        # 3. Walk the chain from the anchor to the tip.
        verified = 0
        cur = anchor
        while cur in by_prev:
            row = by_prev[cur][0]
            verified += 1
            cur = str(row.event_hash)
        if verified != len(rows):
            return _broken(None, "chain contains a cycle or disconnected rows", verified)
        return {
            "verified": True,
            "verified_events": verified,
            "broken_chain_at": None,
            "chain_tip_hash": cur,
        }
