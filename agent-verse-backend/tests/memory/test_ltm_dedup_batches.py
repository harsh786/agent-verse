"""MEM-47: long-term-memory dedup finds duplicate groups with ONE index-backed
grouping query per tenant, then deletes in bounded batches by those groups.

The old dedup re-ran a window sort over ALL of a tenant's memories in every
batch (up to 50 per tenant per run).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import app.memory.ltm_maintenance as ltm

T1, T2 = "ltm-a", "ltm-b"


class _Session:
    def __init__(self, db: _Db) -> None:
        self._db = db

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = " ".join(str(stmt).split())
        params = dict(params or {})
        self._db.log.append((sql, params))
        if "set_config" in sql or "row_security" in sql:
            return SimpleNamespace(fetchall=list, rowcount=0)
        if sql.startswith("SELECT t.id FROM tenants"):
            return SimpleNamespace(fetchall=lambda: [(T1,), (T2,)])
        if sql.startswith("SELECT") and ("FROM legal_holds" in sql or "FROM tenant_settings" in sql):
            return SimpleNamespace(fetchall=list)
        if "HAVING count(*) > 1" in sql:
            groups = self._db.dups.get(params["tid"], {})
            hashes = sorted(groups)[: params["max_groups"]]
            return SimpleNamespace(fetchall=lambda: [(h,) for h in hashes])
        if sql.startswith("DELETE") and "md5(m.content) = ANY" in sql:
            groups = self._db.dups.get(params["tid"], {})
            deleted = 0
            for h in params["hashes"]:
                take = min(groups.get(h, 0), params["lim"] - deleted)
                if take:
                    groups[h] -= take
                    deleted += take
            return SimpleNamespace(rowcount=deleted)
        if sql.startswith("DELETE"):  # retention expiry
            return SimpleNamespace(rowcount=0)
        raise AssertionError(f"unexpected SQL: {sql}")


class _Db:
    def __init__(self, dups: dict[str, dict[str, int]]) -> None:
        # tenant -> {content hash -> removable extra copies}
        self.dups = dups
        self.log: list[tuple[str, dict[str, Any]]] = []

    def __call__(self) -> _Session:
        return _Session(self)


async def test_one_grouping_query_per_tenant_then_bounded_deletes() -> None:
    app_db = _Db({T1: {f"h{i:03d}": 1 for i in range(25)} | {"big": 7}, T2: {}})
    system_db = _Db({})

    totals = await ltm.consolidate_long_term_memory(
        system_db=system_db, app_db=app_db, batch_size=10, max_batches=50
    )

    assert totals["duplicates_removed"] == 25 + 7
    grouping = [p for s, p in app_db.log if "HAVING count(*) > 1" in s]
    assert [p["tid"] for p in grouping] == [T1, T2]  # once per tenant, not per batch
    deletes = [(s, p) for s, p in app_db.log if s.startswith("DELETE") and "md5" in s]
    assert deletes and all(len(p["hashes"]) <= 10 and p["lim"] == 10 for _, p in deletes)
    # No whole-tenant window: every dedup DELETE is restricted to duplicate groups.
    assert all("md5(m.content) = ANY" in s for s, _ in deletes)
    assert not any("PARTITION BY m.content ORDER BY" in s for s, _ in app_db.log)
    # T2 has no duplicates: no dedup DELETE was issued for it.
    assert all(p["tid"] == T1 for _, p in deletes)


async def test_dedup_stops_at_max_batches() -> None:
    app_db = _Db({T1: {f"h{i:03d}": 3 for i in range(100)}, T2: {}})
    totals = await ltm.consolidate_long_term_memory(
        system_db=_Db({}), app_db=app_db, batch_size=10, max_batches=4
    )
    # 4 DELETE transactions of <= 10 rows: three drain the first chunk of
    # groups (30 extra copies), the fourth confirms it is empty.
    deletes = [p for s, p in app_db.log if s.startswith("DELETE") and "md5" in s]
    assert len(deletes) == 4
    assert totals["duplicates_removed"] == 30
    grouping = [p for s, p in app_db.log if "HAVING count(*) > 1" in s]
    assert grouping[0]["max_groups"] == 40


async def test_tenant_scan_does_not_read_every_memory() -> None:
    system_db = _Db({})
    await ltm.consolidate_long_term_memory(system_db=system_db, app_db=_Db({}))
    scans = [s for s, _ in system_db.log if "long_term_memory" in s]
    assert scans and all("DISTINCT tenant_id FROM long_term_memory" not in s for s in scans)
    assert any("EXISTS (SELECT 1 FROM long_term_memory m WHERE m.tenant_id = t.id)" in s
               for s in scans)
