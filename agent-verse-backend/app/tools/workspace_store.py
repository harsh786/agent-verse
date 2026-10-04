"""Tenant file workspace (``/tools/files``), durable in Postgres (NATIVE-01).

The workspace used to be a pod-local ``/tmp`` directory per tenant: a file
written through one API replica was invisible to every other replica and to the
workers, and a restart lost it. Entries now live in ``workspace_files`` (one row
per file or directory, primary key ``(tenant_id, dir, name)``, RLS-forced); every
query also carries an explicit ``tenant_id`` predicate.

Directories are explicit rows written for every ancestor of a file, so listing a
directory is one indexed range read (``dir = :d ORDER BY name``) with keyset
pagination, never a scan of the subtree.

``InMemoryWorkspaceStore`` has the same semantics for unit tests and local
development only; ``durable`` is False and the API refuses it outside development.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.db.rls import sqlalchemy_rls_context

MAX_PATH_CHARS = 1024
MAX_SEGMENT_CHARS = 255
MAX_DEPTH = 32
DEFAULT_LIST_LIMIT = 500
MAX_LIST_LIMIT = 1000


class WorkspacePathError(ValueError):
    """The path is not a valid workspace path (400)."""


class WorkspaceConflictError(Exception):
    """A file stands where a directory is needed, or the reverse (409)."""


class WorkspaceUnavailableError(Exception):
    """The workspace store could not be reached (503); nothing was changed."""


class WorkspaceFileTooLargeError(Exception):
    """One file is larger than ``max_file_bytes`` (413)."""


class WorkspaceQuotaExceededError(Exception):
    """The write would take the tenant past its byte or entry quota (507)."""


@dataclass(frozen=True)
class WorkspaceLimits:
    """Per-file and per-tenant bounds (NATIVE-04): one tenant cannot fill the store."""

    max_file_bytes: int
    max_tenant_bytes: int
    max_entries: int

    @classmethod
    def from_settings(cls) -> WorkspaceLimits:
        from app.core.config import get_settings

        s = get_settings()
        return cls(
            max_file_bytes=int(s.workspace_max_file_bytes),
            max_tenant_bytes=int(s.workspace_max_tenant_bytes),
            max_entries=int(s.workspace_max_entries),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "max_file_bytes": self.max_file_bytes,
            "max_tenant_bytes": self.max_tenant_bytes,
            "max_entries": self.max_entries,
        }

    def check_file(self, size: int) -> None:
        if size > self.max_file_bytes:
            raise WorkspaceFileTooLargeError(
                f"file is {size} bytes; the limit is {self.max_file_bytes} bytes"
            )

    def check_totals(self, bytes_used: int, entries: int, d_bytes: int, d_entries: int) -> None:
        if d_bytes > 0 and bytes_used + d_bytes > self.max_tenant_bytes:
            raise WorkspaceQuotaExceededError(
                f"workspace quota exceeded: {bytes_used} of {self.max_tenant_bytes} bytes "
                f"used, this write needs {d_bytes} more"
            )
        if d_entries > 0 and entries + d_entries > self.max_entries:
            raise WorkspaceQuotaExceededError(
                f"workspace quota exceeded: {entries} of {self.max_entries} files and "
                f"directories used, this write needs {d_entries} more"
            )


@dataclass(frozen=True)
class _Entry:
    dir: str
    name: str
    kind: str  # "file" | "directory"
    content: str | None
    size_bytes: int
    updated_at: datetime


def split_path(path: str, *, allow_root: bool = False) -> tuple[str, str]:
    """Normalise *path* to ``(dir, name)``; ``("", "")`` is the root.

    ``..`` segments and absolute paths raise PermissionError (403, as the old
    filesystem workspace did for anything resolving outside it); malformed paths
    raise WorkspacePathError.
    """
    raw = path if isinstance(path, str) else ""
    if len(raw) > MAX_PATH_CHARS:
        raise WorkspacePathError(f"path longer than {MAX_PATH_CHARS} characters")
    if any(ord(c) < 32 or c == "\x7f" for c in raw) or "\\" in raw:
        raise WorkspacePathError("path contains control characters or backslashes")
    if raw.startswith("/"):
        raise PermissionError(f"Path {path!r} resolves outside the workspace")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PermissionError(f"Path {path!r} resolves outside the workspace")
    if any(len(p) > MAX_SEGMENT_CHARS for p in parts):
        raise WorkspacePathError(f"path segment longer than {MAX_SEGMENT_CHARS} characters")
    if len(parts) > MAX_DEPTH:
        raise WorkspacePathError(f"path deeper than {MAX_DEPTH} levels")
    if not parts:
        if allow_root:
            return "", ""
        raise WorkspacePathError("a file path is required")
    return "/".join(parts[:-1]), parts[-1]


def _join(dir_: str, name: str) -> str:
    return f"{dir_}/{name}" if dir_ else name


def _ancestors(dir_: str) -> list[tuple[str, str]]:
    """``(dir, name)`` of every directory on the way to *dir_*, outermost first."""
    if not dir_:
        return []
    parts = dir_.split("/")
    return [("/".join(parts[:i]), parts[i]) for i in range(len(parts))]


def _entry_dict(e: _Entry) -> dict[str, Any]:
    is_dir = e.kind == "directory"
    return {
        "name": e.name,
        "path": _join(e.dir, e.name),
        "type": "directory" if is_dir else "file",
        "is_dir": is_dir,
        "size_bytes": 0 if is_dir else e.size_bytes,
        "modified_at": e.updated_at.timestamp(),
    }


def _validate_content(content: str) -> bytes:
    if "\x00" in content:
        raise WorkspacePathError("file content may not contain NUL characters")
    return content.encode("utf-8")


class InMemoryWorkspaceStore:
    """Per-process store — unit tests and local development only."""

    durable = False

    def __init__(self, *, limits: WorkspaceLimits | None = None) -> None:
        self._data: dict[str, dict[tuple[str, str], _Entry]] = {}
        self._lock = asyncio.Lock()
        self._limits = limits

    @property
    def limits(self) -> WorkspaceLimits:
        return self._limits or WorkspaceLimits.from_settings()

    async def usage(self, tenant_id: str) -> dict[str, int]:
        entries = self._data.get(tenant_id, {})
        return {
            "bytes_used": sum(e.size_bytes for e in entries.values()),
            "entries": len(entries),
            **self.limits.as_dict(),
        }

    async def read(self, tenant_id: str, path: str) -> str:
        dir_, name = split_path(path)
        e = self._data.get(tenant_id, {}).get((dir_, name))
        if e is None:
            raise FileNotFoundError(f"File not found: {path!r}")
        if e.kind != "file":
            raise WorkspaceConflictError(f"{path!r} is a directory")
        return e.content or ""

    async def write(self, tenant_id: str, path: str, content: str) -> int:
        dir_, name = split_path(path)
        data = _validate_content(content)
        limits = self.limits
        limits.check_file(len(data))
        async with self._lock:
            entries = self._data.setdefault(tenant_id, {})
            now = datetime.now(UTC)
            for a_dir, a_name in _ancestors(dir_):
                existing = entries.get((a_dir, a_name))
                if existing is not None and existing.kind != "directory":
                    raise WorkspaceConflictError(f"{_join(a_dir, a_name)!r} is a file")
            target = entries.get((dir_, name))
            if target is not None and target.kind != "file":
                raise WorkspaceConflictError(f"{path!r} is a directory")
            new_entries = sum(1 for a in _ancestors(dir_) if a not in entries)
            limits.check_totals(
                sum(e.size_bytes for e in entries.values()),
                len(entries),
                len(data) - (target.size_bytes if target else 0),
                new_entries + (0 if target else 1),
            )
            for a_dir, a_name in _ancestors(dir_):
                entries.setdefault(
                    (a_dir, a_name), _Entry(a_dir, a_name, "directory", None, 0, now)
                )
            entries[(dir_, name)] = _Entry(dir_, name, "file", content, len(data), now)
        return len(data)

    async def list(
        self,
        tenant_id: str,
        directory: str = ".",
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        after: str | None = None,
    ) -> list[dict[str, Any]]:
        dir_, name = split_path(directory, allow_root=True)
        full = _join(dir_, name)
        entries = self._data.get(tenant_id, {})
        if full:
            node = entries.get((dir_, name))
            if node is None:
                return []
            if node.kind != "directory":
                raise WorkspaceConflictError(f"{directory!r} is not a directory")
        children = sorted(
            (e for (d, n), e in entries.items() if d == full and (after is None or n > after)),
            key=lambda e: e.name,
        )
        return [_entry_dict(e) for e in children[: max(1, min(limit, MAX_LIST_LIMIT))]]

    async def delete(self, tenant_id: str, path: str) -> bool:
        dir_, name = split_path(path)
        full = _join(dir_, name)
        async with self._lock:
            entries = self._data.get(tenant_id, {})
            if (dir_, name) not in entries:
                return False
            for key in [
                k
                for k in entries
                if k == (dir_, name) or k[0] == full or k[0].startswith(full + "/")
            ]:
                del entries[key]
        return True


def _like_prefix(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"


class PostgresWorkspaceStore:
    """``workspace_files`` in Postgres, shared by every replica and worker."""

    durable = True

    def __init__(self, db_session_factory: Any, *, limits: WorkspaceLimits | None = None) -> None:
        self._db = db_session_factory
        self._limits = limits

    @property
    def limits(self) -> WorkspaceLimits:
        return self._limits or WorkspaceLimits.from_settings()

    @staticmethod
    async def _lock_usage(s: Any, tenant_id: str) -> tuple[int, int]:
        """Lock the tenant's usage row: writes/deletes of one tenant serialise here,
        so the running totals stay exact without ever summing the table."""
        from sqlalchemy import text

        await s.execute(
            text(
                "INSERT INTO workspace_usage (tenant_id) VALUES (:t) "
                "ON CONFLICT (tenant_id) DO NOTHING"
            ),
            {"t": tenant_id},
        )
        row = (
            await s.execute(
                text(
                    "SELECT bytes_used, entries FROM workspace_usage "
                    "WHERE tenant_id = :t FOR UPDATE"
                ),
                {"t": tenant_id},
            )
        ).one()
        return int(row[0]), int(row[1])

    @staticmethod
    async def _add_usage(s: Any, tenant_id: str, d_bytes: int, d_entries: int) -> None:
        from sqlalchemy import text

        await s.execute(
            text(
                "UPDATE workspace_usage SET bytes_used = GREATEST(bytes_used + :b, 0), "
                "entries = GREATEST(entries + :e, 0), updated_at = NOW() "
                "WHERE tenant_id = :t"
            ),
            {"t": tenant_id, "b": d_bytes, "e": d_entries},
        )

    async def usage(self, tenant_id: str) -> dict[str, int]:
        from sqlalchemy import text

        try:
            async with self._db() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
                row = (
                    await s.execute(
                        text(
                            "SELECT bytes_used, entries FROM workspace_usage WHERE tenant_id = :t"
                        ),
                        {"t": tenant_id},
                    )
                ).first()
        except Exception as exc:
            raise WorkspaceUnavailableError(str(exc)[:200]) from exc
        return {
            "bytes_used": int(row[0]) if row else 0,
            "entries": int(row[1]) if row else 0,
            **self.limits.as_dict(),
        }

    async def read(self, tenant_id: str, path: str) -> str:
        from sqlalchemy import text

        dir_, name = split_path(path)
        try:
            async with self._db() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
                row = (
                    await s.execute(
                        text(
                            "SELECT kind, content FROM workspace_files "
                            "WHERE tenant_id = :t AND dir = :d AND name = :n"
                        ),
                        {"t": tenant_id, "d": dir_, "n": name},
                    )
                ).first()
        except Exception as exc:
            raise WorkspaceUnavailableError(str(exc)[:200]) from exc
        if row is None:
            raise FileNotFoundError(f"File not found: {path!r}")
        if row[0] != "file":
            raise WorkspaceConflictError(f"{path!r} is a directory")
        return str(row[1] or "")

    async def write(self, tenant_id: str, path: str, content: str) -> int:
        from sqlalchemy import text

        dir_, name = split_path(path)
        data = _validate_content(content)
        limits = self.limits
        limits.check_file(len(data))
        ancestors = _ancestors(dir_)
        params: dict[str, Any] = {"t": tenant_id}
        try:
            async with self._db() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
                bytes_used, entries = await self._lock_usage(s, tenant_id)
                new_dirs = 0
                if ancestors:
                    params["dirs"] = [a[0] for a in ancestors]
                    params["names"] = [a[1] for a in ancestors]
                    inserted = await s.execute(
                        text(
                            "INSERT INTO workspace_files (tenant_id, dir, name, kind) "
                            "SELECT :t, d, n, 'directory' "
                            "FROM unnest(CAST(:dirs AS varchar[]), CAST(:names AS varchar[])) "
                            "AS a(d, n) ON CONFLICT (tenant_id, dir, name) DO NOTHING "
                            "RETURNING 1"
                        ),
                        params,
                    )
                    new_dirs = len(inserted.fetchall())
                    # Re-read the ancestors (locked) AFTER the insert: a concurrent
                    # writer may have committed a FILE at one of these names.
                    clash = (
                        await s.execute(
                            text(
                                "SELECT w.dir, w.name FROM workspace_files w "
                                "JOIN unnest(CAST(:dirs AS varchar[]), "
                                "CAST(:names AS varchar[])) AS a(d, n) "
                                "ON w.dir = a.d AND w.name = a.n "
                                "WHERE w.tenant_id = :t AND w.kind <> 'directory' "
                                "LIMIT 1 FOR SHARE OF w"
                            ),
                            params,
                        )
                    ).first()
                    if clash is not None:
                        raise WorkspaceConflictError(f"{_join(clash[0], clash[1])!r} is a file")
                old = (
                    await s.execute(
                        text(
                            "SELECT size_bytes FROM workspace_files "
                            "WHERE tenant_id = :t AND dir = :d AND name = :n"
                        ),
                        {"t": tenant_id, "d": dir_, "n": name},
                    )
                ).first()
                d_bytes = len(data) - (int(old[0]) if old else 0)
                d_entries = new_dirs + (0 if old else 1)
                # Raising here rolls the whole transaction back (ancestors too).
                limits.check_totals(bytes_used, entries, d_bytes, d_entries)
                written = (
                    await s.execute(
                        text(
                            "INSERT INTO workspace_files "
                            "(tenant_id, dir, name, kind, content, size_bytes, sha256) "
                            "VALUES (:t, :d, :n, 'file', :c, :sz, :h) "
                            "ON CONFLICT (tenant_id, dir, name) DO UPDATE SET "
                            "content = EXCLUDED.content, size_bytes = EXCLUDED.size_bytes, "
                            "sha256 = EXCLUDED.sha256, updated_at = NOW() "
                            "WHERE workspace_files.kind = 'file' RETURNING 1"
                        ),
                        {
                            "t": tenant_id,
                            "d": dir_,
                            "n": name,
                            "c": content,
                            "sz": len(data),
                            "h": hashlib.sha256(data).hexdigest(),
                        },
                    )
                ).first()
                if written is None:
                    raise WorkspaceConflictError(f"{path!r} is a directory")
                await self._add_usage(s, tenant_id, d_bytes, d_entries)
        except (WorkspaceConflictError, WorkspacePathError, WorkspaceQuotaExceededError):
            raise
        except Exception as exc:
            raise WorkspaceUnavailableError(str(exc)[:200]) from exc
        return len(data)

    async def list(
        self,
        tenant_id: str,
        directory: str = ".",
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        after: str | None = None,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        dir_, name = split_path(directory, allow_root=True)
        full = _join(dir_, name)
        try:
            async with self._db() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
                if full:
                    node = (
                        await s.execute(
                            text(
                                "SELECT kind FROM workspace_files "
                                "WHERE tenant_id = :t AND dir = :d AND name = :n"
                            ),
                            {"t": tenant_id, "d": dir_, "n": name},
                        )
                    ).first()
                    if node is None:
                        return []
                    if node[0] != "directory":
                        raise WorkspaceConflictError(f"{directory!r} is not a directory")
                rows = (
                    await s.execute(
                        text(
                            "SELECT dir, name, kind, size_bytes, updated_at "
                            "FROM workspace_files WHERE tenant_id = :t AND dir = :d "
                            "AND (CAST(:after AS varchar) IS NULL OR name > :after) "
                            "ORDER BY name LIMIT :lim"
                        ),
                        {
                            "t": tenant_id,
                            "d": full,
                            "after": after,
                            "lim": max(1, min(int(limit), MAX_LIST_LIMIT)),
                        },
                    )
                ).fetchall()
        except WorkspaceConflictError:
            raise
        except Exception as exc:
            raise WorkspaceUnavailableError(str(exc)[:200]) from exc
        return [_entry_dict(_Entry(r[0], r[1], r[2], None, int(r[3] or 0), r[4])) for r in rows]

    async def delete(self, tenant_id: str, path: str) -> bool:
        from sqlalchemy import text

        dir_, name = split_path(path)
        full = _join(dir_, name)
        try:
            async with self._db() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
                await self._lock_usage(s, tenant_id)
                removed = (
                    await s.execute(
                        text(
                            "DELETE FROM workspace_files WHERE tenant_id = :t AND ("
                            "(dir = :d AND name = :n) OR dir = :full "
                            "OR dir LIKE :prefix ESCAPE '\\') RETURNING size_bytes"
                        ),
                        {
                            "t": tenant_id,
                            "d": dir_,
                            "n": name,
                            "full": full,
                            "prefix": _like_prefix(full),
                        },
                    )
                ).fetchall()
                if removed:
                    await self._add_usage(
                        s, tenant_id, -sum(int(r[0] or 0) for r in removed), -len(removed)
                    )
        except Exception as exc:
            raise WorkspaceUnavailableError(str(exc)[:200]) from exc
        return bool(removed)


WorkspaceStore = InMemoryWorkspaceStore | PostgresWorkspaceStore

__all__ = [
    "InMemoryWorkspaceStore",
    "PostgresWorkspaceStore",
    "WorkspaceConflictError",
    "WorkspaceFileTooLargeError",
    "WorkspaceLimits",
    "WorkspacePathError",
    "WorkspaceQuotaExceededError",
    "WorkspaceStore",
    "WorkspaceUnavailableError",
    "split_path",
]
