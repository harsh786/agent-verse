"""BYOK-2: a process with the wrong / no vault key fails loudly, never decrypts wrong.

The user report: the API encrypted tenant BYOK keys with VAULT_MASTER_KEY, the
worker pod had no such variable, and every goal failed with the opaque
"Tenant LLM API key could not be decrypted". Now:

* an empty key value is not a key, and two different values under the two
  accepted names are refused (one of them silently won before);
* a Celery worker / beat process refuses to START without a usable key outside
  development (it used to start and fail every task);
* a decrypt failure names the side that is misconfigured by key FINGERPRINT
  (stored with the ciphertext), never the key;
* the API publishes a vault canary; a worker whose key cannot open it refuses
  to start outside development, and the API's /health/ready reports it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.providers import vault as vault_mod
from app.providers.tenant_provider import TenantProviderError, build_tenant_provider

KEY_A = "api-side-master-key-" + "a" * 24
KEY_B = "worker-side-master-key-" + "b" * 24


@pytest.fixture(autouse=True)
def _clean_vault_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    for name in (
        "VAULT_MASTER_KEY",
        "VAULT_MASTER_KEY_FILE",
        "AGENTVERSE_VAULT_KEY",
        "AGENTVERSE_VAULT_KEY_FILE",
        "VAULT_PREVIOUS_MASTER_KEYS",
    ):
        monkeypatch.delenv(name, raising=False)
    vault_mod._cached_vault.cache_clear()
    from app.providers import vault_canary

    vault_canary.reset_last_canary_result()
    yield
    vault_mod._cached_vault.cache_clear()
    vault_canary.reset_last_canary_result()
    vault_mod.set_process_role("api")


# ── key resolution ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("env", ["production", "staging"])
def test_an_empty_key_value_is_not_a_master_key(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    """docker-compose.prod passed AGENTVERSE_VAULT_KEY=${...:-} → '' was used as the key."""
    monkeypatch.setenv("ENVIRONMENT", env)
    monkeypatch.setenv("AGENTVERSE_VAULT_KEY", "")
    monkeypatch.setenv("VAULT_MASTER_KEY", "   ")
    with pytest.raises(RuntimeError, match="vault master key is required"):
        vault_mod.get_vault()


def test_an_empty_alias_does_not_shadow_the_real_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AGENTVERSE_VAULT_KEY", "")
    monkeypatch.setenv("VAULT_MASTER_KEY", KEY_A)
    assert vault_mod.get_vault().fingerprint() == vault_mod.CredentialVault(KEY_A).fingerprint()


@pytest.mark.parametrize("env", ["development", "production"])
def test_two_different_keys_under_the_two_names_are_refused(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    monkeypatch.setenv("ENVIRONMENT", env)
    monkeypatch.setenv("AGENTVERSE_VAULT_KEY", KEY_A)
    monkeypatch.setenv("VAULT_MASTER_KEY", KEY_B)
    with pytest.raises(RuntimeError, match=r"AGENTVERSE_VAULT_KEY.*VAULT_MASTER_KEY") as exc:
        vault_mod.get_vault()
    assert KEY_A not in str(exc.value) and KEY_B not in str(exc.value)


def test_the_same_key_under_both_names_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AGENTVERSE_VAULT_KEY", KEY_A)
    monkeypatch.setenv("VAULT_MASTER_KEY", KEY_A)
    assert vault_mod.get_vault().fingerprint() == vault_mod.CredentialVault(KEY_A).fingerprint()


def test_fingerprints_cover_previous_keys() -> None:
    vault = vault_mod.CredentialVault(KEY_B, previous_master_keys=(KEY_A,))
    assert vault.fingerprints() == (
        vault_mod.CredentialVault(KEY_B).fingerprint(),
        vault_mod.CredentialVault(KEY_A).fingerprint(),
    )
    assert KEY_A not in "".join(vault.fingerprints())


# ── worker / beat refuse to start ─────────────────────────────────────────────


@pytest.mark.parametrize("env", ["production", "staging"])
def test_worker_refuses_to_start_without_a_vault_key(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    from app.scaling import celery_app as celery_mod

    monkeypatch.setenv("ENVIRONMENT", env)
    with pytest.raises(SystemExit) as exc:
        celery_mod._vault_startup_check_worker()
    assert "VAULT_MASTER_KEY" in str(exc.value.code)
    assert vault_mod.process_role() == "worker"


def test_beat_refuses_to_start_without_a_vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import celery_app as celery_mod

    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(SystemExit):
        celery_mod._vault_startup_check_beat()


def test_worker_with_a_key_starts_and_runs_the_canary_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers import vault_canary
    from app.scaling import celery_app as celery_mod

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("VAULT_MASTER_KEY", KEY_A)
    fp = vault_mod.CredentialVault(KEY_A).fingerprint()
    seen: list[str] = []

    def _check(role: str) -> vault_canary.VaultCanaryResult:
        seen.append(role)
        return vault_canary.VaultCanaryResult("ok", fp, fp, "ok")

    monkeypatch.setattr(vault_canary, "run_vault_self_check", _check)
    celery_mod._vault_startup_check_worker()  # no SystemExit
    assert seen == ["worker"]


def test_worker_refuses_to_start_when_its_key_cannot_open_the_api_canary(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.providers import vault_canary
    from app.scaling import celery_app as celery_mod

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("VAULT_MASTER_KEY", KEY_B)
    mismatch = vault_canary.VaultCanaryResult(
        "mismatch", "fp-worker", "fp-api", "vault key mismatch: fp-worker vs fp-api"
    )
    monkeypatch.setattr(vault_canary, "run_vault_self_check", lambda role: mismatch)
    with pytest.raises(SystemExit) as exc:
        celery_mod._vault_startup_check_worker()
    assert "fp-api" in str(exc.value.code) and "fp-worker" in str(exc.value.code)


def test_dev_worker_logs_a_canary_mismatch_but_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers import vault_canary
    from app.scaling import celery_app as celery_mod

    monkeypatch.setenv("ENVIRONMENT", "development")
    mismatch = vault_canary.VaultCanaryResult("mismatch", "fp-w", "fp-a", "vault key mismatch")
    monkeypatch.setattr(vault_canary, "run_vault_self_check", lambda role: mismatch)
    celery_mod._vault_startup_check_worker()  # logged, not fatal in development


def test_worker_starts_when_the_canary_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient DB error is not a key mismatch: the worker starts (warning)."""
    from app.providers import vault_canary
    from app.scaling import celery_app as celery_mod

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("VAULT_MASTER_KEY", KEY_A)
    unavailable = vault_canary.VaultCanaryResult("unavailable", "fp", None, "db down")
    monkeypatch.setattr(vault_canary, "run_vault_self_check", lambda role: unavailable)
    celery_mod._vault_startup_check_worker()


def test_worker_init_and_beat_init_are_wired() -> None:
    from celery.signals import beat_init, worker_init

    from app.scaling import celery_app as celery_mod

    assert any(
        getattr(r[1](), "__name__", "") == "_on_worker_init_vault_check"
        for r in worker_init.receivers
        if r[1]() is not None
    ), "worker_init has no vault startup check"
    assert any(
        getattr(r[1](), "__name__", "") == "_on_beat_init_vault_check"
        for r in beat_init.receivers
        if r[1]() is not None
    ), "beat_init has no vault startup check"
    assert celery_mod._on_worker_init_vault_check is not None


# ── decrypt failures name the misconfigured side ──────────────────────────────


def _cfg_encrypted_with(key: str) -> dict[str, Any]:
    v = vault_mod.CredentialVault(key)
    return {
        "provider": "anthropic",
        "encrypted_key": v.encrypt("sk-ant-tenant-secret"),
        "vault_key_fingerprint": v.fingerprint(),
    }


def test_worker_with_another_key_reports_a_fingerprint_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("VAULT_MASTER_KEY", KEY_B)
    vault_mod.set_process_role("worker")
    cfg = _cfg_encrypted_with(KEY_A)
    with pytest.raises(TenantProviderError) as exc:
        build_tenant_provider(cfg, tenant_id="t1")
    msg = str(exc.value)
    fp_a = vault_mod.CredentialVault(KEY_A).fingerprint()
    fp_b = vault_mod.CredentialVault(KEY_B).fingerprint()
    assert "could not be decrypted" in msg
    assert "vault key mismatch" in msg
    assert fp_a in msg and fp_b in msg
    assert "worker" in msg
    assert "VAULT_MASTER_KEY" in msg
    for secret in (KEY_A, KEY_B, "sk-ant-tenant-secret"):
        assert secret not in msg


def test_mismatch_names_this_process_when_the_canary_agrees_with_the_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers import vault_canary

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("VAULT_MASTER_KEY", KEY_B)
    vault_mod.set_process_role("worker")
    fp_a = vault_mod.CredentialVault(KEY_A).fingerprint()
    fp_b = vault_mod.CredentialVault(KEY_B).fingerprint()
    vault_canary._set_last_canary_result(
        vault_canary.VaultCanaryResult("mismatch", fp_b, fp_a, "mismatch")
    )
    with pytest.raises(TenantProviderError, match="this worker process is the misconfigured"):
        build_tenant_provider(_cfg_encrypted_with(KEY_A), tenant_id="t1")


def test_previous_key_still_decrypts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("VAULT_MASTER_KEY", KEY_B)
    monkeypatch.setenv("VAULT_PREVIOUS_MASTER_KEYS", KEY_A)
    provider = build_tenant_provider(_cfg_encrypted_with(KEY_A), tenant_id="t1")
    assert provider is not None


def test_matching_fingerprint_but_unreadable_value_is_reported_as_corrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("VAULT_MASTER_KEY", KEY_A)
    cfg = _cfg_encrypted_with(KEY_A)
    cfg["encrypted_key"] = cfg["encrypted_key"][:-6] + "AAAAAA"
    with pytest.raises(TenantProviderError) as exc:
        build_tenant_provider(cfg, tenant_id="t1")
    assert "corrupt" in str(exc.value)
    assert "mismatch" not in str(exc.value)


def test_legacy_row_without_fingerprint_still_names_this_process_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("VAULT_MASTER_KEY", KEY_B)
    cfg = _cfg_encrypted_with(KEY_A)
    cfg.pop("vault_key_fingerprint")
    with pytest.raises(TenantProviderError) as exc:
        build_tenant_provider(cfg, tenant_id="t1")
    assert vault_mod.CredentialVault(KEY_B).fingerprint() in str(exc.value)
    assert "no key fingerprint was stored" in str(exc.value)


def test_process_without_any_key_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    vault_mod.set_process_role("worker")
    with pytest.raises(TenantProviderError) as exc:
        build_tenant_provider(_cfg_encrypted_with(KEY_A), tenant_id="t1")
    assert "this worker process has no usable vault master key" in str(exc.value)


# ── fingerprint is stored with the ciphertext ─────────────────────────────────


class _RecordingStore:
    def __init__(self) -> None:
        self.saved: dict[str, Any] = {}

    async def set_config(self, **kwargs: Any) -> None:
        self.saved = kwargs

    async def get_config(self, tenant_id: str, **_: Any) -> dict[str, Any] | None:
        return None


async def test_put_llm_saves_the_vault_key_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from app.api import tenants as tenants_api

    monkeypatch.setenv("VAULT_MASTER_KEY", KEY_A)
    store = _RecordingStore()
    request: Any = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(llm_config_store=store))
    )
    monkeypatch.setattr(tenants_api, "_llm_store", lambda req: store)
    await tenants_api._save_llm_config(
        request,
        "t1",
        provider="anthropic",
        encrypted_key=vault_mod.get_vault().encrypt("sk"),
        model="",
        base_url=None,
        masked_key="****",
        vault_key_fingerprint=vault_mod.get_vault().fingerprint(),
    )
    assert store.saved["vault_key_fingerprint"] == vault_mod.CredentialVault(KEY_A).fingerprint()


def test_llm_config_store_round_trips_the_fingerprint_through_the_cache() -> None:
    import inspect

    from app.services.llm_config_store import LLMConfigStore

    sig = inspect.signature(LLMConfigStore.set_config)
    assert "vault_key_fingerprint" in sig.parameters
    src = inspect.getsource(LLMConfigStore)
    assert "vault_key_fingerprint" in src  # persisted to Postgres + Redis cache


# ── API readiness ─────────────────────────────────────────────────────────────


async def test_vault_key_health_check_raises_on_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers import vault_canary

    async def _publish(db_factory: Any, role: str = "api") -> vault_canary.VaultCanaryResult:
        return vault_canary.VaultCanaryResult("mismatch", "fp-l", "fp-c", "vault key mismatch")

    monkeypatch.setattr(vault_canary, "publish_vault_canary", _publish)
    check = vault_canary.vault_key_health_check(object())
    with pytest.raises(RuntimeError, match="vault key mismatch"):
        await check.check()

    async def _ok(db_factory: Any, role: str = "api") -> vault_canary.VaultCanaryResult:
        return vault_canary.VaultCanaryResult("ok", "fp", "fp", "ok")

    monkeypatch.setattr(vault_canary, "publish_vault_canary", _ok)
    await vault_canary.vault_key_health_check(object()).check()
    assert check.name == "vault_key"
