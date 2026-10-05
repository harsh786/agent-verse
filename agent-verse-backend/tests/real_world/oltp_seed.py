"""A realistic shipping schema for the OLTP source scenarios (SRC-DB-*), PostgreSQL + MySQL.

Each run gets its own PostgreSQL schema / MySQL database and its own least-privilege
reader (SELECT on the granted tables only). Tables: ``customers`` (UTF-8 names,
NULL notes, JSON profile), ``shipments`` (FK, numeric, JSON meta, nullable
timestamp), ``shipment_events`` (bulk-loaded in ONE statement, so every row has
the same ``updated_at`` — more rows than one batch), the view
``shipment_overview`` (a join) and ``payroll`` (not granted at first). Seeding
runs from this host as the database's admin user; the stack only ever connects as
the reader. Nothing here imports ``app``.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass, field
from typing import Any

UTF8_NAMES = [
    "Zoë Fernández Logística", "株式会社ヤマト運輸テスト", "Ångström Frakt AB",
    "Δέλτα Μεταφορές", "शर्मा ट्रांसपोर्ट", "Łódź Spedycja", "Café Olé Exportações",
]
FACTS = {
    "customer_note": "Prefers night deliveries at gate 4; contact Okonkwo Adebayo",
    "seal": "SL-77Q1",
    "event_note": "Seal SL-90X found tampered at the Raipur weighbridge",
    "payroll": "Night-yard marshal Theodora Quist",
}


@dataclass
class Seeded:
    engine: str
    namespace: str  # PG schema / MySQL database
    reader: str
    reader_password: str
    counts: dict[str, int] = field(default_factory=dict)

    def tables(self, *names: str) -> list[str]:
        return [f"{self.namespace}.{n}" if self.engine == "postgresql" else n for n in names]


def _customers(n: int) -> list[tuple[Any, ...]]:
    rows = []
    for i in range(1, n + 1):
        name = UTF8_NAMES[i % len(UTF8_NAMES)] + f" #{i:03d}"
        notes = None if i % 3 == 0 else f"Account {i:03d} ships via the Nagpur hub."
        if i == 7:
            notes = FACTS["customer_note"]
        profile = {"tier": ["gold", "silver", "bronze"][i % 3], "languages": ["hi", "en"],
                   "credit_days": 30 + i % 4 * 15}
        rows.append((i, name, ["IN", "SE", "JP", "GR", "PL"][i % 5], notes, json.dumps(profile)))
    return rows


def _shipments(n: int, customers: int) -> list[tuple[Any, ...]]:
    rows = []
    for i in range(1, n + 1):
        meta = {"hazmat": i % 11 == 0, "seal": f"SL-{1000 + i}", "temps_c": [4, 5, 3]}
        if i == 42:
            meta["seal"] = FACTS["seal"]
        rows.append((i, 1 + i % customers, "Nhava Sheva", ["Ludhiana", "Raipur", "Kochi"][i % 3],
                     ["in_transit", "delivered", "held"][i % 3], round(100 + i * 1.25, 2),
                     json.dumps(meta)))
    return rows


# ── PostgreSQL ──────────────────────────────────────────────────────────────


async def _pg(dsn: str, statements: list[tuple[str, list[Any]] | str]) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        for st in statements:
            if isinstance(st, str):
                await conn.execute(st)
            else:
                await conn.executemany(st[0], st[1])
    finally:
        await conn.close()


def pg_exec(dsn: str, *statements: tuple[str, list[Any]] | str) -> None:
    asyncio.run(_pg(dsn, list(statements)))


def pg_fetchval(dsn: str, sql: str) -> Any:
    async def run() -> Any:
        import asyncpg

        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchval(sql)
        finally:
            await conn.close()

    return asyncio.run(run())


def seed_postgres(admin_dsn: str, tag: str, *, customers: int, shipments: int,
                  events: int) -> Seeded:
    ns, reader = f"rw_{tag}", f"rw_reader_{tag}"
    pw = secrets.token_hex(12)
    pg_exec(
        admin_dsn,
        f"CREATE SCHEMA {ns}",
        # payroll first: its rows are OLDER than every customer row.
        f"CREATE TABLE {ns}.payroll (id serial PRIMARY KEY, employee text NOT NULL, "
        f"salary numeric(12,2), updated_at timestamptz NOT NULL DEFAULT clock_timestamp())",
        f"INSERT INTO {ns}.payroll (employee, salary) VALUES "
        f"('{FACTS['payroll']}', 64000), ('Clerk Ines Duarte', 41000)",
        "SELECT pg_sleep(1.2)",
        f"CREATE TABLE {ns}.customers (id integer PRIMARY KEY, name text NOT NULL, "
        f"country char(2), notes text, profile jsonb, "
        f"updated_at timestamptz NOT NULL DEFAULT clock_timestamp())",
        (f"INSERT INTO {ns}.customers (id, name, country, notes, profile) "
         f"VALUES ($1, $2, $3, $4, $5::jsonb)", _customers(customers)),
        f"CREATE TABLE {ns}.shipments (id bigint PRIMARY KEY, "
        f"customer_id integer REFERENCES {ns}.customers(id), origin text, destination text, "
        f"status text, weight_kg numeric(10,2), meta jsonb, delivered_at timestamptz, "
        f"updated_at timestamptz NOT NULL DEFAULT clock_timestamp())",
        (f"INSERT INTO {ns}.shipments (id, customer_id, origin, destination, status, weight_kg, "
         f"meta) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)", _shipments(shipments, customers)),
        f"CREATE TABLE {ns}.shipment_events (id bigserial PRIMARY KEY, "
        f"shipment_id bigint REFERENCES {ns}.shipments(id), event_type text, note text, "
        f"updated_at timestamptz NOT NULL DEFAULT now())",
        # ONE statement: every event row gets the same now() — a bulk load.
        f"INSERT INTO {ns}.shipment_events (shipment_id, event_type, note) "
        f"SELECT 1 + g % {shipments}, (ARRAY['scan','gate_in','gate_out'])[1 + g % 3], "
        f"CASE WHEN g = {events} THEN '{FACTS['event_note']}' "
        f"ELSE 'routine scan ' || g END FROM generate_series(1, {events}) g",
        f"CREATE VIEW {ns}.shipment_overview AS SELECT s.id, c.name AS customer, s.origin, "
        f"s.destination, s.status, GREATEST(s.updated_at, c.updated_at) AS updated_at "
        f"FROM {ns}.shipments s JOIN {ns}.customers c ON c.id = s.customer_id",
        f"CREATE ROLE {reader} LOGIN PASSWORD '{pw}' NOSUPERUSER NOCREATEDB NOCREATEROLE",
        f"GRANT CONNECT ON DATABASE shipping TO {reader}",
        f"GRANT USAGE ON SCHEMA {ns} TO {reader}",
        f"GRANT SELECT ON {ns}.customers, {ns}.shipments, {ns}.shipment_events, "
        f"{ns}.shipment_overview TO {reader}",
    )
    return Seeded("postgresql", ns, reader, pw, {
        "customers": customers, "shipments": shipments, "shipment_events": events,
        "shipment_overview": shipments, "payroll": 2})


def drop_postgres(admin_dsn: str, s: Seeded) -> None:
    pg_exec(admin_dsn, f"DROP SCHEMA IF EXISTS {s.namespace} CASCADE",
            f"REVOKE ALL ON DATABASE shipping FROM {s.reader}",
            f"DROP ROLE IF EXISTS {s.reader}")


# ── MySQL ───────────────────────────────────────────────────────────────────


def my_exec(host: str, port: int, password: str, *statements: tuple[str, list[Any]] | str,
            database: str | None = None) -> None:
    import pymysql

    conn = pymysql.connect(host=host, port=port, user="root", password=password,
                           database=database, charset="utf8mb4", autocommit=True)
    try:
        with conn.cursor() as cur:
            for st in statements:
                if isinstance(st, str):
                    cur.execute(st)
                else:
                    cur.executemany(st[0], st[1])
    finally:
        conn.close()


def seed_mysql(host: str, port: int, password: str, tag: str, *, customers: int,
               shipments: int, events: int) -> Seeded:
    db, reader = f"rw_{tag}", f"rw_reader_{tag}"
    pw = secrets.token_hex(12)
    my_exec(host, port, password, f"CREATE DATABASE {db} CHARACTER SET utf8mb4")
    upd = "updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"
    my_exec(
        host, port, password,
        f"CREATE TABLE payroll (id INT AUTO_INCREMENT PRIMARY KEY, employee VARCHAR(200) NOT NULL, "
        f"salary DECIMAL(12,2), {upd})",
        f"INSERT INTO payroll (employee, salary) VALUES ('{FACTS['payroll']}', 64000), "
        f"('Clerk Ines Duarte', 41000)",
        "DO SLEEP(1.2)",
        f"CREATE TABLE customers (id INT PRIMARY KEY, name VARCHAR(200) NOT NULL, country CHAR(2), "
        f"notes TEXT NULL, profile JSON, {upd})",
        ("INSERT INTO customers (id, name, country, notes, profile) VALUES (%s, %s, %s, %s, %s)",
         _customers(customers)),
        f"CREATE TABLE shipments (id BIGINT PRIMARY KEY, customer_id INT, origin VARCHAR(80), "
        f"destination VARCHAR(80), status VARCHAR(20), weight_kg DECIMAL(10,2), meta JSON, "
        f"delivered_at DATETIME NULL, {upd}, FOREIGN KEY (customer_id) REFERENCES customers(id))",
        ("INSERT INTO shipments (id, customer_id, origin, destination, status, weight_kg, meta) "
         "VALUES (%s, %s, %s, %s, %s, %s, %s)", _shipments(shipments, customers)),
        f"CREATE TABLE shipment_events (id BIGINT AUTO_INCREMENT PRIMARY KEY, shipment_id BIGINT, "
        f"event_type VARCHAR(20), note TEXT, {upd})",
        "SET @@cte_max_recursion_depth = 100000",
        f"INSERT INTO shipment_events (shipment_id, event_type, note, updated_at) "
        f"WITH RECURSIVE g(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < {events}) "
        f"SELECT 1 + n % {shipments}, ELT(1 + n % 3, 'scan', 'gate_in', 'gate_out'), "
        f"IF(n = {events}, '{FACTS['event_note']}', CONCAT('routine scan ', n)), "
        f"NOW(6) FROM g",
        "CREATE VIEW shipment_overview AS SELECT s.id, c.name AS customer, s.origin, "
        "s.destination, s.status, GREATEST(s.updated_at, c.updated_at) AS updated_at "
        "FROM shipments s JOIN customers c ON c.id = s.customer_id",
        f"CREATE USER '{reader}'@'%' IDENTIFIED BY '{pw}'",
        *[f"GRANT SELECT ON {db}.{t} TO '{reader}'@'%'"
          for t in ("customers", "shipments", "shipment_events", "shipment_overview")],
        database=db,
    )
    return Seeded("mysql", db, reader, pw, {
        "customers": customers, "shipments": shipments, "shipment_events": events,
        "shipment_overview": shipments, "payroll": 2})


def drop_mysql(host: str, port: int, password: str, s: Seeded) -> None:
    my_exec(host, port, password, f"DROP DATABASE IF EXISTS {s.namespace}",
            f"DROP USER IF EXISTS '{s.reader}'@'%'")
