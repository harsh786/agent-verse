"""SECRET-05: the public dev vault key is only for development / test.

Any ENVIRONMENT other than exactly "production" (staging, prod-eu, ...) used to
fall back silently to the published ``dev-insecure-master-key``.
"""

from __future__ import annotations

import pytest

from app.providers import vault as vault_mod


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "VAULT_MASTER_KEY",
        "VAULT_MASTER_KEY_FILE",
        "AGENTVERSE_VAULT_KEY",
        "AGENTVERSE_VAULT_KEY_FILE",
        "VAULT_PREVIOUS_MASTER_KEYS",
    ):
        monkeypatch.delenv(name, raising=False)
    vault_mod._cached_vault.cache_clear()


@pytest.mark.parametrize("env", ["staging", "prod-eu", "Production", "qa", "banana"])
def test_non_dev_environment_without_a_key_refuses(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    monkeypatch.setenv("ENVIRONMENT", env)
    with pytest.raises(RuntimeError, match="vault master key"):
        vault_mod.get_vault()


@pytest.mark.parametrize("env", ["staging", "prod-eu"])
def test_non_dev_environment_refuses_the_dev_key_value(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    monkeypatch.setenv("ENVIRONMENT", env)
    monkeypatch.setenv("VAULT_MASTER_KEY", "dev-insecure-master-key")
    with pytest.raises(RuntimeError, match="dev-insecure-master-key"):
        vault_mod.get_vault()


@pytest.mark.parametrize("env", ["development", "test", "testing", "local", "dev"])
def test_dev_and_test_environments_may_use_the_dev_key(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    monkeypatch.setenv("ENVIRONMENT", env)
    monkeypatch.setenv("ALLOW_DEV_VAULT", "true")
    assert vault_mod.get_vault() is not None


def test_staging_with_a_real_key_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("VAULT_MASTER_KEY", "k" * 40)
    assert vault_mod.get_vault().decrypt(vault_mod.get_vault().encrypt("x")) == "x"
