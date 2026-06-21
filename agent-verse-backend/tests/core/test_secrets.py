"""Tests for read_secret() — the dev→prod secret resolution contract.

Precedence: <NAME>_FILE (mounted secret file) wins over <NAME> (plain env var),
which wins over the provided default. This lets one container image read a file in
production (Docker/K8s secrets) and a plain env var in local dev with no code change.
"""

import pytest

from app.core.secrets import SecretNotFoundError, read_secret


def test_reads_plain_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PASSWORD", "plain-value")
    assert read_secret("DB_PASSWORD") == "plain-value"


def test_file_suffixed_var_takes_precedence_over_plain(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    secret_file = tmp_path / "db_password"
    secret_file.write_text("file-value")
    monkeypatch.setenv("DB_PASSWORD", "plain-value")
    monkeypatch.setenv("DB_PASSWORD_FILE", str(secret_file))

    assert read_secret("DB_PASSWORD") == "file-value"


def test_file_contents_are_stripped(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    secret_file = tmp_path / "token"
    secret_file.write_text("  secret-token\n")
    monkeypatch.setenv("API_TOKEN_FILE", str(secret_file))

    assert read_secret("API_TOKEN") == "secret-token"


def test_returns_default_when_nothing_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING", raising=False)
    monkeypatch.delenv("MISSING_FILE", raising=False)
    assert read_secret("MISSING", default="fallback") == "fallback"


def test_raises_when_required_and_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING", raising=False)
    monkeypatch.delenv("MISSING_FILE", raising=False)
    with pytest.raises(SecretNotFoundError):
        read_secret("MISSING")


def test_raises_when_file_var_points_to_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("BAD_FILE", str(tmp_path / "does-not-exist"))
    with pytest.raises(SecretNotFoundError):
        read_secret("BAD")
