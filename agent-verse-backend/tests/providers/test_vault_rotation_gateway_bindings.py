"""B2-GAP-2: ``agentverse vault-rotate`` re-encrypts messaging-gateway binding secrets.

``channel_tenant_mappings.channel_config.*_enc`` (DEF-3) holds platform-vault
ciphertext for a tenant without an envelope key. ``tenant-key-compact`` re-sealed
the ``tv1:`` ones, but ``vault-rotate`` never listed the table: after retiring the
old master key every such binding (Telegram secret_token, WhatsApp app secret,
Slack signing secret, outbound tokens) stopped opening. Real Postgres coverage:
``tests/integration/test_vault_rotate_all_stores_pg.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.providers import vault_rotation
from app.providers.tenant_vault import TENANT_CIPHER_PREFIX
from app.providers.vault import CredentialVault
from app.providers.vault_rotation import PG_STORES, StoreReport, _rotate_enc_fields

OLD = CredentialVault("old-master-key-for-binding-rotation-test")
NEW = CredentialVault("new-master-key-for-binding-rotation-test")


def test_binding_store_is_listed_with_the_enc_field_codec() -> None:
    store = next(s for s in PG_STORES if s.name == "channel_binding_secrets")
    assert store.table == "channel_tenant_mappings" and store.columns == ("channel_config",)
    assert store.source_json and store.enc_fields
    # Appended after every older store: a rotation checkpointed before the store
    # existed resumes onto it (later stores, e.g. tenant SMTP secrets, follow it).
    names = [s.name for s in PG_STORES]
    assert names.index("channel_binding_secrets") > names.index("workflow_version_secrets")
    assert PG_STORES[-1].name == "tenant_smtp_secrets"


def test_enc_fields_are_re_encrypted_and_everything_else_left_alone() -> None:
    config: dict[str, Any] = {
        "secret_enc": OLD.encrypt("telegram-secret-token"),
        "outbound_token_enc": OLD.encrypt("bot-token"),
        "verify_token_enc": "",
        "app_id": "app-1",
        "org_id": "org-1",
        "nested": {"other_enc": OLD.encrypt("nested-value")},
    }
    report = StoreReport()
    rotated = _rotate_enc_fields(config, OLD, NEW, report)
    assert rotated is not None and report.rotated == 3 and report.failed == 0
    assert NEW.decrypt(rotated["secret_enc"]) == "telegram-secret-token"
    assert NEW.decrypt(rotated["outbound_token_enc"]) == "bot-token"
    assert NEW.decrypt(rotated["nested"]["other_enc"]) == "nested-value"
    assert rotated["verify_token_enc"] == "" and rotated["app_id"] == "app-1"
    # Idempotent: a second pass finds everything current.
    again = StoreReport()
    assert _rotate_enc_fields(rotated, OLD, NEW, again) is None
    assert again.already_current == 3


def test_tenant_envelope_values_are_untouched_and_unreadable_ones_counted() -> None:
    tv1 = TENANT_CIPHER_PREFIX + CredentialVault.from_byok(bytes(range(32))).encrypt("x")
    stranger = CredentialVault("a-third-key-nobody-configured-0000000")
    report = StoreReport()
    out = _rotate_enc_fields(
        {"secret_enc": tv1, "outbound_token_enc": stranger.encrypt("lost")}, OLD, NEW, report
    )
    assert out is None  # nothing could (or needed to) change
    assert report.tenant_key == 1 and report.failed == 1


async def test_completed_rotation_from_before_the_store_resumes_onto_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rotation that reported ``complete`` before this store was added would
    otherwise skip every store on re-run, leaving the bindings on the old key."""
    names = [s.name for s in PG_STORES]
    added = names[names.index("channel_binding_secrets") :]  # stores newer than the run
    old_report = {name: {} for name in names if name not in added}
    visited: list[str] = []
    recorded: list[str] = []
    saved: list[str] = []

    async def _load(_db: Any, _rid: str) -> dict[str, Any]:
        return {"position": {"store": "__done__"}, "report": old_report, "status": "complete"}

    async def _tenants(_db: Any) -> list[str]:
        return ["t-1"]

    async def _batch(_db: Any, store: Any, *_a: Any) -> tuple[StoreReport, None]:
        visited.append(store.name)
        return StoreReport(rows_scanned=1, rotated=1), None

    async def _save(_db: Any, _rid: str, _pos: Any, _rep: Any, status: str) -> None:
        saved.append(status)

    async def _record(_db: Any, rid: str) -> None:
        recorded.append(rid)

    monkeypatch.setattr(vault_rotation, "_load_checkpoint", _load)
    monkeypatch.setattr(vault_rotation, "_tenant_ids", _tenants)
    monkeypatch.setattr(vault_rotation, "_rotate_pg_batch", _batch)
    monkeypatch.setattr(vault_rotation, "_save_checkpoint", _save)
    monkeypatch.setattr(vault_rotation, "_record_key_version", _record)

    result = await vault_rotation.rotate_all_stores(
        old=OLD, new=NEW, rotation_id="rot-1", system_db=object()
    )
    assert result["status"] == "complete", result
    assert visited == added
    assert recorded == []  # the key version was recorded by the original run
    assert saved[-1] == "complete"

    # A checkpoint that already covers every store stays a no-op.
    visited.clear()
    old_report.update({name: {} for name in added})
    result = await vault_rotation.rotate_all_stores(
        old=OLD, new=NEW, rotation_id="rot-1", system_db=object()
    )
    assert result["status"] == "complete" and visited == []
