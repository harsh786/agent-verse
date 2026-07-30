"""Security policy tests for repository ingestion."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )


@pytest.mark.parametrize(
    "url",
    [
        "/tmp/repository",
        "file:///tmp/repository",
        "ssh://git@example.com/repository",
        "git://example.com/repository",
        "git@example.com:organization/repository.git",
        "http://example.com/repository",
        "https://user:secret@example.com/repository",
        "https://example.com:invalid/repository",
        "https://127.0.0.1/repository",
        "https://10.0.0.1/repository",
        "https://169.254.169.254/repository",
        "https://224.0.0.1/repository",
        "https://192.0.2.10/repository",
    ],
)
def test_repository_url_rejects_unsafe_sources(url: str) -> None:
    from app.ingestion.repository_security import RepositorySecurityError, validate_repository_url

    with pytest.raises(RepositorySecurityError):
        validate_repository_url(url)


def test_repository_url_is_sanitized_to_public_host_and_path() -> None:
    from app.ingestion.repository_security import resolve_repository_source

    source = resolve_repository_source(
        "https://github.com/example/repository.git?token=secret#fragment"
    )
    assert source.url == "https://github.com/example/repository.git"
    assert source.hostname == "github.com"
    assert source.pinned_ip == "93.184.216.34"
    assert source.curl_resolve == "github.com:443:93.184.216.34"


def test_repository_source_resolves_once_and_pins_validated_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ingestion.repository_security import resolve_repository_source

    calls = 0

    def resolve(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        nonlocal calls
        calls += 1
        if calls > 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    source = resolve_repository_source("https://example.com/repository")

    assert calls == 1
    assert source.curl_resolve == "example.com:443:93.184.216.34"


@pytest.mark.parametrize("branch", ["", "--upload-pack=evil", "main\nnext"])
def test_repository_branch_rejects_option_and_control_injection(branch: str) -> None:
    from app.ingestion.repository_security import RepositorySecurityError, validate_branch

    with pytest.raises(RepositorySecurityError):
        validate_branch(branch)


@pytest.mark.parametrize(
    "pattern",
    [
        "/etc/passwd",
        "../*.py",
        "src/../../*.py",
        "src\\*.py",
        ".git/**/*",
        "**/.env",
        "**/*.pem",
        "**/*.png",
    ],
)
def test_repository_patterns_reject_traversal_secrets_and_non_allowlisted_files(
    pattern: str,
) -> None:
    from app.ingestion.repository_security import RepositorySecurityError, validate_patterns

    with pytest.raises(RepositorySecurityError):
        validate_patterns([pattern])


def test_repository_file_selection_rejects_symlinks(tmp_path: Path) -> None:
    from app.ingestion.repository_security import (
        RepositoryLimits,
        RepositorySecurityError,
        read_repository_files,
    )

    outside = tmp_path.parent / "outside-secret.py"
    outside.write_text("SECRET = 'do not read'")
    (tmp_path / "linked.py").symlink_to(outside)

    with pytest.raises(RepositorySecurityError, match="symlink"):
        read_repository_files(
            tmp_path,
            ["**/*.py"],
            RepositoryLimits(max_files=10, max_file_bytes=1024, max_total_bytes=4096,
                             max_repository_bytes=8192),
        )


def test_repository_file_selection_enforces_file_and_total_limits(tmp_path: Path) -> None:
    from app.ingestion.repository_security import (
        RepositoryLimits,
        RepositorySecurityError,
        read_repository_files,
    )

    (tmp_path / "a.py").write_bytes(b"a" * 8)
    (tmp_path / "b.py").write_bytes(b"b" * 8)

    with pytest.raises(RepositorySecurityError, match="total byte"):
        read_repository_files(
            tmp_path,
            ["**/*.py"],
            RepositoryLimits(max_files=10, max_file_bytes=10, max_total_bytes=12,
                             max_repository_bytes=100),
        )


def test_repository_file_selection_enforces_count_and_clone_size(tmp_path: Path) -> None:
    from app.ingestion.repository_security import (
        RepositoryLimits,
        RepositorySecurityError,
        read_repository_files,
    )

    (tmp_path / "a.py").write_bytes(b"a" * 8)
    (tmp_path / "b.py").write_bytes(b"b" * 8)

    with pytest.raises(RepositorySecurityError, match="file count"):
        read_repository_files(
            tmp_path,
            ["**/*.py"],
            RepositoryLimits(max_files=1, max_file_bytes=100, max_total_bytes=100,
                             max_repository_bytes=100),
        )
    with pytest.raises(RepositorySecurityError, match="Repository byte"):
        read_repository_files(
            tmp_path,
            ["**/*.py"],
            RepositoryLimits(max_files=10, max_file_bytes=100, max_total_bytes=100,
                             max_repository_bytes=10),
        )
    with pytest.raises(RepositorySecurityError, match="file byte"):
        read_repository_files(
            tmp_path,
            ["**/*.py"],
            RepositoryLimits(max_files=10, max_file_bytes=4, max_total_bytes=100,
                             max_repository_bytes=100),
        )


def test_repository_file_selection_excludes_git_and_likely_secrets(tmp_path: Path) -> None:
    from app.ingestion.repository_security import RepositoryLimits, read_repository_files

    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config.py").write_text("secret")
    (tmp_path / "credentials.py").write_text("TOKEN = 'secret'")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "nested.py").write_text("TOKEN = 'nested secret'")
    (tmp_path / "service.py").write_text("def service(): return True")

    files = read_repository_files(
        tmp_path,
        ["**/*.py"],
        RepositoryLimits(max_files=10, max_file_bytes=1024, max_total_bytes=4096,
                         max_repository_bytes=8192),
    )

    assert [(item.relative_path, item.content) for item in files] == [
        ("service.py", "def service(): return True")
    ]


def test_repository_file_selection_rejects_binary_content_with_text_suffix(
    tmp_path: Path,
) -> None:
    from app.ingestion.repository_security import (
        RepositoryLimits,
        RepositorySecurityError,
        read_repository_files,
    )

    (tmp_path / "payload.py").write_bytes(b"\x00\x01binary")

    with pytest.raises(RepositorySecurityError, match="binary"):
        read_repository_files(
            tmp_path,
            ["**/*.py"],
            RepositoryLimits(max_files=10, max_file_bytes=1024, max_total_bytes=4096,
                             max_repository_bytes=8192),
        )


def test_repository_file_selection_rejects_invalid_utf8(tmp_path: Path) -> None:
    from app.ingestion.repository_security import (
        RepositoryLimits,
        RepositorySecurityError,
        read_repository_files,
    )

    (tmp_path / "payload.py").write_bytes(b"valid-prefix\xffinvalid")

    with pytest.raises(RepositorySecurityError, match="UTF-8"):
        read_repository_files(
            tmp_path,
            ["**/*.py"],
            RepositoryLimits(max_files=10, max_file_bytes=1024, max_total_bytes=4096,
                             max_repository_bytes=8192),
        )


def test_repository_heartbeat_setting_requires_safe_lease_margin() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(repo_ingest_lease_seconds=30, repo_ingest_heartbeat_seconds=15)
    settings = Settings(repo_ingest_lease_seconds=30, repo_ingest_heartbeat_seconds=5)
    assert settings.repo_ingest_heartbeat_seconds == 5
