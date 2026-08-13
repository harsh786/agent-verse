from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.runtime_flags import RuntimeFlags
from app.orchestration.strategy_certification import (
    RolloutController,
    StrategyEvidenceStore,
)

pytestmark = pytest.mark.integration


def test_runtime_flags_parse_shadow_allowlist_and_kill_switch(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_RUNTIME_V2_SHADOW", "true")
    monkeypatch.setenv("STRATEGY_RUNTIME_V2_TENANT_ALLOWLIST", "tenant-a, tenant-b")
    monkeypatch.setenv("STRATEGY_RUNTIME_V2_KILL_SWITCH", "true")

    flags = RuntimeFlags.from_env()

    assert flags.strategy_runtime_v2_shadow
    assert flags.strategy_runtime_v2_tenant_allowlist == frozenset({"tenant-a", "tenant-b"})
    assert flags.strategy_runtime_v2_kill_switch


def test_shadow_never_dispatches_v2_but_records_comparison() -> None:
    controller = RolloutController(shadow=True, allowlist=frozenset({"tenant-a"}))

    decision = controller.choose(
        tenant_id="tenant-a",
        legacy={"strategy": "react", "topology": ["plan"]},
        version_v2={"strategy": "reflection", "topology": ["plan", "reflect"]},
    )

    assert decision.path == "legacy"
    assert decision.shadow_comparison["strategy_mismatch"] is True
    assert decision.shadow_comparison["topology_mismatch"] is True


def test_canary_is_allowlisted_and_kill_switch_blocks_only_new_v2_admission() -> None:
    controller = RolloutController(allowlist=frozenset({"tenant-a"}))
    assert controller.choose("tenant-a", {}, {}).path == "v2"
    assert controller.choose("tenant-b", {}, {}).path == "legacy"

    killed = RolloutController(
        allowlist=frozenset({"tenant-a"}),
        kill_switch=True,
    )
    assert killed.choose("tenant-a", {}, {}, already_admitted=False).path == "rejected"
    assert killed.choose("tenant-a", {}, {}, already_admitted=True).path == "v2"


def test_evidence_store_exposes_append_and_version_expiry_filtered_query_only() -> None:
    store = StrategyEvidenceStore(db_session_factory=None)

    assert hasattr(store, "append")
    assert hasattr(store, "list_current")
    assert not hasattr(store, "update")
    assert not hasattr(store, "delete")
    query = store.current_query
    assert "tenant_id = :tenant_id" in query
    assert "adapter_version = :adapter_version" in query
    assert "state_schema_version = :state_schema_version" in query
    assert "expires_at > :now" in query


def test_evidence_expiry_boundary_is_utc_aware() -> None:
    now = datetime.now(UTC)
    assert StrategyEvidenceStore.is_current(now + timedelta(seconds=1), now)
    assert not StrategyEvidenceStore.is_current(now, now)
