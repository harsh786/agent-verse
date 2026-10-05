"""SRC-DB-*: PostgreSQL and MySQL knowledge sources on the live stack (P1b / A3).

The databases are throwaway containers on the compose network (``rw-pg``,
``rw-mysql``), operator-allowlisted for the connector egress guard. Each run seeds
its own schema (see :mod:`tests.real_world.oltp_seed`) and connects as a
least-privilege reader.

Environment (else SKIPPED): ``RW_PG_ROOT_PASSWORD`` (+ ``RW_PG_SEED_PORT``, default
56432; ``RW_PG_HOST``, default ``rw-pg``) and ``RW_MYSQL_ROOT_PASSWORD`` (+
``RW_MYSQL_SEED_PORT``, default 53306; ``RW_MYSQL_HOST``, default ``rw-mysql``).

Scenarios: SRC-DB-SYNC (realistic schema, view, JSON / NULL / UTF-8, a bulk-loaded
table larger than one batch with identical timestamps; then inserted, updated and
deleted rows), SRC-DB-TABLE-RETRY (a table the reader may not read fails alone and
is synced once access is granted — it must not be skipped because other tables
moved a shared cursor), SRC-DB-FAILURES (bad credentials, unreachable port, missing
database, missing table, internal hosts refused).
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from typing import Any

import pytest

from tests.real_world import kb
from tests.real_world import oltp_seed as seed
from tests.real_world import source_jobs as sj
from tests.real_world import sources as srcs
from tests.real_world.helpers import LiveAPI, mask, register_secret, tag, wait_until
from tests.real_world.metrics import norm, record

FAMILY = "oltp_database"
CUSTOMERS, SHIPMENTS, EVENTS = (int(os.getenv("RW_DB_CUSTOMERS", "30")),
                                int(os.getenv("RW_DB_SHIPMENTS", "120")),
                                int(os.getenv("RW_DB_EVENTS", "1500")))
BATCH = int(os.getenv("RW_DB_BATCH", "500"))


class Engine:
    def __init__(self, name: str) -> None:
        self.name = name
        if name == "postgresql":
            self.root = os.getenv("RW_PG_ROOT_PASSWORD", "")
            self.host = os.getenv("RW_PG_HOST", "rw-pg")
            self.port = 5432
            self.seed_port = int(os.getenv("RW_PG_SEED_PORT", "56432"))
        else:
            self.root = os.getenv("RW_MYSQL_ROOT_PASSWORD", "")
            self.host = os.getenv("RW_MYSQL_HOST", "rw-mysql")
            self.port = 3306
            self.seed_port = int(os.getenv("RW_MYSQL_SEED_PORT", "53306"))
        if not self.root:
            pytest.skip(f"needs RW_{'PG' if name == 'postgresql' else 'MYSQL'}_ROOT_PASSWORD: "
                        f"a {name} the stack can reach (egress-allowlisted)")
        register_secret(self.root)

    @property
    def admin_dsn(self) -> str:
        return f"postgresql://postgres:{self.root}@127.0.0.1:{self.seed_port}/shipping"

    def seed(self, **kw: int) -> seed.Seeded:
        t = tag()
        if self.name == "postgresql":
            s = seed.seed_postgres(self.admin_dsn, t, **kw)
        else:
            s = seed.seed_mysql("127.0.0.1", self.seed_port, self.root, t, **kw)
        register_secret(s.reader_password)
        return s

    def drop(self, s: seed.Seeded) -> None:
        with contextlib.suppress(Exception):
            if self.name == "postgresql":
                seed.drop_postgres(self.admin_dsn, s)
            else:
                seed.drop_mysql("127.0.0.1", self.seed_port, self.root, s)

    def run(self, s: seed.Seeded, *statements: str) -> None:
        if self.name == "postgresql":
            seed.pg_exec(self.admin_dsn, *[st.replace("{ns}", s.namespace + ".")
                                           for st in statements])
        else:
            seed.my_exec("127.0.0.1", self.seed_port, self.root,
                         *[st.replace("{ns}", "") for st in statements], database=s.namespace)

    def config(self, s: seed.Seeded, tables: list[str], **over: Any) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "host": self.host, "port": self.port,
            "database": "shipping" if self.name == "postgresql" else s.namespace,
            "username": s.reader, "password": s.reader_password,
            "tables": s.tables(*tables), "cursor_field": "updated_at", "batch_size": BATCH,
            "primary_keys": {"shipment_overview": ["id"]},
        }
        cfg.update(over)
        return cfg


@pytest.fixture(params=["postgresql", "mysql"])
def engine(request: pytest.FixtureRequest) -> Engine:
    return Engine(request.param)


@pytest.fixture
def seeded(engine: Engine) -> Iterator[seed.Seeded]:
    s = engine.seed(customers=CUSTOMERS, shipments=SHIPMENTS, events=EVENTS)
    yield s
    engine.drop(s)


def _source(api: LiveAPI, cleanup: Any, engine: Engine, cid: str, cfg: dict[str, Any],
            expect: int = 201) -> dict[str, Any]:
    return sj.create_source(api, cleanup, family=FAMILY, source_type=engine.name, config=cfg,
                            collection_id=cid, expect=expect)


def _hits(api: LiveAPI, cid: str, q: str, k: int = 5) -> list[dict[str, Any]]:
    return kb.search(api, cid, q, top_k=k)


def _found(api: LiveAPI, cid: str, q: str, needle: str) -> bool:
    return any(norm(needle) in norm(h.get("content")) for h in _hits(api, cid, q))


def _count(api: LiveAPI, cid: str) -> int:
    return int(kb.documents_page(api, cid, 1, 0).get("total") or 0)


# ── SRC-DB-SYNC ─────────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-DB-SYNC")
def test_db_first_and_incremental_sync(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                       engine: Engine, seeded: seed.Seeded) -> None:
    tables = ["customers", "shipments", "shipment_events", "shipment_overview"]
    cid = srcs.create_collection(api, cleanup, f"rw-db-{engine.name}")
    sid = _source(api, cleanup, engine, cid, engine.config(seeded, tables))["id"]
    evidence.update(engine=engine.name, namespace=seeded.namespace, source_id=sid,
                    collection_id=cid)
    total = sum(seeded.counts[t] for t in tables)

    job1 = sj.sync(api, sid, timeout=2400)
    evidence["sync1"] = job1
    soft: list[str] = []
    n1 = _count(api, cid)
    evidence["documents_after_sync1"] = n1
    if str(job1.get("status")).lower() not in sj.COMPLETED:
        soft.append(f"sync 1 {job1.get('status')}: {job1.get('error_message')}")
    if n1 != total:
        soft.append(f"sync 1 left {n1} documents, expected {total} rows "
                    f"({', '.join(f'{t}={seeded.counts[t]}' for t in tables)})")
    probes = {
        "null-safe customer note": ("night deliveries gate 4 Okonkwo", seed.FACTS["customer_note"]),
        "JSON value": ("shipment seal SL-77Q1", seed.FACTS["seal"]),
        "last row of a bulk load (> one batch, same timestamp)":
            ("seal tampered Raipur weighbridge", seed.FACTS["event_note"]),
        "UTF-8 name": ("株式会社ヤマト運輸テスト #001", "株式会社ヤマト運輸テスト #001"),
        "view (join)": ("shipment overview Zoë Fernández Logística", "Zoë Fernández Logística"),
    }
    found = {k: _found(api, cid, q, needle) for k, (q, needle) in probes.items()}
    evidence["probes"] = found
    soft += [f"not searchable: {k}" for k, ok in found.items() if not ok]
    sample = _hits(api, cid, "customer notes Nagpur hub account", 8)
    if any("None" in str(h.get("content")) for h in sample):
        soft.append("NULL columns are rendered as 'None'")
    evidence["sample_hit"] = mask(sample[0].get("content") if sample else "")[:300]

    # Upstream changes: 3 inserted, 2 updated, 2 deleted rows.
    engine.run(
        seeded,
        "INSERT INTO {ns}customers (id, name, country, notes) VALUES "
        "(901, 'Kashgar Silk Freight', 'IN', 'New account opened by Mirela Constantin'), "
        "(902, 'Øresund Kyl AB', 'SE', NULL), (903, 'Nordwind Spedition', 'PL', NULL)",
        # The application maintains updated_at (MySQL also does ON UPDATE).
        "UPDATE {ns}shipments SET status = 'returned_to_origin', "
        "updated_at = CURRENT_TIMESTAMP(6) WHERE id = 5",
        "UPDATE {ns}shipments SET status = 'customs_hold_bay_9', "
        "updated_at = CURRENT_TIMESTAMP(6) WHERE id = 6",
        "DELETE FROM {ns}shipment_events WHERE id IN (10, 11)",
    )
    time.sleep(1)
    job2 = sj.sync(api, sid, timeout=1200)
    evidence["sync2"] = job2
    if str(job2.get("status")).lower() not in sj.COMPLETED:
        soft.append(f"sync 2 {job2.get('status')}: {job2.get('error_message')}")
    # 3 inserts + 2 updated shipments + their 2 overview rows — counted over every
    # job after sync 1: the beat may run a scheduled sync of this source first.
    later = [j for j in sj.jobs(api, sid)
             if str(j.get("created_at") or "") > str(job1.get("started_at") or "")
             and not sj._same(j.get("job_id"), job1.get("job_id"))]
    evidence["jobs_after_sync1"] = [(j.get("triggered_by"), j.get("status"), j.get("docs_indexed"))
                                    for j in later]
    changed = sum(int(j.get("docs_indexed") or 0) for j in later)
    if changed != 7:
        soft.append(f"{changed} rows indexed after sync 1, expected the 7 changed rows")
    if not _found(api, cid, "Kashgar Silk Freight Mirela Constantin", "Mirela Constantin"):
        soft.append("inserted row not searchable")
    if not _found(api, cid, "shipment customs hold bay 9", "customs_hold_bay_9"):
        soft.append("updated row's new value not searchable")
    rec = api.post(f"/sources/{sid}/reconcile")
    evidence["reconcile_http"] = rec.status_code
    if rec.status_code != 202:
        soft.append(f"deleted rows cannot be reconciled: {rec.status_code} "
                    f"{mask(rec.text)[:160]}")
    else:
        try:
            wait_until(lambda: _count(api, cid), timeout=300, interval=6,
                       desc="deleted rows removed", done=lambda n: n == total + 3 - 2)
        except AssertionError as exc:
            soft.append(f"reconcile did not remove exactly the 2 deleted rows: {exc}")
    evidence["documents_final"] = _count(api, cid)
    record(evidence, rows=total, sync1_s=job1.get("wall_s"), sync2_s=job2.get("wall_s"),
           rows_per_s=round(n1 / max(1.0, float(job1.get("wall_s") or 1)), 2))
    assert not soft, "; ".join(soft)


# ── SRC-DB-TABLE-RETRY ──────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-DB-TABLE-RETRY")
def test_db_failed_table_is_not_skipped_later(api: LiveAPI, cleanup: Any,
                                              evidence: dict[str, Any], engine: Engine,
                                              seeded: seed.Seeded) -> None:
    cid = srcs.create_collection(api, cleanup, f"rw-db-priv-{engine.name}")
    sid = _source(api, cleanup, engine, cid,
                  engine.config(seeded, ["customers", "payroll"]))["id"]
    job1 = sj.sync(api, sid)
    evidence["sync1"] = job1
    soft: list[str] = []
    msg = str(job1.get("error_message") or "")
    if str(job1.get("status")).lower() != "partial" or "payroll" not in msg:
        soft.append(f"an unreadable table should make the sync partial naming it: {job1}")
    if not any(w in msg.lower() for w in ("permission", "denied", "privilege")):
        soft.append(f"the reason does not say access was denied: {msg[:200]!r}")
    if _count(api, cid) != seeded.counts["customers"]:
        soft.append(f"readable table not synced: {_count(api, cid)} documents")
    grant = (f"GRANT SELECT ON {seeded.namespace}.payroll TO {seeded.reader}"
             if engine.name == "postgresql"
             else f"GRANT SELECT ON {seeded.namespace}.payroll TO '{seeded.reader}'@'%'")
    engine.run(seeded, grant)
    job2 = sj.sync(api, sid)
    evidence["sync2"] = job2
    if str(job2.get("status")).lower() not in sj.COMPLETED:
        soft.append(f"sync after the grant {job2.get('status')}: {job2.get('error_message')}")
    if not _found(api, cid, "night-yard marshal payroll", seed.FACTS["payroll"]):
        soft.append("the table that failed before was skipped after access was granted "
                    "(its rows are older than the cursor the other table advanced)")
    evidence["documents"] = _count(api, cid)
    assert not soft, "; ".join(soft)


# ── SRC-DB-FAILURES ─────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-DB-FAILURES")
def test_db_honest_failures(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                            engine: Engine, seeded: seed.Seeded) -> None:
    cid = srcs.create_collection(api, cleanup, f"rw-db-fail-{engine.name}")
    base = engine.config(seeded, ["customers"])
    words_auth = ("password", "authentication", "access denied", "1045")
    cases = {
        "bad_password": (dict(base, password="wrong-" + tag()), words_auth),
        "unknown_user": (dict(base, username="nobody_" + tag()), (*words_auth, "role")),
        "unreachable_port": (dict(base, port=5999), ("connect", "refused", "timed out",
                                                      "2003", "unreachable")),
        "missing_database": (dict(base, database="nosuchdb_" + tag()),
                             ("does not exist", "unknown database", "1044", "1049",
                              "access denied")),
        "missing_table": (dict(base, tables=seeded.tables("no_such_table")),
                          ("no_such_table",)),
    }
    results: dict[str, Any] = {}
    soft: list[str] = []
    for name, (cfg, words) in cases.items():
        v = sj.validate(api, family=FAMILY, source_type=engine.name, config=cfg)
        sid = _source(api, cleanup, engine, cid, cfg)["id"]
        t0 = time.monotonic()
        job = sj.sync(api, sid, timeout=600)
        msg = str(job.get("error_message") or "")
        results[name] = {"validate_valid": v.get("valid"), "status": job.get("status"),
                         "failed": job.get("docs_failed"), "error": msg[:220],
                         "fail_s": round(time.monotonic() - t0, 1)}
        if name != "missing_table" and v.get("valid"):
            soft.append(f"{name}: /sources/validate reported valid")
        if str(job.get("status")).lower() not in ("failed", "partial") or not job.get("docs_failed"):
            soft.append(f"{name}: not an honest failure: {job.get('status')}")
        if not any(w.lower() in msg.lower() for w in words):
            soft.append(f"{name}: error does not say why: {msg[:160]!r}")
    internal = ["postgres", "pgbouncer", "redis", "localhost", "127.0.0.1", "169.254.169.254",
                "host.docker.internal", "10.1.2.3", "/var/run/postgresql"]
    for host in internal:
        r = _source(api, cleanup, engine, cid, dict(base, host=host), expect=422)
        results[f"refused {host}"] = str(r.get("detail"))[:100]
    evidence["cases"] = results
    if _count(api, cid):
        soft.append("a failing source indexed documents")
    assert not soft, "; ".join(soft)
