"""A realistic order-management dataset for the MongoDB scenarios (SRC-MONGO-*, MCP-MONGO-*).

Seeding runs from this host as the throwaway server's admin user (``rwroot``);
the stack connects as a least-privilege reader (``read`` on ``rw_p1c``) or, for
the MCP tool connector, as ``rwtool`` (``readWrite`` on ``rw_shop``). Every run
uses its own collections (``<name>_<tag>``) so runs never see each other's data.

Documents are what an operations team really keeps in MongoDB: orders with
embedded customers, line items (Decimal128 money), a long tracking-event array,
a deeply nested routing plan, and the BSON types a JSON export would mangle
(Int64 beyond 2^53, Decimal128, Binary, Regex, UUID, Timestamp, null). Nothing
here imports ``app``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any

FACTS = {
    # One per probe; each must be searchable after sync 1.
    "order_note": "Deliver to the Bhiwandi cold-chain annex, bay C-14, before 06:00",
    "tracking_early": "Cleared customs at Nhava Sheva gate 3 with seal NS-4471",
    "tracking_late": "Late scan beyond the indexed window: pallet KX-998 rerouted",
    "deep": "Final mile handled by courier Thandiwe Mokoena on an e-cargo bike",
    "customer": "Key account Ishaan Raghunathan prefers invoices in Kannada",
    "product": "Hand-thrown terracotta water cooler, 12 litre, from Kutch artisans",
    "decimal": "1249.75",
    "int64": "9007199254740993",
}
UPDATED = {
    "status": "returned_to_origin_after_failed_kyc",
    "note": "Customer asked to reroute to the Hosur micro-fulfilment centre",
}
INSERTED_NOTE = "Rush order for the Onam festival stock at the Kochi store"

CITIES = ["Pune", "Nagpur", "Kochi", "Guwahati", "Surat", "Indore", "Mysuru", "Ranchi"]
NAMES = ["Zoë Fernández", "Ishaan Raghunathan", "Ångström Frakt", "Δέσποινα Παππά",
         "शर्मा ट्रेडर्स", "株式会社ミナト", "Łucja Nowak", "Olúwaseun Adébáyọ̀"]


@dataclass
class MongoSeeded:
    database: str
    tag: str
    collections: dict[str, str] = field(default_factory=dict)  # logical -> real name
    counts: dict[str, int] = field(default_factory=dict)
    ids: dict[str, Any] = field(default_factory=dict)  # named documents' _id

    def names(self, *logical: str) -> list[str]:
        return [self.collections[n] for n in (logical or tuple(self.collections))]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _order(i: int, t0: dt.datetime) -> dict[str, Any]:
    from bson import Decimal128

    city = CITIES[i % len(CITIES)]
    lines = [{"sku": f"SKU-{(i * 7 + k) % 300:04d}", "qty": 1 + (i + k) % 4,
              "unit_price": Decimal128(f"{120 + (i * 13 + k * 7) % 900}.50")}
             for k in range(1 + i % 4)]
    return {
        "order_no": f"ORD-{i:05d}",
        "status": ["placed", "packed", "shipped", "delivered"][i % 4],
        "placed_at": t0 + dt.timedelta(minutes=17 * i),
        "updated_at": t0 + dt.timedelta(minutes=17 * i),
        "customer": {"name": NAMES[i % len(NAMES)] + f" #{i:03d}",
                     "address": {"city": city, "pin": f"{400000 + i * 37 % 99999}"}},
        "lines": lines,
        "notes": f"Order {i:05d} for the {city} region; standard 3-day surface shipping "
                 f"via the {city} hub, packed in recyclable cartons.",
    }


def seed(client: Any, database: str, tag: str, *, orders: int = 160, customers: int = 30,
         products: int = 40) -> MongoSeeded:
    """Create the run's collections in ``database`` and return what was written."""
    from bson import Binary, Decimal128, Int64, Regex, Timestamp
    from bson.binary import UuidRepresentation

    db = client.get_database(database)
    s = MongoSeeded(database=database, tag=tag)
    s.collections = {n: f"{n}_{tag}" for n in ("orders", "customers", "products")}
    t0 = dt.datetime(2026, 9, 1, 6, 0, tzinfo=dt.UTC)

    docs = [_order(i, t0) for i in range(1, orders + 1)]
    # Named documents carrying the probe facts.
    docs[4]["notes"] = FACTS["order_note"] + ". Gate pass printed at the dock office."
    docs[5]["tracking_events"] = [
        {"seq": k, "at": t0 + dt.timedelta(hours=k),
         "event": (FACTS["tracking_early"] if k == 3 else
                   FACTS["tracking_late"] if k == 120 else f"Hub scan {k} at {CITIES[k % 8]}")}
        for k in range(150)
    ]
    docs[6]["routing"] = {"plan": {"leg1": {"carrier": {"depot": {"shift": {"crew": {
        "lead": {"note": FACTS["deep"], "radio": "VHF-12"}}}}}}}}
    docs[7].update({
        "invoice_total": Decimal128(FACTS["decimal"]),
        "ledger_seq": Int64(int(FACTS["int64"])),
        "signature_png": Binary(b"\x89PNG\r\n\x1a\n" + bytes(range(64)), 0),
        "sku_pattern": Regex("^SKU-0[0-9]{3}$", "i"),
        "shipment_uuid": Binary.from_uuid(uuid.UUID("6f1c5d2e-1b7a-4c1e-9b3a-0d5e7c9a1b22"),
                                          UuidRepresentation.STANDARD),
        "oplog_ts": Timestamp(1790000000, 7),
        "coupon": None,
        "gift_wrap": False,
        "notes": "Corporate gifting order for Diwali; invoice total and ledger sequence "
                 "must survive ingestion exactly.",
    })
    res = db[s.collections["orders"]].insert_many(docs)
    s.ids.update(order_note=res.inserted_ids[4], tracking=res.inserted_ids[5],
                 deep=res.inserted_ids[6], bson_types=res.inserted_ids[7],
                 update_a=res.inserted_ids[10], update_b=res.inserted_ids[11],
                 delete_a=res.inserted_ids[12], delete_b=res.inserted_ids[13])
    s.counts["orders"] = len(docs)

    cust = []
    for i in range(1, customers + 1):
        note = (FACTS["customer"] if i == 9 else
                f"Account {i:03d} buys monthly through the {CITIES[i % 8]} distributor.")
        cust.append({"_id": f"CUST-{i:04d}", "name": NAMES[i % len(NAMES)] + f" #{i:03d}",
                     "tier": ["gold", "silver", "bronze"][i % 3], "notes": note,
                     "contacts": [{"kind": "email", "value": f"buyer{i}@example.in"},
                                  {"kind": "phone", "value": f"+91-98{i:08d}"}],
                     "updated_at": t0})
    db[s.collections["customers"]].insert_many(cust)
    s.counts["customers"] = len(cust)

    prods = []
    for i in range(1, products + 1):
        desc = (FACTS["product"] if i == 17 else
                f"Catalogue item {i:03d}: handloom cotton product from {CITIES[i % 8]} weavers.")
        prods.append({"_id": i, "title": f"Product {i:03d}", "description": desc,
                      "price": Decimal128(f"{99 + i * 11}.00"),
                      "tags": ["handmade", "fair-trade", CITIES[i % 8].lower()],
                      "updated_at": t0})
    db[s.collections["products"]].insert_many(prods)
    s.counts["products"] = len(prods)
    return s


def drop(client: Any, s: MongoSeeded) -> None:
    db = client.get_database(s.database)
    for name in s.collections.values():
        db.drop_collection(name)
