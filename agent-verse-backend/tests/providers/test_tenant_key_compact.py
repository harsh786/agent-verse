"""TENANT-KEY-COMPACT unit checks: the re-seal decision and the CLI contract.
The end-to-end run (real Postgres + Redis) is tests/integration/test_tenant_key_compact_pg.py."""

from __future__ import annotations

from typing import Any

import pytest

from app.providers import tenant_key_compaction as tkc
from app.providers.tenant_vault import TENANT_CIPHER_PREFIX
from app.providers.vault import CredentialVault, get_vault

KEY_NEW, KEY_OLD, KEY_X = bytes(range(32)), bytes(range(40, 72)), bytes(range(80, 112))


def _tv(key: bytes, plain: str) -> str:
    return TENANT_CIPHER_PREFIX + CredentialVault.from_byok(key).encrypt(plain)


def test_sealer_reseals_old_key_values_and_counts_the_rest() -> None:
    rep = tkc.TenantCompaction("t")
    sealer = tkc._Sealer([KEY_NEW, KEY_OLD], rep)

    resealed = sealer.reseal(_tv(KEY_OLD, "a"))
    assert resealed is not None
    assert CredentialVault.from_byok(KEY_NEW).decrypt(resealed[len(TENANT_CIPHER_PREFIX):]) == "a"
    assert sealer.reseal(_tv(KEY_NEW, "b")) is None  # already current
    assert sealer.reseal(_tv(KEY_X, "c")) is None  # unreadable: never rewritten
    assert sealer.reseal(get_vault().encrypt("platform")) is None  # not a tenant value
    assert (rep.scanned, rep.resealed, rep.current, rep.unreadable) == (3, 1, 1, 1)


def test_source_config_secrets_are_resealed_in_place() -> None:
    rep = tkc.TenantCompaction("t")
    sealer = tkc._Sealer([KEY_NEW, KEY_OLD], rep)
    cfg = {"base_url": "u", "api_token": "enc:v1:" + _tv(KEY_OLD, '"tok"'), "nested": {}}
    out = sealer.reseal_source(cfg)
    assert out is not None and out["base_url"] == "u"
    assert out["api_token"].startswith("enc:v1:" + TENANT_CIPHER_PREFIX)
    assert sealer.reseal_source(out) is None


def test_redis_glob_escapes_tenant_ids() -> None:
    assert tkc._glob("a*b?[c]") == "a[*]b[?][[]c[]]"


@pytest.mark.parametrize(
    ("status", "code"),
    [("complete", 0), ("dry_run", 0), ("incomplete", 1)],
)
def test_cli_exit_codes_and_options(
    monkeypatch: pytest.MonkeyPatch, status: str, code: int
) -> None:
    from typer.testing import CliRunner

    from app.cli.main import app

    seen: dict[str, Any] = {}

    async def _compact(**kw: Any) -> dict[str, Any]:
        seen.update(kw)
        return {"status": status, "tenants": []}

    monkeypatch.setattr(tkc, "compact_tenant_keys", _compact)
    monkeypatch.delenv("REDIS_URL", raising=False)
    out = CliRunner().invoke(
        app, ["tenant-key-compact", "--tenant", "t1", "--tenant", "t2", "--dry-run",
              "--min-age-seconds", "5"],
    )
    assert out.exit_code == code, out.output
    assert seen["tenant_ids"] == ["t1", "t2"] and seen["dry_run"] is True
    assert seen["min_age_seconds"] == 5.0
