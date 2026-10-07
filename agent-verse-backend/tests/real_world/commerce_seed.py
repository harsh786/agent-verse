"""A realistic payments / commerce dataset in MongoDB for the MONGO-PIPELINE-* scenarios.

What a mid-size Indian D2C marketplace really keeps in MongoDB, in five collections:

* ``customers``        string ``_id`` (``CUS-00042``): tier, 6-level nested billing
                       address, invoice preferences, Decimal128 credit limit, nulls.
* ``orders``           ObjectId ``_id``: line items (Decimal128 money, nested GST),
                       a 7-level fulfilment plan, coupons that are ``null``.
* ``payment_events``   ObjectId ``_id``: gateway responses, retry-attempt arrays (one of
                       150 items), Int64 ledger sequences beyond 2^53.
* ``support_tickets``  int ``_id``: free text in English, Hindi, Japanese, Spanish and
                       German, a negation, and one ticket with planted PII.
* ``postmortems``      string ``_id`` (``PM-2026-014``): root cause, timeline arrays,
                       action items with owners.

Everything is generated deterministically from ``(size, seed)`` — the same call gives
byte-identical BSON — so the known-answer questions (:data:`QUESTIONS`) always point
at the same documents. ObjectIds are derived from a fixed timestamp base plus a hash,
so documents inserted later by a scenario (``ObjectId()`` = now) sort after every
seeded one and are found by the connector's ``_id`` scan.

Generic filler deliberately carries no e-mail address, phone number, card / account
number or PAN-shaped code, so the platform's PII screen (``pii_action=redact``) only
fires on the planted PII ticket and content checksums stay exact. Nothing here
imports ``app``; ``bson`` / ``pymongo`` are imported lazily.
"""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import json
import random
import re
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

SEED = 20261007
BASE_TS = int(dt.datetime(2026, 9, 1, tzinfo=dt.UTC).timestamp())
T0 = dt.datetime(2026, 9, 1, 6, 0, tzinfo=dt.UTC)
LOGICAL = ("customers", "orders", "payment_events", "support_tickets", "postmortems")
_WEIGHTS = {"customers": 0.12, "orders": 0.40, "payment_events": 0.28,
            "support_tickets": 0.15, "postmortems": 0.05}
_MINIMUM = {"customers": 30, "orders": 40, "payment_events": 30, "support_tickets": 30,
            "postmortems": 12}

CITIES = ["Pune", "Nagpur", "Kochi", "Guwahati", "Surat", "Indore", "Mysuru", "Ranchi",
          "Jaipur", "Bhopal", "Vadodara", "Madurai"]
STATES = {"Pune": "Maharashtra", "Nagpur": "Maharashtra", "Kochi": "Kerala",
          "Guwahati": "Assam", "Surat": "Gujarat", "Indore": "Madhya Pradesh",
          "Mysuru": "Karnataka", "Ranchi": "Jharkhand", "Jaipur": "Rajasthan",
          "Bhopal": "Madhya Pradesh", "Vadodara": "Gujarat", "Madurai": "Tamil Nadu"}
FIRST = ["Aarav", "Diya", "Kabir", "Ishita", "Vivaan", "Saanvi", "Reyansh", "Myra",
         "Zoë", "Łucja", "Olúwaseun", "Δέσποινα", "Søren", "Aigerim", "Thandiwe", "Mateo"]
LAST = ["Sharma", "Iyer", "Banerjee", "Khan", "Gill", "Nair", "Fernández", "Nowak",
        "Adébáyọ̀", "Παππά", "Kjær", "Nurlanovna", "Mokoena", "Rossi", "Joshi", "Patil"]
PRODUCTS = [("SKU-HL-0101", "Handloom cotton dhurrie, 4x6 ft"),
            ("SKU-BR-0207", "Brass Urli bowl, 10 inch"),
            ("SKU-TC-0315", "Terracotta planter set of three"),
            ("SKU-KH-0422", "Khadi kurta, indigo, size M"),
            ("SKU-JT-0530", "Jute storage basket, large"),
            ("SKU-SL-0618", "Sandalwood soap gift box"),
            ("SKU-CP-0726", "Copper water bottle, 1 litre"),
            ("SKU-MB-0834", "Madhubani painting, A3 framed"),
            ("SKU-BB-0942", "Bamboo serving tray"),
            ("SKU-PT-1050", "Pattachitra wall scroll")]
STATUSES = ["placed", "packed", "shipped", "delivered", "returned"]
METHODS = ["upi", "card", "netbanking", "wallet", "cod"]
GATEWAYS = ["razorpay", "payu", "cashfree", "ccavenue"]
TICKET_TOPICS = ["late delivery", "damaged item", "wrong size", "missing invoice",
                 "address change", "refund status", "duplicate charge", "gift wrap"]
SEVERITIES = ["SEV-3", "SEV-2", "SEV-2", "SEV-3", "SEV-4"]

# ── Planted, known-answer documents ──────────────────────────────────────────

PLANTED_ORDERS = ("ORD-770001", "ORD-770002", "ORD-770003", "ORD-770004", "ORD-770005",
                  "ORD-770006", "ORD-770007")
PII = {"email": "neha.kulkarni@example.org", "card": "4111 1111 1111 1111",
       "pan": "ABCPK1234F", "categories": ("EMAIL", "CREDIT_CARD", "PAN")}
LEDGER_SEQ = 9007199254740997  # > 2^53 and NOT Luhn-valid (never mistaken for a card)
BEYOND_WINDOW_ITEM = "Kondapalli wooden elephant, hand-painted"  # array item #131 of 140
ABSTAIN_QUESTION = ("What was the root cause of incident PM-2026-099 and who led the "
                    "response?")


@dataclass(frozen=True)
class Question:
    id: str
    kind: str  # numeric | date | nested | array | multilingual | negation | cross_collection | …
    question: str
    collection: str  # logical collection holding the answer
    key: str  # str(_id) of the document holding the answer
    must_contain: str  # text the right chunk carries
    answer_any: tuple[str, ...]
    answer_all: tuple[str, ...] = ()
    answer_forbidden: tuple[str, ...] = ()
    related: tuple[tuple[str, str], ...] = ()  # other (collection, key) the hop goes through

    def as_eval(self) -> dict[str, Any]:
        """The dict shape ``metrics.answer_correct`` scores."""
        return {"id": self.id, "kind": self.kind, "answer_any": list(self.answer_any),
                "answer_all": list(self.answer_all),
                "answer_forbidden": list(self.answer_forbidden),
                "chunk_must_contain": self.must_contain}


@dataclass
class CommerceData:
    seed: int
    size: int
    docs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    planted: dict[str, Any] = field(default_factory=dict)  # name -> _id

    @property
    def counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.docs.items()}

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def keys(self, logical: str) -> list[str]:
        return [str(d["_id"]) for d in self.docs[logical]]

    def find(self, logical: str, key: str) -> dict[str, Any]:
        return next(d for d in self.docs[logical] if str(d["_id"]) == key)


def object_id(ts: int, key: str) -> Any:
    """Deterministic ObjectId: 4-byte timestamp + 8 bytes of sha1(key)."""
    from bson import ObjectId

    return ObjectId(struct.pack(">I", ts) + hashlib.sha1(key.encode()).digest()[:8])


def luhn_ok(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if len(d) < 13:
        return False
    total = 0
    for i, n in enumerate(reversed(d)):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _dec(value: decimal.Decimal | str) -> Any:
    from bson import Decimal128

    return Decimal128(str(value))


def split_sizes(size: int) -> dict[str, int]:
    """Documents per collection for a requested total (each above its minimum)."""
    sizes = {k: max(_MINIMUM[k], int(size * w)) for k, w in _WEIGHTS.items()}
    sizes["orders"] += max(0, size - sum(sizes.values()))
    return sizes


def _name(rng: random.Random, i: int) -> str:
    return f"{FIRST[i % len(FIRST)]} {LAST[(i * 7 + rng.randrange(3)) % len(LAST)]}"


def _customer(rng: random.Random, i: int) -> dict[str, Any]:
    city = CITIES[i % len(CITIES)]
    return {
        "_id": f"CUS-{i:05d}",
        "name": _name(rng, i),
        "tier": ["bronze", "silver", "gold"][i % 3],
        "segment": ["retail", "boutique", "corporate"][(i // 3) % 3],
        "since": T0 - dt.timedelta(days=30 + (i * 11) % 900),
        "credit_limit": _dec(f"{5000 + (i * 1250) % 95000}.00"),
        "address": {"billing": {"geo": {"region": {"state": STATES[city], "city": {
            "name": city, "locality": f"Sector {1 + i % 40}", "pin_prefix": f"{400 + i % 99}"}}}}},
        "preferences": {"comms": {"invoice": {"language": ["English", "Hindi", "Marathi",
                                                            "Bengali"][i % 4],
                                              "format": "pdf", "consolidated": i % 5 == 0}}},
        "loyalty": {"points": (i * 37) % 4000, "expires_at": None},
        "notes": f"Buys {PRODUCTS[i % 10][1].lower()} regularly through the {city} store.",
    }


def _lines(rng: random.Random, i: int, n: int) -> tuple[list[dict[str, Any]], decimal.Decimal]:
    lines: list[dict[str, Any]] = []
    total = decimal.Decimal("0")
    for k in range(n):
        sku, title = PRODUCTS[(i + k * 3) % len(PRODUCTS)]
        qty = 1 + (i + k) % 4
        price = decimal.Decimal(f"{149 + (i * 13 + k * 71) % 4800}.{(i + k) % 4 * 25:02d}")
        total += price * qty
        lines.append({"sku": sku, "title": title, "qty": qty, "unit_price": _dec(price),
                      "tax": {"gst": {"rate": _dec("0.12"), "hsn": f"{6900 + k % 90}"}}})
    return lines, total


def _order(rng: random.Random, i: int, n_customers: int) -> dict[str, Any]:
    city = CITIES[(i * 5) % len(CITIES)]
    lines, total = _lines(rng, i, 1 + i % 4)
    return {
        "_id": object_id(BASE_TS + 10 * i, f"order:{i}"),
        "order_no": f"ORD-{i:06d}",
        "customer_id": f"CUS-{1 + i % n_customers:05d}",
        "status": STATUSES[i % len(STATUSES)],
        "placed_at": T0 + dt.timedelta(minutes=13 * i),
        "currency": "INR",
        "lines": lines,
        "total": _dec(total),
        "coupon": None if i % 3 else f"FEST{10 + i % 20}",
        "gift": i % 7 == 0,
        "fulfilment": {"plan": {"leg": {"hub": {"dock": {"slot": {
            "window": f"{6 + i % 12:02d}:00-{7 + i % 12:02d}:00",
            "note": f"Standard surface dispatch from the {city} hub."}}}}}},
        "notes": f"Order for the {city} region, packed in recyclable cartons.",
    }


def _payment(rng: random.Random, i: int, n_orders: int) -> dict[str, Any]:
    order_i = 1 + (i * 3) % n_orders
    kind = ["captured", "captured", "authorized", "refunded", "failed"][i % 5]
    return {
        "_id": object_id(BASE_TS + 10 * i + 3, f"payment:{i}"),
        "event_id": f"PAY-{i:06d}",
        "order_no": f"ORD-{order_i:06d}",
        "type": kind,
        "method": METHODS[i % len(METHODS)],
        "amount": _dec(f"{199 + (i * 29) % 9000}.00"),
        "at": T0 + dt.timedelta(minutes=13 * order_i + 2),
        "gateway": {"name": GATEWAYS[i % 4], "response": {
            "code": "00" if kind != "failed" else "U30",
            "message": "approved" if kind != "failed" else "transaction declined by remitter"}},
        "attempts": [{"n": k + 1, "result": "ok" if k == 0 else "retry"}
                     for k in range(1 + i % 3)],
        "description": f"Payment for order ORD-{order_i:06d}.",
    }


def _ticket(rng: random.Random, i: int, n_orders: int) -> dict[str, Any]:
    topic = TICKET_TOPICS[i % len(TICKET_TOPICS)]
    order_i = 1 + (i * 7) % n_orders
    return {
        "_id": 100000 + i,
        "ticket_no": f"TKT-{i:06d}",
        "order_no": f"ORD-{order_i:06d}",
        "language": "en",
        "subject": topic.capitalize(),
        "body": (f"Customer reports {topic} on order ORD-{order_i:06d}; agent "
                 f"{FIRST[i % len(FIRST)]} acknowledged and set a follow-up for day "
                 f"{1 + i % 6}."),
        "status": ["open", "pending", "resolved"][i % 3],
        "priority": ["low", "normal", "high"][(i // 2) % 3],
        "opened_at": T0 + dt.timedelta(hours=i),
        "tags": [topic.replace(" ", "-"), CITIES[i % len(CITIES)].lower()],
    }


def _postmortem(rng: random.Random, i: int) -> dict[str, Any]:
    started = T0 - dt.timedelta(days=200 - i * 3, hours=i % 24)
    minutes = 12 + (i * 17) % 160
    return {
        "_id": f"PM-2025-{i:03d}",
        "title": f"Degraded {['search', 'checkout', 'invoicing', 'notifications'][i % 4]} "
                 f"in {['ap-south-1', 'ap-south-2'][i % 2]}",
        "severity": SEVERITIES[i % len(SEVERITIES)],
        "started_at": started,
        "resolved_at": started + dt.timedelta(minutes=minutes),
        "duration_minutes": minutes,
        "summary": f"Elevated error rates for {minutes} minutes; mitigated by rolling back.",
        "root_cause": ["a bad feature-flag rollout", "connection pool exhaustion",
                       "a slow migration", "an unbounded retry loop"][i % 4],
        "timeline": [{"at": f"{(1 + k) % 24:02d}:{(k * 7) % 60:02d}",
                      "event": f"Step {k + 1} of the response"} for k in range(4)],
        "action_items": [{"owner": _name(rng, i + 3), "item": "Add an alert", "done": False}],
    }


def _plant(data: CommerceData) -> None:
    """Overwrite / append the documents that carry the known answers."""
    c, o, p, t, m = (data.docs[k] for k in LOGICAL)
    # customers
    c[0].update(_id="CUS-F0001", name="Meera Venkataraman", tier="platinum",
                notes="Key account; wants every invoice in Tamil.",
                preferences={"comms": {"invoice": {"language": "Tamil", "format": "pdf",
                                                   "consolidated": True}}})
    c[1].update(_id="CUS-F0002", name="Rohan D'Souza", notes="Boutique owner in Goa.",
                address={"billing": {"geo": {"region": {"state": "Goa", "city": {
                    "name": "Mapusa", "locality": "Aldona, North Goa",
                    "pin_prefix": "403"}}}}})
    c[2].update(_id="CUS-F0003", name="Ananya Bhattacharya", credit_limit=_dec("250000.00"),
                notes="Corporate gifting account with a raised credit limit of 250000.00 INR.")
    c[3].update(_id="CUS-F0004", name="株式会社サクラ物流",
                notes="Prefers consolidated monthly invoicing in Japanese yen.")
    # orders (planted order numbers are outside the generated ORD-000001.. range)
    o[0].update(order_no="ORD-770001", customer_id="CUS-F0001", coupon=None,
                total=_dec("48213.75"),
                notes="Fragile Bidriware vases, double-boxed with silica packs.")
    o[1]["order_no"] = "ORD-770002"
    o[1]["fulfilment"] = {"plan": {"leg": {"hub": {"dock": {"slot": {
        "window": "23:30-01:00",
        "note": "Dock 7B night slot at the Bhiwandi consolidation hub"}}}}}}
    o[1]["notes"] = "Night-shift consolidation order for the Bhiwandi hub."
    o[2].update(order_no="ORD-770003", status="delivered",
                delivered_at=dt.datetime(2026, 8, 15, 18, 40, tzinfo=dt.UTC),
                notes="Delivered on 2026-08-15, Independence Day, despite the holiday closure.")
    o[3].update(order_no="ORD-770004", coupon=None,
                notes="No coupon was applied; the full price was charged to the corporate card.")
    bulk = []
    for k in range(140):
        title = ("Channapatna lacquer toy train" if k == 6 else
                 BEYOND_WINDOW_ITEM if k == 130 else f"Assorted handicraft item {k + 1:03d}")
        bulk.append({"sku": f"SKU-BULK-{k + 1:03d}", "title": title, "qty": 1,
                     "unit_price": _dec("99.00"), "tax": {"gst": {"rate": _dec("0.12"),
                                                                 "hsn": "9503"}}})
    o[4].update(order_no="ORD-770005", lines=bulk, total=_dec("13860.00"),
                notes="Bulk order of 140 handicraft line items for a museum shop.")
    o[5].update(order_no="ORD-770006",
                notes="Terracotta planter arrived cracked; partial refund agreed.")
    o[6].update(order_no="ORD-770007",
                notes="Reefer truck shipment kept at 4°C for the organic honey jars.")
    # payment events
    p[0].update(event_id="PAY-990001", order_no="ORD-770001", type="chargeback",
                amount=_dec("48213.75"), method="card",
                gateway={"name": "razorpay", "response": {
                    "code": "4837", "message": "Chargeback reason code 4837: no cardholder "
                                               "authorization"}},
                description="Chargeback on the payment for the Bidriware vases order "
                            "ORD-770001.")
    attempts = [{"n": k + 1, "result": "retry",
                 "message": ("Issuer timeout from the Kotak switch, retried after 30 seconds"
                             if k == 3 else f"Soft decline {k + 1}, retry scheduled")}
                for k in range(150)]
    p[1].update(event_id="PAY-990002", attempts=attempts, type="failed",
                description="AutoPay retry storm on a subscription renewal.")
    p[2].update(event_id="PAY-990003", order_no="ORD-770006", type="refunded",
                amount=_dec("1999.00"),
                description="Partial refund of 1999.00 INR for the cracked terracotta planter.")
    p[3].update(event_id="PAY-990004", type="failed", method="upi",
                gateway={"name": "cashfree", "response": {
                    "code": "U16", "message": "UPI AutoPay mandate revoked by the customer in "
                                              "the PhonePe app"}},
                description="AutoPay debit for the monthly tea subscription.")
    from bson import Int64

    p[4].update(event_id="PAY-990005", ledger_seq=Int64(LEDGER_SEQ),
                description="Settlement batch SB-5521; the ledger sequence must survive "
                            "exactly.")
    # support tickets: multilingual, negation, PII
    t[0].update(ticket_no="TKT-880001", language="hi",
                body="पुणे गोदाम से डिलीवरी में पाँच दिन की देरी हुई; ग्राहक ने मुआवज़े के रूप में "
                     "₹500 का वाउचर स्वीकार किया।")
    t[1].update(ticket_no="TKT-880002", language="ja",
                body="配送ラベルの印刷ミスにより、名古屋倉庫で荷物が二日間保留されました。")
    t[2].update(ticket_no="TKT-880003", language="es",
                body="El cliente pidió cambiar la dirección de entrega a Valencia antes del envío.")
    t[3].update(ticket_no="TKT-880004", language="en", subject="Brass lamp complaint",
                body="The customer explicitly did NOT ask for a refund; they want a replacement "
                     "brass lamp shipped to Mysuru.")
    t[4].update(ticket_no="TKT-880005", order_no="ORD-770002", language="en",
                body="Courier missed the Dock 7B night slot; the delivery was rebooked for "
                     "Saturday at 06:00.")
    t[5].update(ticket_no="TKT-880006", language="de",
                body="Die Lieferung nach München wurde wegen eines Zollproblems um drei Tage "
                     "verzögert.")
    t[6].update(ticket_no="TKT-880007", language="en", subject="Duplicate debit",
                body=(f"Customer Neha Kulkarni wrote from {PII['email']} and read out card "
                      f"{PII['card']} and PAN {PII['pan']} about a duplicate debit on her "
                      "saree order."))
    # postmortems
    m[0].update(_id="PM-2026-014", title="Settlement webhook outage", severity="SEV-2",
                duration_minutes=47,
                summary="Settlement webhooks failed for 47 minutes; merchants saw delayed "
                        "payouts.",
                root_cause="An expired TLS certificate on the settlement webhook endpoint of "
                           "the payment gateway.",
                action_items=[{"owner": "Kavya Pillai",
                               "item": "Automate certificate renewal with 30-day expiry "
                                       "alerts", "done": False}])
    m[1].update(_id="PM-2026-021", severity="SEV-1",
                title="Checkout latency spike after Redis primary failover in ap-south-1",
                timeline=[{"at": "01:52", "event": "p99 checkout latency above 4 s"},
                          {"at": "02:14", "event": "Redis primary failover completed"},
                          {"at": "02:31", "event": "Latency back to baseline"}])
    data.planted = {
        "order_total": o[0]["_id"], "order_dock": o[1]["_id"], "order_date": o[2]["_id"],
        "order_coupon": o[3]["_id"], "order_bulk": o[4]["_id"], "order_planter": o[5]["_id"],
        "order_reefer": o[6]["_id"], "pay_chargeback": p[0]["_id"],
        "pay_attempts": p[1]["_id"], "pay_refund": p[2]["_id"], "pay_mandate": p[3]["_id"],
        "pay_ledger": p[4]["_id"], "ticket_pii": t[6]["_id"],
        # documents the incremental scenario changes / deletes (generic ones)
        "update_ticket": t[10]["_id"], "update_customer": c[10]["_id"],
        "update_order": o[12]["_id"], "delete_payment": p[20]["_id"],
        "delete_ticket": t[21]["_id"],
    }


def generate(size: int = 3000, seed: int = SEED) -> CommerceData:
    """The whole dataset for ``size`` documents (deterministic for ``(size, seed)``)."""
    rng = random.Random(seed)
    sizes = split_sizes(size)
    data = CommerceData(seed=seed, size=size)
    data.docs["customers"] = [_customer(rng, i) for i in range(1, sizes["customers"] + 1)]
    data.docs["orders"] = [_order(rng, i, sizes["customers"])
                           for i in range(1, sizes["orders"] + 1)]
    data.docs["payment_events"] = [_payment(rng, i, sizes["orders"])
                                   for i in range(1, sizes["payment_events"] + 1)]
    data.docs["support_tickets"] = [_ticket(rng, i, sizes["orders"])
                                    for i in range(1, sizes["support_tickets"] + 1)]
    data.docs["postmortems"] = [_postmortem(rng, i) for i in range(1, sizes["postmortems"] + 1)]
    _plant(data)
    return data


def digest(data: CommerceData) -> str:
    """sha256 of the canonical Extended JSON of every document (determinism check)."""
    from bson import json_util

    h = hashlib.sha256()
    for logical in LOGICAL:
        for doc in data.docs[logical]:
            h.update(json_util.dumps(doc, json_options=json_util.CANONICAL_JSON_OPTIONS,
                                     sort_keys=True).encode())
    return h.hexdigest()


# ── Known-answer questions ───────────────────────────────────────────────────


def questions(data: CommerceData) -> list[Question]:
    pl = {k: str(v) for k, v in data.planted.items()}
    q = Question
    return [
        q("Q01", "numeric", "What is the exact order total of the double-boxed Bidriware vases "
          "order?", "orders", pl["order_total"], "48213.75", ("48213.75", "48,213.75")),
        q("Q02", "nested", "Which dock slot is the night-shift Bhiwandi consolidation order "
          "assigned to?", "orders", pl["order_dock"], "Dock 7B", ("7B",)),
        q("Q03", "date", "On what date was order ORD-770003 delivered?", "orders",
          pl["order_date"], "2026-08-15", ("2026-08-15", "15 august", "august 15", "15/08/2026")),
        q("Q04", "null", "Was a coupon applied to order ORD-770004?", "orders",
          pl["order_coupon"], "No coupon was applied",
          ("no coupon", "not applied", "without a coupon", "wasn't applied", "was not")),
        q("Q05", "array", "Which Channapatna toy is part of the bulk museum-shop order "
          "ORD-770005?", "orders", pl["order_bulk"], "Channapatna lacquer toy train",
          ("toy train",)),
        q("Q06", "numeric", "At what temperature was the organic honey order ORD-770007 "
          "shipped?", "orders", pl["order_reefer"], "4°C", ("4°c", "4 °c", "4 degrees",
                                                           "4 deg")),
        q("Q07", "nested", "In which language does Meera Venkataraman want her invoices?",
          "customers", "CUS-F0001", "Tamil", ("tamil",)),
        q("Q08", "categorical", "Which customer tier is Meera Venkataraman on?", "customers",
          "CUS-F0001", "platinum", ("platinum",)),
        q("Q09", "nested", "Which locality is Rohan D'Souza billed at?", "customers",
          "CUS-F0002", "Aldona", ("aldona",)),
        q("Q10", "numeric", "What credit limit does Ananya Bhattacharya's corporate gifting "
          "account have?", "customers", "CUS-F0003", "250000",
          ("250000", "250,000", "2,50,000", "2.5 lakh")),
        q("Q11", "multilingual", "What invoicing preference does 株式会社サクラ物流 have?",
          "customers", "CUS-F0004", "monthly invoicing", ("monthly",)),
        q("Q12", "cross_collection", "What chargeback reason code was raised against the "
          "payment for the Bidriware vases order?", "payment_events", pl["pay_chargeback"],
          "4837", ("4837",), related=(("orders", pl["order_total"]),)),
        q("Q13", "array", "Which bank's switch timed out during the retries of payment "
          "PAY-990002?", "payment_events", pl["pay_attempts"], "Kotak switch", ("kotak",)),
        q("Q14", "cross_collection", "How much was refunded for the cracked terracotta "
          "planter?", "payment_events", pl["pay_refund"], "1999.00", ("1999", "1,999"),
          related=(("orders", pl["order_planter"]),)),
        q("Q15", "causal", "Why did the AutoPay payment PAY-990004 fail?", "payment_events",
          pl["pay_mandate"], "mandate revoked", ("mandate revoked", "revoked the mandate",
                                                 "mandate was revoked")),
        q("Q16", "numeric", "What is the ledger sequence of settlement batch SB-5521?",
          "payment_events", pl["pay_ledger"], str(LEDGER_SEQ), (str(LEDGER_SEQ),)),
        q("Q17", "multilingual", "पुणे गोदाम वाली देरी के लिए ग्राहक को कितने रुपये का वाउचर मिला?",
          "support_tickets", "100001", "₹500", ("500",)),
        q("Q18", "multilingual", "名古屋倉庫で荷物が保留された理由は何ですか？", "support_tickets",
          "100002", "配送ラベル", ("ラベル", "label")),
        q("Q19", "multilingual", "¿A qué ciudad pidió el cliente cambiar la dirección de "
          "entrega?", "support_tickets", "100003", "Valencia", ("valencia",)),
        q("Q20", "negation", "Did the customer with the brass lamp complaint ask for a refund?",
          "support_tickets", "100004", "did NOT ask for a refund",
          ("did not", "didn't", "no refund", "not ask", "replacement"),
          answer_forbidden=("yes, the customer asked", "yes, they asked", "yes.")),
        q("Q21", "cross_collection", "For when was the missed Dock 7B night-slot delivery "
          "rebooked?", "support_tickets", "100005", "Saturday at 06:00",
          ("saturday", "06:00"), related=(("orders", pl["order_dock"]),)),
        q("Q22", "multilingual", "Warum wurde die Lieferung nach München verzögert?",
          "support_tickets", "100006", "Zollproblems", ("zoll", "customs")),
        q("Q23", "causal", "What was the root cause of incident PM-2026-014?", "postmortems",
          "PM-2026-014", "expired TLS certificate",
          ("expired tls certificate", "certificate expired", "expired certificate")),
        q("Q24", "numeric", "How many minutes did the settlement webhook outage last?",
          "postmortems", "PM-2026-014", "47 minutes", ("47",)),
        q("Q25", "entity", "Who owns the certificate-renewal automation action item?",
          "postmortems", "PM-2026-014", "Kavya Pillai", ("kavya pillai",)),
        q("Q26", "array", "At what time did the Redis primary failover complete during "
          "PM-2026-021?", "postmortems", "PM-2026-021", "02:14", ("02:14", "2:14")),
        q("Q27", "categorical", "What severity was the checkout latency spike after the Redis "
          "primary failover?", "postmortems", "PM-2026-021", "SEV-1",
          ("sev-1", "sev 1", "sev1", "severity 1")),
    ]


# ── Matching platform answers to documents ──────────────────────────────────


def doc_path(database: str, collection: str, key: str) -> str:
    """The tail of the ``source_url`` the MongoDB connector gives a document."""
    return f"/{database}/{collection}/{quote(key, safe='')}"


def hit_url(hit: dict[str, Any]) -> str:
    meta = hit.get("metadata") or {}
    return str(hit.get("source_url") or meta.get("source_url") or hit.get("source") or "")


def hit_is(hit: dict[str, Any], database: str, collection: str, key: str) -> bool:
    return hit_url(hit).endswith(doc_path(database, collection, key))


def rank_in(hits: list[dict[str, Any]], database: str, collection: str, key: str,
            must_contain: str) -> int | None:
    """1-based rank of the first hit from the right document holding the fact."""
    from tests.real_world.metrics import norm

    for i, h in enumerate(hits, start=1):
        if hit_is(h, database, collection, key) and norm(must_contain) in norm(h.get("content")):
            return i
    return None


def duplicates(values: list[Any]) -> dict[Any, int]:
    """Values that occur more than once, with their counts."""
    seen: dict[Any, int] = {}
    for v in values:
        seen[v] = seen.get(v, 0) + 1
    return {k: n for k, n in seen.items() if n > 1}


def duplicate_hits(hits: list[dict[str, Any]]) -> list[str]:
    """Search hits that are the same chunk twice (same document URL + same content)."""
    from tests.real_world.metrics import norm

    keys = [f"{hit_url(h)}|{hashlib.sha1(norm(h.get('content')).encode()).hexdigest()}"
            for h in hits]
    return sorted(duplicates(keys))


def leaf_values(doc: Any, *, min_len: int = 4) -> list[str]:
    """Every string-ish scalar of a document worth finding verbatim in its chunks.

    Strings (>= ``min_len`` chars, first 100 array items, depth <= 5 like the
    connector renders individually), Decimal128 as their exact text. Used for
    the content checksum of sampled documents.
    """
    out: list[str] = []

    def walk(v: Any, depth: int) -> None:
        if depth > 5:
            return
        if isinstance(v, dict):
            for x in v.values():
                walk(x, depth + 1)
        elif isinstance(v, list | tuple):
            for x in v[:100]:
                walk(x, depth + 1)
        elif isinstance(v, str):
            if len(v) >= min_len:
                out.append(v)
        elif type(v).__name__ == "Decimal128":
            out.append(str(v))
    walk(doc, 0)
    return out


def content_checksum(values: list[str]) -> str:
    from tests.real_world.metrics import norm

    return hashlib.sha256("\x1f".join(sorted(norm(v) for v in values)).encode()).hexdigest()


_ABSTAIN_PHRASES = ("no information", "not found", "does not contain", "doesn't contain",
                    "cannot find", "can't find", "could not find", "couldn't find",
                    "don't have", "do not have", "no record", "not available", "unable to",
                    "insufficient", "not mentioned", "no mention", "no details",
                    "not in the provided", "no relevant", "unknown", "not specified")


def abstained(status: int, body: Any) -> bool:
    """Did ``/rag/query`` decline to answer (no evidence) instead of inventing one?

    Honest answers: 422 ``answer_ungrounded``; 200 flagged ``grounded: false`` or
    ``low_confidence``; or an answer that says the knowledge does not hold it.
    """
    if status == 422:
        detail = body.get("detail") if isinstance(body, dict) else None
        return (isinstance(detail, dict) and detail.get("code") == "answer_ungrounded") or \
            "ungrounded" in str(body)
    if status != 200 or not isinstance(body, dict):
        return False
    if body.get("grounded") is False or body.get("low_confidence") is True:
        return True
    ans = body.get("answer")
    text = str(ans.get("text") or ans.get("answer") or ans) if isinstance(ans, dict) \
        else str(ans or "")
    low = " ".join(text.lower().split())
    return not low or any(p in low for p in _ABSTAIN_PHRASES)


_DIGIT_RUN = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def accidental_pii(text: str) -> list[str]:
    """Shapes in generic text the platform's PII screen would redact (checked offline)."""
    found = [m.group(0) for m in _DIGIT_RUN.finditer(text) if luhn_ok(m.group(0))]
    found += re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text)
    found += re.findall(r"\b[A-Z]{3}[PCHABGJLFT][A-Z]\d{4}[A-Z]\b", text)
    found += re.findall(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}\b", text)
    return found


def as_text(doc: dict[str, Any]) -> str:
    from bson import json_util

    return json_util.dumps(doc, ensure_ascii=False)


# ── Seeding / changes against a real MongoDB ────────────────────────────────


def collection_names(tag: str) -> dict[str, str]:
    return {k: f"{k}_{tag}" for k in LOGICAL}


def seed(client: Any, database: str, tag: str, data: CommerceData, batch: int = 1000
         ) -> dict[str, str]:
    """Insert the dataset into ``<logical>_<tag>`` collections; returns logical -> name."""
    names = collection_names(tag)
    db = client.get_database(database)
    for logical, name in names.items():
        docs = data.docs[logical]
        for start in range(0, len(docs), batch):
            db[name].insert_many(docs[start:start + batch], ordered=False)
    return names


def drop(client: Any, database: str, names: dict[str, str]) -> None:
    db = client.get_database(database)
    for name in names.values():
        db.drop_collection(name)


INSERTED_FACT = "Rush Onam order of 36 banana-leaf platters for the Kochi store"
UPDATED_TICKET = "Resolution: replacement shipped via Blue Dart, airway bill BD-7781-KX"
UPDATED_LANGUAGE = "Malayalam"


def new_orders(n: int = 5) -> list[dict[str, Any]]:
    """Orders inserted after the first sync (fresh ObjectIds sort after the seeded ones)."""
    from bson import ObjectId

    out = []
    for k in range(n):
        out.append({"_id": ObjectId(), "order_no": f"ORD-88{k:04d}", "status": "placed",
                    "currency": "INR", "total": _dec(f"{1200 + k * 10}.00"),
                    "lines": [{"sku": PRODUCTS[k][0], "title": PRODUCTS[k][1], "qty": 1}],
                    "notes": INSERTED_FACT if k == 0 else
                    f"Post-sync order {k} for the {CITIES[k]} store."})
    return out


# ── Poison / drift documents (failure scenarios) ────────────────────────────


def nested(depth: int, leaf: str) -> dict[str, Any]:
    doc: dict[str, Any] = {"leaf": leaf}
    for k in range(depth - 1):
        doc = {f"l{depth - 1 - k}": doc}
    return doc


def nesting_depth(doc: Any) -> int:
    if isinstance(doc, dict):
        return 1 + max((nesting_depth(v) for v in doc.values()), default=0)
    if isinstance(doc, list):
        return 1 + max((nesting_depth(v) for v in doc), default=0)
    return 0


OVERSIZE_BYTES = 11 * 1024 * 1024  # above the pipeline's 10 MiB per-document cap


def poison_docs() -> dict[str, dict[str, Any]]:
    """Valid BSON that stresses the connector / pipeline (inserted with pymongo)."""
    return {
        "oversize": {"_id": "POISON-OVERSIZE", "kind": "oversize",
                     "blob": "x" * OVERSIZE_BYTES,
                     "notes": "Archived raw gateway dump, far larger than any document cap."},
        "deep": {"_id": "POISON-DEEP", "kind": "deep", "notes": "Pathological nesting.",
                 "tree": nested(95, "Bottom of the 95-level settlement tree")},
        "huge_array": {"_id": "POISON-ARRAY", "kind": "huge_array",
                       "notes": "Click-stream array with 20000 events.",
                       "events": [{"seq": k, "page": f"/p/{k % 97}"} for k in range(20000)]},
        "odd_bson": {"_id": "POISON-ODD", "kind": "odd_bson",
                     "notes": "Control characters \x00\x01\x07 and lone ​ zero-width "
                              "spaces in a merchant note.",
                     "empty": {}, "empty_list": [], "neg_zero": -0.0, "nan": float("nan")},
    }


def invalid_utf8_raw(key: str) -> bytes:
    """A BSON document whose ``notes`` string holds invalid UTF-8 (0xC3 0x28).

    Drivers refuse to *encode* such a string, so it is assembled by hand and inserted
    as ``RawBSONDocument``; servers store it as is (older exports really contain it).
    """
    def cstring(s: str) -> bytes:
        return s.encode() + b"\x00"

    def string_el(name: str, value: bytes) -> bytes:
        return b"\x02" + cstring(name) + struct.pack("<i", len(value) + 1) + value + b"\x00"

    body = (string_el("_id", key.encode()) + string_el("kind", b"invalid_utf8")
            + string_el("notes", b"Merchant note with a broken byte \xc3\x28 from a legacy export"))
    return struct.pack("<i", len(body) + 5) + body + b"\x00"


def drifted_payments(n: int = 6) -> list[dict[str, Any]]:
    """Schema v2 payment events: amount in integer paise, method as an object, tags list."""
    from bson import ObjectId

    return [{"_id": ObjectId(), "schema_version": 2, "event_id": f"PAY2-{k:04d}",
             "amount_minor": 125000 + k * 100, "currency": "INR",
             "method": {"type": "upi", "vpa_handle": "merchant-collect"},
             "tags": ["schema-v2", "drift"],
             "description": (f"Schema v2 settlement event {k} for the Thrissur jewellery "
                             "merchant" if k == 0 else f"Schema v2 event {k}.")}
            for k in range(n)]


# ── Scale generator (streamed, never held in memory) ────────────────────────


SCALE_NEEDLES = {
    17: "Needle: consignment KX-17 of saffron from Pampore cleared at 03:10",
    4242: "Needle: chargeback CB-4242 disputed by a Lucknow chikankari boutique",
    65535: "Needle: refund RF-65535 issued for a Kanchipuram silk saree",
}


def scale_docs(n: int, seed_value: int = SEED) -> Iterator[dict[str, Any]]:
    """``n`` realistic order documents for the scale scenario (deterministic)."""
    rng = random.Random(seed_value)
    for i in range(1, n + 1):
        doc = _order(rng, i, 5000)
        doc["_id"] = object_id(BASE_TS + i, f"scale:{i}")
        if i in SCALE_NEEDLES:
            doc["notes"] = SCALE_NEEDLES[i]
        yield doc


def needle_keys(n: int) -> dict[int, str]:
    return {i: str(object_id(BASE_TS + i, f"scale:{i}")) for i in SCALE_NEEDLES if i <= n}


def to_json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)
