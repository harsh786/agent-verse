"""Fail-closed repository URL, pattern, and cloned-file validation."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from app.net.ssrf_guard import SSRFError, assert_public_url

_ALLOWED_SUFFIXES = frozenset({".py", ".md", ".ts", ".js"})
_SECRET_NAME_PARTS = (
    ".env",
    "credential",
    "credentials",
    "id_rsa",
    "private_key",
    "secret",
    "secrets",
)


class RepositorySecurityError(ValueError):
    """Repository input or cloned content violates ingestion policy."""


@dataclass(frozen=True, slots=True)
class RepositoryLimits:
    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    max_repository_bytes: int
    max_repository_files: int = 10_000


@dataclass(frozen=True, slots=True)
class RepositorySource:
    url: str
    hostname: str
    pinned_ip: str
    curl_resolve: str


@dataclass(frozen=True, slots=True)
class RepositoryFile:
    relative_path: str
    content: str


def repository_usage(root: Path) -> tuple[int, int]:
    """Return no-follow file count and bytes for clone-time quota monitoring."""
    file_count = 0
    total_bytes = 0
    if not root.exists():
        return 0, 0
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in directory_names:
            if (base / name).is_symlink():
                raise RepositorySecurityError("Repository symlink content is not allowed")
        for name in file_names:
            child = base / name
            if child.is_symlink():
                raise RepositorySecurityError("Repository symlink content is not allowed")
            file_count += 1
            total_bytes += child.stat(follow_symlinks=False).st_size
    return file_count, total_bytes


def resolve_repository_source(url: str) -> RepositorySource:
    """Resolve once, validate all addresses, and return a pinned HTTPS source."""
    try:
        parsed = urlsplit(url)
    except Exception as exc:
        raise RepositorySecurityError("Invalid repository URL") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise RepositorySecurityError("Repository URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise RepositorySecurityError("Repository URL credentials are not allowed")
    if any(ord(char) < 32 for char in url):
        raise RepositorySecurityError("Repository URL contains control characters")
    try:
        validated_ips = assert_public_url(url, context="repository ingestion")
    except (SSRFError, ValueError) as exc:
        raise RepositorySecurityError("Repository URL is not public") from exc
    host = parsed.hostname.lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as exc:
        raise RepositorySecurityError("Repository URL has an invalid port") from exc
    if port not in (None, 443):
        raise RepositorySecurityError("Alternate repository ports are not allowed")
    ipv4_addresses = [ip for ip in validated_ips if ":" not in ip]
    if not ipv4_addresses:
        raise RepositorySecurityError("Repository DNS pin cannot be represented safely")
    pinned_ip = sorted(ipv4_addresses)[0]
    path = parsed.path or "/"
    sanitized_url = urlunsplit(("https", host, path, "", ""))
    return RepositorySource(
        url=sanitized_url,
        hostname=host,
        pinned_ip=pinned_ip,
        curl_resolve=f"{host}:443:{pinned_ip}",
    )


def validate_repository_url(url: str) -> str:
    """Compatibility wrapper returning the sanitized pinned-source URL."""
    return resolve_repository_source(url).url


def validate_branch(branch: str) -> str:
    if (
        not branch
        or len(branch) > 255
        or branch.startswith("-")
        or any(ord(char) < 32 for char in branch)
    ):
        raise RepositorySecurityError("Invalid repository branch")
    return branch


def validate_patterns(patterns: list[str]) -> list[str]:
    """Validate bounded include globs against the text/code allowlist."""
    if not patterns or len(patterns) > 32:
        raise RepositorySecurityError("Repository include patterns are required and bounded")
    validated: list[str] = []
    for pattern in patterns:
        if not pattern or len(pattern) > 256 or "\x00" in pattern or "\\" in pattern:
            raise RepositorySecurityError("Invalid repository include pattern")
        pure = PurePosixPath(pattern)
        if pure.is_absolute() or ".." in pure.parts or ".git" in pure.parts:
            raise RepositorySecurityError("Repository include pattern escapes clone root")
        lower = pattern.lower()
        if any(secret in lower for secret in _SECRET_NAME_PARTS):
            raise RepositorySecurityError("Repository include pattern targets likely secrets")
        if not any(lower.endswith(suffix) for suffix in _ALLOWED_SUFFIXES):
            raise RepositorySecurityError("Repository include pattern is not allowlisted")
        validated.append(pattern)
    return validated


def _is_secret_or_disallowed(path: PurePosixPath) -> bool:
    return path.suffix.lower() not in _ALLOWED_SUFFIXES or any(
        secret in part.lower() for part in path.parts for secret in _SECRET_NAME_PARTS
    )


def _assert_no_symlink_components(root: Path, path: Path) -> None:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RepositorySecurityError("Repository symlink content is not allowed")


def read_repository_files(
    clone_root: Path,
    patterns: list[str],
    limits: RepositoryLimits,
) -> list[RepositoryFile]:
    """Safely select and decode bounded allowlisted files without following links."""
    validated_patterns = validate_patterns(patterns)
    root = clone_root.resolve(strict=True)
    repository_file_count, repository_bytes = repository_usage(root)
    if repository_file_count > limits.max_repository_files:
        raise RepositorySecurityError("Repository clone file count limit exceeded")
    if repository_bytes > limits.max_repository_bytes:
        raise RepositorySecurityError("Repository byte limit exceeded")

    selected: dict[Path, None] = {}
    for pattern in validated_patterns:
        for candidate in root.glob(pattern):
            if len(selected) >= limits.max_files:
                raise RepositorySecurityError("Repository file count limit exceeded")
            _assert_no_symlink_components(root, candidate)
            resolved = candidate.resolve(strict=True)
            try:
                relative = resolved.relative_to(root)
            except ValueError as exc:
                raise RepositorySecurityError("Repository file escapes clone root") from exc
            if (
                not resolved.is_file()
                or ".git" in relative.parts
                or _is_secret_or_disallowed(PurePosixPath(relative.as_posix()))
            ):
                continue
            selected[resolved] = None

    files: list[RepositoryFile] = []
    total_bytes = 0
    for path in sorted(selected):
        size = path.stat(follow_symlinks=False).st_size
        if size > limits.max_file_bytes:
            raise RepositorySecurityError("Repository file byte limit exceeded")
        total_bytes += size
        if total_bytes > limits.max_total_bytes:
            raise RepositorySecurityError("Repository total byte limit exceeded")
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            )
        except OSError as exc:
            raise RepositorySecurityError("Repository file cannot be opened safely") from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size != size:
                raise RepositorySecurityError("Repository file changed while reading")
            parts: list[bytes] = []
            bytes_read = 0
            while bytes_read <= limits.max_file_bytes:
                part = os.read(
                    descriptor,
                    min(65_536, limits.max_file_bytes + 1 - bytes_read),
                )
                if not part:
                    break
                parts.append(part)
                bytes_read += len(part)
            raw = b"".join(parts)
        finally:
            os.close(descriptor)
        if len(raw) != size or len(raw) > limits.max_file_bytes:
            raise RepositorySecurityError("Repository file changed while reading")
        if b"\x00" in raw:
            raise RepositorySecurityError("Repository binary content is not allowed")
        try:
            content = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise RepositorySecurityError("Repository file is not strict UTF-8 text") from exc
        files.append(
            RepositoryFile(
                relative_path=path.relative_to(root).as_posix(),
                content=content,
            )
        )
    return files
