"""ZIP archive expansion for knowledge uploads: streaming, bounded, honest.

An uploaded archive is expanded member by member (only one member's bytes are
held at a time) under hard limits, so a crafted file cannot exhaust a replica:

* the central directory is checked first (member count, declared uncompressed
  total, per-member compression ratio) — a classic zip bomb is refused before
  a single byte is inflated;
* every member is then read in blocks and counted, so an archive that lies in
  its headers is cut off at the limit as it inflates;
* nested archives are expanded up to ``max_depth`` levels, each counted
  against the same totals (a "42.zip"-style nest is refused the same way).

Directories, macOS resource forks (``__MACOSX/``, ``._*``) and hidden files are
ignored. Encrypted members, symlinks, archives nested too deep and members over
the per-file limit are skipped and reported (:class:`ArchiveSkip`), never
silently dropped. Member names are normalised: no absolute paths, drive letters
or ``..`` components survive (nothing is written to disk, but the name is shown
to users and cited).
"""

from __future__ import annotations

import io
import os
import posixpath
import stat
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass

from app.ingestion.document_text import DocumentParseError

ARCHIVE_EXTS = frozenset({"zip"})
_READ_BLOCK = 1024 * 1024
# Members smaller than this are never treated as bombs (tiny files compress well).
_RATIO_MIN_BYTES = 1024 * 1024


class ArchiveRejectedError(DocumentParseError):
    """The archive is refused as a whole (zip bomb, too large, too many members).

    ``too_large`` marks a size limit (an HTTP 413) rather than a malformed /
    hostile archive (422).
    """

    def __init__(self, message: str, *, too_large: bool = False) -> None:
        super().__init__(message)
        self.too_large = too_large


@dataclass(frozen=True)
class ArchiveLimits:
    max_member_bytes: int
    max_total_bytes: int
    max_members: int = 1000
    max_depth: int = 3  # the uploaded archive is depth 1
    max_ratio: int = 100

    @classmethod
    def for_upload_limit(cls, upload_limit: int) -> ArchiveLimits:
        """Per-file limit = the upload limit; all members together = twice it."""
        return cls(
            max_member_bytes=upload_limit,
            max_total_bytes=int(os.getenv("KNOWLEDGE_ARCHIVE_MAX_TOTAL_BYTES", "0"))
            or 2 * upload_limit,
            max_members=int(os.getenv("KNOWLEDGE_ARCHIVE_MAX_MEMBERS", "1000")),
            max_depth=int(os.getenv("KNOWLEDGE_ARCHIVE_MAX_DEPTH", "3")),
            max_ratio=int(os.getenv("KNOWLEDGE_ARCHIVE_MAX_RATIO", "100")),
        )


@dataclass(frozen=True)
class ArchiveMember:
    path: str  # e.g. "handbook/policy.docx" or "old/2025.zip/notes/history.md"
    ext: str
    data: bytes


@dataclass(frozen=True)
class ArchiveSkip:
    name: str
    reason: str


def safe_member_path(name: str) -> str:
    """A member name with backslashes, drive letters, absolute roots, ``.`` and
    ``..`` removed (``../../a/b.txt`` -> ``a/b.txt``)."""
    name = name.replace("\\", "/")
    if len(name) >= 2 and name[1] == ":":
        name = name[2:]
    parts: list[str] = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _ignored(path: str) -> bool:
    parts = path.split("/")
    return parts[0] == "__MACOSX" or any(p.startswith(".") for p in parts)


def _ext(path: str) -> str:
    base = posixpath.basename(path)
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


class _Budget:
    def __init__(self, limits: ArchiveLimits) -> None:
        self.limits = limits
        self.members = 0
        self.total = 0

    def count_member(self, filename: str) -> None:
        self.members += 1
        if self.members > self.limits.max_members:
            raise ArchiveRejectedError(
                f"{filename}: the archive has more than {self.limits.max_members} members"
            )

    def add_bytes(self, filename: str, n: int) -> None:
        self.total += n
        if self.total > self.limits.max_total_bytes:
            raise ArchiveRejectedError(
                f"{filename}: the archive expands to more than "
                f"{self.limits.max_total_bytes // (1024 * 1024)} MiB uncompressed",
                too_large=True,
            )


def iter_archive(
    data: bytes, *, filename: str, limits: ArchiveLimits
) -> Iterator[ArchiveMember | ArchiveSkip]:
    """Yield the archive's members (nested archives expanded) and skips.

    Raises ArchiveRejectedError for a zip bomb / limit breach and
    DocumentParseError for an unreadable archive.
    """
    yield from _walk(data, filename=filename, prefix="", depth=1, budget=_Budget(limits))


def _open(data: bytes, label: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError) as exc:
        raise DocumentParseError(f"{label}: not a readable zip archive ({exc})") from exc


def _walk(
    data: bytes, *, filename: str, prefix: str, depth: int, budget: _Budget
) -> Iterator[ArchiveMember | ArchiveSkip]:
    limits = budget.limits
    label = f"{filename}/{prefix}".rstrip("/") if prefix else filename
    with _open(data, label) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        _precheck(infos, label=label, budget=budget)
        for info in infos:
            path = safe_member_path(info.filename)
            if not path or _ignored(path):
                continue
            full = f"{prefix}{path}"
            budget.count_member(filename)
            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                yield ArchiveSkip(full, "symbolic link (not followed)")
                continue
            if info.flag_bits & 0x1:
                yield ArchiveSkip(full, "encrypted member (no password)")
                continue
            ext = _ext(path)
            if ext in ARCHIVE_EXTS and depth >= limits.max_depth:
                yield ArchiveSkip(full, f"nested archive deeper than {limits.max_depth} levels")
                continue
            if info.file_size > limits.max_member_bytes:
                mib = limits.max_member_bytes // (1024 * 1024)
                yield ArchiveSkip(full, f"larger than the {mib} MiB per-file limit")
                continue
            content = _read_member(zf, info, label=label, budget=budget)
            if ext in ARCHIVE_EXTS:
                yield from _walk(
                    content, filename=filename, prefix=f"{full}/", depth=depth + 1, budget=budget
                )
                continue
            yield ArchiveMember(full, ext, content)


def _precheck(infos: list[zipfile.ZipInfo], *, label: str, budget: _Budget) -> None:
    """Refuse from the central directory alone: a bomb, too many / too large members."""
    limits = budget.limits
    if budget.members + len(infos) > limits.max_members:
        raise ArchiveRejectedError(
            f"{label}: the archive has more than {limits.max_members} members"
        )
    for info in infos:
        if info.file_size >= _RATIO_MIN_BYTES and (
            info.file_size > limits.max_ratio * max(info.compress_size, 1)
        ):
            raise ArchiveRejectedError(
                f"{label}: {safe_member_path(info.filename)} has a compression ratio of "
                f"{info.file_size // max(info.compress_size, 1)}:1 (limit "
                f"{limits.max_ratio}:1); this looks like a zip bomb and was refused"
            )
    declared = sum(i.file_size for i in infos if i.file_size <= limits.max_member_bytes)
    if budget.total + declared > limits.max_total_bytes:
        raise ArchiveRejectedError(
            f"{label}: the archive declares {declared // (1024 * 1024)} MiB uncompressed, "
            f"over the {limits.max_total_bytes // (1024 * 1024)} MiB limit",
            too_large=True,
        )


def _read_member(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, *, label: str, budget: _Budget
) -> bytes:
    """Inflate one member in blocks, enforcing its declared size and the totals."""
    limit = min(info.file_size, budget.limits.max_member_bytes)
    chunks: list[bytes] = []
    read = 0
    try:
        with zf.open(info) as fh:
            while True:
                block = fh.read(_READ_BLOCK)
                if not block:
                    break
                read += len(block)
                if read > limit:
                    raise ArchiveRejectedError(
                        f"{label}: {safe_member_path(info.filename)} inflates past its "
                        "declared size; the archive was refused"
                    )
                budget.add_bytes(label, len(block))
                chunks.append(block)
    except ArchiveRejectedError:
        raise
    except (zipfile.BadZipFile, RuntimeError, ValueError, OSError, EOFError) as exc:
        raise DocumentParseError(
            f"{label}: {safe_member_path(info.filename)} could not be read from the zip "
            f"archive ({exc})"
        ) from exc
    return b"".join(chunks)
