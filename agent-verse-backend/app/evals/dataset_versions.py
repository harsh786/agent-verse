"""Versioning rules for AI-Ops eval datasets (P7-3).

A dataset is a sequence of versions, each with its own golden tasks:

* ``published`` versions are immutable. Creating a dataset publishes version 1;
  a run always runs a published version and records ``dataset_version``.
* the head may be a ``draft`` — the only editable version. Editing a published
  head creates draft ``head + 1`` (copying its tasks); editing a draft head
  changes it in place. Running (or publishing) a draft publishes it.

The pure task-edit rules live here and are shared by the durable store
(:class:`app.evals.ai_ops_store.AIOpsStore`) and the in-memory fallback
(:class:`MemoryDatasetStore`, single-process dev and tests).
"""

from __future__ import annotations

import copy
import datetime
from typing import Any

DRAFT = "draft"
PUBLISHED = "published"
#: Upper bound of golden tasks per dataset version.
MAX_TASKS = 5000


class DatasetVersionError(Exception):
    """Base error of a dataset version operation (the API maps each subclass)."""


class DatasetNotFoundError(DatasetVersionError):
    pass


class VersionNotFoundError(DatasetVersionError):
    pass


class VersionConflictError(DatasetVersionError):
    """``if_version`` did not match the head (someone else edited it first)."""


class NothingToPublishError(DatasetVersionError):
    pass


class InvalidEditError(DatasetVersionError):
    pass


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def validate_task(task: Any) -> dict[str, Any]:
    if not isinstance(task, dict):
        raise InvalidEditError("a golden task must be an object")
    if not str(task.get("input") or task.get("goal") or "").strip():
        raise InvalidEditError("a golden task needs a non-empty 'input'")
    return dict(task)


def apply_edits(tasks: list[dict[str, Any]], ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply task edit operations to a copy of ``tasks``.

    ``{"op": "replace_all", "golden_tasks": [...]}``, ``{"op": "add", "task": {...}}``,
    ``{"op": "replace", "index": i, "task": {...}}``, ``{"op": "delete", "index": i}``.
    """
    out = [dict(t) for t in tasks]
    for op in ops:
        kind = op.get("op")
        if kind == "replace_all":
            raw = op.get("golden_tasks")
            if not isinstance(raw, list):
                raise InvalidEditError("'golden_tasks' must be a list")
            out = [validate_task(t) for t in raw]
        elif kind == "add":
            out.append(validate_task(op.get("task")))
        elif kind in {"replace", "delete"}:
            idx = op.get("index")
            if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < len(out):
                raise VersionNotFoundError(f"golden task {idx!r} does not exist")
            if kind == "replace":
                out[idx] = validate_task(op.get("task"))
            else:
                del out[idx]
        else:
            raise InvalidEditError(f"unknown edit operation {kind!r}")
    if len(out) > MAX_TASKS:
        raise InvalidEditError(f"a dataset version holds at most {MAX_TASKS} golden tasks")
    return out


def public_dataset(head: dict[str, Any]) -> dict[str, Any]:
    """The API shape of a dataset head (``version`` is the head version)."""
    tasks = list(head.get("golden_tasks") or [])
    return {
        "dataset_id": head["dataset_id"],
        "tenant_id": head.get("tenant_id"),
        "name": head.get("name", ""),
        "description": head.get("description", ""),
        "golden_tasks": tasks,
        "task_count": len(tasks),
        "created_at": head.get("created_at"),
        "version": int(head.get("version") or 1),
        "status": head.get("status") or PUBLISHED,
        "published_version": head.get("published_version"),
    }


class MemoryDatasetStore:
    """The versioned dataset surface over the API's in-process ``_datasets`` dict.

    Each dataset dict carries its versions under ``_versions``; single process
    only (no durable store wired).
    """

    def __init__(self, datasets: dict[str, dict[str, Any]]) -> None:
        self._d = datasets

    def _row(self, tenant_id: str, dataset_id: str) -> dict[str, Any]:
        row = self._d.get(f"{tenant_id}:{dataset_id}")
        if row is None:
            raise DatasetNotFoundError(dataset_id)
        return row

    def _head(self, row: dict[str, Any]) -> dict[str, Any]:
        versions: dict[int, dict[str, Any]] = row["_versions"]
        head_v = max(versions)
        published = [v for v, rec in versions.items() if rec["status"] == PUBLISHED]
        head = {k: v for k, v in row.items() if k != "_versions"}
        head.update(
            version=head_v,
            status=versions[head_v]["status"],
            golden_tasks=copy.deepcopy(versions[head_v]["golden_tasks"]),
            published_version=max(published) if published else None,
        )
        return public_dataset(head)

    async def create_dataset(
        self, *, tenant_id: str, dataset_id: str, name: str, description: str,
        golden_tasks: list[dict[str, Any]],
    ) -> None:
        now = _now()
        self._d[f"{tenant_id}:{dataset_id}"] = {
            "dataset_id": dataset_id, "tenant_id": tenant_id, "name": name,
            "description": description, "created_at": now,
            "_versions": {1: {"status": PUBLISHED, "golden_tasks": copy.deepcopy(golden_tasks),
                              "created_at": now, "published_at": now}},
        }

    async def get_dataset(self, tenant_id: str, dataset_id: str) -> dict[str, Any] | None:
        try:
            return self._head(self._row(tenant_id, dataset_id))
        except DatasetNotFoundError:
            return None

    async def list_datasets(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = [r for k, r in self._d.items() if k.startswith(f"{tenant_id}:")]
        rows.sort(key=lambda r: str(r.get("created_at")), reverse=True)
        return [self._head(r) for r in rows]

    async def get_dataset_version(
        self, tenant_id: str, dataset_id: str, version: int
    ) -> dict[str, Any] | None:
        try:
            row = self._row(tenant_id, dataset_id)
        except DatasetNotFoundError:
            return None
        rec = row["_versions"].get(int(version))
        if rec is None:
            return None
        return {
            "dataset_id": dataset_id, "name": row.get("name", ""), "version": int(version),
            "status": rec["status"], "golden_tasks": copy.deepcopy(rec["golden_tasks"]),
            "task_count": len(rec["golden_tasks"]), "created_at": rec["created_at"],
            "published_at": rec.get("published_at"),
        }

    async def list_dataset_versions(
        self, tenant_id: str, dataset_id: str
    ) -> list[dict[str, Any]]:
        row = self._row(tenant_id, dataset_id)
        return [
            {"version": v, "status": rec["status"], "task_count": len(rec["golden_tasks"]),
             "created_at": rec["created_at"], "published_at": rec.get("published_at")}
            for v, rec in sorted(row["_versions"].items(), reverse=True)
        ]

    async def edit_dataset(
        self, tenant_id: str, dataset_id: str, *, ops: list[dict[str, Any]],
        name: str | None = None, description: str | None = None,
        if_version: int | None = None,
    ) -> dict[str, Any]:
        row = self._row(tenant_id, dataset_id)
        versions: dict[int, dict[str, Any]] = row["_versions"]
        head_v = max(versions)
        if if_version is not None and int(if_version) != head_v:
            raise VersionConflictError(f"the head is version {head_v}, not {if_version}")
        if ops:
            tasks = apply_edits(versions[head_v]["golden_tasks"], ops)
            if versions[head_v]["status"] == PUBLISHED:
                versions[head_v + 1] = {"status": DRAFT, "golden_tasks": tasks,
                                        "created_at": _now(), "published_at": None}
            else:
                versions[head_v]["golden_tasks"] = tasks
        if name is not None:
            row["name"] = name
        if description is not None:
            row["description"] = description
        return self._head(row)

    async def publish_version(
        self, tenant_id: str, dataset_id: str, version: int | None = None
    ) -> dict[str, Any]:
        """Publish the head draft (or ``version`` if it is that draft)."""
        row = self._row(tenant_id, dataset_id)
        versions: dict[int, dict[str, Any]] = row["_versions"]
        head_v = max(versions)
        target = head_v if version is None else int(version)
        if target not in versions:
            raise VersionNotFoundError(f"version {target} does not exist")
        if versions[target]["status"] == PUBLISHED:
            raise NothingToPublishError(f"version {target} is already published")
        versions[target].update(status=PUBLISHED, published_at=_now())
        return self._head(row)

    async def pin_version_for_run(
        self, tenant_id: str, dataset_id: str, version: int | None = None
    ) -> dict[str, Any]:
        """The published version a run executes (a draft is published first)."""
        row = self._row(tenant_id, dataset_id)
        versions: dict[int, dict[str, Any]] = row["_versions"]
        target = max(versions) if version is None else int(version)
        if target not in versions:
            raise VersionNotFoundError(f"version {target} does not exist")
        if versions[target]["status"] == DRAFT:
            versions[target].update(status=PUBLISHED, published_at=_now())
        pinned = await self.get_dataset_version(tenant_id, dataset_id, target)
        if pinned is None:
            raise VersionNotFoundError(f"version {target} does not exist")
        return pinned
