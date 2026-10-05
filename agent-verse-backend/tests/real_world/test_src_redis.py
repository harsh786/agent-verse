"""SRC-REDIS-*: Redis as a knowledge source on the live stack (P1c / A5).

Throwaway servers on the compose network (``redis/redis-stack-server``, so RedisJSON
is there), operator-allowlisted for the connector egress guard, labelled
``p1c=live-test``:

* ``rw-redis``: ``requirepass`` plus an ACL user ``rwreader`` (``~rw:*``, ``+@read``,
  ``+info``, ``+json.get``) — the least-privilege reader the stack connects as — and
  ``rwnojson`` (the same without ``JSON.GET``).
* ``rw-redis-tls``: TLS only (certificate from a private test CA), ``requirepass``.

Seeding runs from this host through the published ports with the default user.

Environment (else SKIPPED): ``RW_REDIS_PASSWORD``, ``RW_REDIS_ACL_PASSWORD``,
``RW_REDIS_NOJSON_PASSWORD``
(+ ``RW_REDIS_SEED_PORT`` 56379, ``RW_REDIS_TLS_SEED_PORT`` 56380, ``RW_TLS_DIR``).
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from typing import Any

import pytest

from tests.real_world import kb
from tests.real_world import source_jobs as sj
from tests.real_world import sources as srcs
from tests.real_world.helpers import LiveAPI, mask, register_secret, tag, wait_until
from tests.real_world.metrics import norm, record

FAMILY = "nosql_database"
FACTS = {
    "string": "Darjeeling first-flush muscatel, estate lot DJ-117, best before March 2027",
    "hash": "Prefers delivery after 7 pm; gate code 4471; contact Meenakshi Sundaram",
    "list": "Ticket escalated to the Coimbatore field team after the third failed visit",
    "set": "monsoon-proof-packaging",
    "zset": "Bhiwandi-cold-chain",
    "stream": "pallet scanned at Nhava Sheva gate 3 seal NS-4471",
    "json": "Final mile by courier Thandiwe Mokoena on an e-cargo bike",
    "big_tail": "TAIL-MARKER-beyond-the-value-cap",
    "hash_tail": "field-79-beyond-the-item-cap",
    "outside": "This key is outside the configured patterns and must never be indexed",
}


def _env(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        pytest.skip(f"needs {name}: the throwaway Redis servers (see module docstring)")
    register_secret(value)
    return value


def _tls_dir() -> str:
    path = os.getenv("RW_TLS_DIR", "")
    if not path or not os.path.exists(os.path.join(path, "ca.pem")):
        pytest.skip("needs RW_TLS_DIR with the test CA (ca.pem, other-ca.pem)")
    return path


def _pem(name: str) -> str:
    with open(os.path.join(_tls_dir(), name), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def seed() -> Iterator[Any]:
    import redis

    client = redis.Redis(host="127.0.0.1", port=int(os.getenv("RW_REDIS_SEED_PORT", "56379")),
                         password=_env("RW_REDIS_PASSWORD"), socket_timeout=10)
    prefixes: list[str] = []
    client.prefixes = prefixes  # type: ignore[attr-defined]
    yield client
    for prefix in prefixes:
        with contextlib.suppress(Exception):
            for key in client.scan_iter(match=f"{prefix}*", count=500):
                client.delete(key)
    client.close()


def _reader(**over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {"host": "rw-redis", "port": 6379, "auth_type": "acl",
                           "username": "rwreader", "password": _env("RW_REDIS_ACL_PASSWORD")}
    cfg.update(over)
    return cfg


def _source(api: LiveAPI, cleanup: Any, cid: str, cfg: dict[str, Any],
            expect: int = 201) -> dict[str, Any]:
    return sj.create_source(api, cleanup, family=FAMILY, source_type="redis", config=cfg,
                            collection_id=cid, expect=expect)


def _count(api: LiveAPI, cid: str) -> int:
    return int(kb.documents_page(api, cid, 1, 0).get("total") or 0)


def _hit_with(api: LiveAPI, cid: str, q: str, needle: str, k: int = 5) -> dict[str, Any] | None:
    for h in kb.search(api, cid, q, top_k=k):
        if norm(needle) in norm(h.get("content")):
            return h
    return None


def _citation(hit: dict[str, Any]) -> str:
    meta = hit.get("metadata") or {}
    return str(hit.get("source_url") or meta.get("source_url") or hit.get("source")
               or meta.get("source") or "")


def _seed_types(r: Any, p: str) -> dict[str, str]:
    """One key per Redis type under ``p`` (+ an oversized string and hash)."""
    keys = {
        "string": f"{p}:product:dj-117", "hash": f"{p}:customer:1042",
        "list": f"{p}:ticket:88310:timeline", "set": f"{p}:product:dj-117:tags",
        "zset": f"{p}:warehouse:rank", "stream": f"{p}:order:55102:events",
        "json": f"{p}:order:55102", "big": f"{p}:catalogue:blob",
        "wide": f"{p}:customer:wide",
    }
    r.set(keys["string"], f"Product note: {FACTS['string']}. Packed in 250 g tins.")
    r.hset(keys["hash"], mapping={"name": "Meenakshi Sundaram", "city": "Madurai",
                                  "tier": "gold", "delivery_note": FACTS["hash"]})
    r.rpush(keys["list"], "Ticket 88310 opened: refrigerator not cooling",
            "Visit 1: technician could not access the building",
            "Visit 2: spare part missing", FACTS["list"])
    r.sadd(keys["set"], "organic", "fair-trade", FACTS["set"], "first-flush")
    r.zadd(keys["zset"], {"Pune-hub": 91.5, FACTS["zset"]: 97.25, "Guwahati-transit": 72.0})
    for k, event in enumerate(["order placed", "packed at Bhiwandi", FACTS["stream"],
                               "out for delivery"]):
        r.xadd(keys["stream"], {"seq": str(k), "event": event})
    r.execute_command("JSON.SET", keys["json"], "$", json.dumps({
        "order": "55102", "customer": {"name": "Ishaan Raghunathan", "city": "Bengaluru"},
        "lines": [{"sku": "DJ-117", "qty": 3}],
        "delivery": {"route": {"leg": {"courier": {"note": FACTS["json"]}}}}}))
    r.set(keys["big"], ("Catalogue export, page filler about handloom products. " * 120)
          + FACTS["big_tail"])
    r.hset(keys["wide"], mapping={f"field-{i:02d}": (FACTS["hash_tail"] if i == 79 else
                                                     f"attribute value {i} for the wide customer")
                                  for i in range(80)})
    return keys


# ── SRC-REDIS-TYPES ─────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-REDIS-TYPES")
def test_redis_every_type_and_patterns(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                       seed: Any) -> None:
    """Every data type, several key patterns, keys outside them, value / item caps;
    a second Source restricted to two types."""
    p = f"rw:{tag()}"
    seed.prefixes.append(p)
    keys = _seed_types(seed, p)
    seed.set(f"{p}-outside:note", FACTS["outside"])  # matches no pattern below
    patterns = [f"{p}:product:*", f"{p}:customer:*", f"{p}:ticket:*", f"{p}:order:*",
                f"{p}:warehouse:*", f"{p}:catalogue:*"]
    cid = srcs.create_collection(api, cleanup, "rw-redis-types")
    sid = _source(api, cleanup, cid, _reader(key_patterns=",".join(patterns),
                                             max_value_bytes=4096, max_items=50))["id"]
    evidence.update(source_id=sid, collection_id=cid)
    v = sj.validate(api, family=FAMILY, source_type="redis", config=_reader(
        key_patterns=",".join(patterns)))
    evidence["validate"] = mask(v)[:400]
    job = sj.sync(api, sid, timeout=900)
    evidence["sync1"] = job
    soft: list[str] = []
    if str(job.get("status")).lower() not in sj.COMPLETED:
        soft.append(f"sync {job.get('status')}: {job.get('error_message')}")
    n = _count(api, cid)
    evidence["documents"] = n
    if n != len(keys):
        soft.append(f"{n} documents, expected {len(keys)} (one per key)")
    probes = {
        "string": ("Darjeeling first flush muscatel estate lot DJ-117", FACTS["string"]),
        "hash": ("delivery after 7 pm gate code Meenakshi Sundaram", FACTS["hash"]),
        "list": ("ticket escalated Coimbatore field team failed visit", FACTS["list"]),
        "set": ("product tags monsoon proof packaging organic", FACTS["set"]),
        "zset": ("warehouse rank Bhiwandi cold chain score", FACTS["zset"]),
        "stream": ("order events pallet scanned Nhava Sheva seal", FACTS["stream"]),
        "json": ("courier Thandiwe Mokoena e-cargo bike", FACTS["json"]),
    }
    cites: dict[str, str] = {}
    for name, (q, needle) in probes.items():
        hit = _hit_with(api, cid, q, needle, 8)
        if hit is None:
            soft.append(f"not searchable: {name}")
            continue
        cites[name] = _citation(hit)
        if not cites[name].startswith("redis://") or keys[name].split(":")[-1] not in \
                cites[name].replace("%3A", ":"):
            soft.append(f"{name}: citation {cites[name]!r} does not name the key")
    evidence["citations"] = cites
    if _hit_with(api, cid, "TAIL-MARKER beyond the value cap", FACTS["big_tail"], 8):
        soft.append("the oversized string was indexed past max_value_bytes")
    if _hit_with(api, cid, "field 79 beyond the item cap", FACTS["hash_tail"], 8):
        soft.append("the wide hash was indexed past max_items")
    if _hit_with(api, cid, "outside the configured patterns", FACTS["outside"], 8):
        soft.append("a key outside the patterns was indexed")
    zset = _hit_with(api, cid, "warehouse rank Bhiwandi cold chain score", FACTS["zset"], 8)
    if zset is not None and "97.25" not in str(zset.get("content")):
        soft.append("zset member indexed without its score")

    # A second Source reading only hashes and JSON documents.
    cid2 = srcs.create_collection(api, cleanup, "rw-redis-types2")
    sid2 = _source(api, cleanup, cid2, _reader(key_patterns=f"{p}:*", types="hash,json"))["id"]
    job2 = sj.sync(api, sid2, timeout=600)
    evidence["typed_sync"] = job2
    n2 = _count(api, cid2)
    if n2 != 3:
        soft.append(f"types=hash,json indexed {n2} documents, expected 3 (2 hashes + 1 JSON)")
    record(evidence, keys=len(keys), sync_s=job.get("wall_s"))
    assert not soft, "; ".join(soft)


# ── SRC-REDIS-INCREMENTAL-RESUME ────────────────────────────────────────────


@pytest.mark.scenario("SRC-REDIS-INCREMENTAL-RESUME")
def test_redis_keyspace_over_several_runs_and_changes(api: LiveAPI, cleanup: Any,
                                                      evidence: dict[str, Any],
                                                      seed: Any) -> None:
    """A keyspace larger than max_keys_per_sync is covered over several runs; then
    edits and new keys are indexed and deleted keys reconciled away."""
    p = f"rw:{tag()}"
    seed.prefixes.append(p)
    total, per_run = int(os.getenv("RW_REDIS_KEYS", "600")), 250
    pipe = seed.pipeline()
    for i in range(total):
        pipe.hset(f"{p}:sku:{i:05d}", mapping={
            "title": f"Handloom cotton saree, design {i:05d}",
            "story": f"Woven in {['Pochampally', 'Chanderi', 'Kanchipuram'][i % 3]} by the "
                     f"cooperative of weaver family {i:05d}; natural dyes, 6.3 metres."})
    pipe.execute()
    cid = srcs.create_collection(api, cleanup, "rw-redis-resume")
    sid = _source(api, cleanup, cid, _reader(key_patterns=f"{p}:sku:*",
                                             max_keys_per_sync=per_run))["id"]
    evidence.update(source_id=sid, keys=total, per_run=per_run)
    soft: list[str] = []
    runs: list[dict[str, Any]] = []
    started = time.monotonic()
    while len(runs) < 6 and _count(api, cid) < total:
        runs.append(sj.sync(api, sid, timeout=900))
    evidence["runs"] = [(r.get("status"), r.get("docs_indexed"), r.get("wall_s")) for r in runs]
    first_pass_s = round(time.monotonic() - started, 1)
    n = _count(api, cid)
    if n != total:
        soft.append(f"{n} of {total} keys indexed after {len(runs)} runs")
    expected_runs = -(-total // per_run)
    if len(runs) != expected_runs or any(int(r.get("docs_indexed") or 0) > per_run for r in runs):
        soft.append(f"{len(runs)} runs (expected {expected_runs}, each at most {per_run} keys)")

    # Changes: 2 edited, 2 new, 3 deleted.
    seed.hset(f"{p}:sku:00007", "story", "Re-dyed with indigo from the Tamil Nadu cooperative "
                                         "after a customer complaint about fading.")
    seed.hset(f"{p}:sku:00008", "title", "Handloom cotton saree, design 00008, festival edition")
    seed.hset(f"{p}:sku:90001", mapping={"title": "New arrival: Bhagalpur tussar silk stole",
                                          "story": "Hand-reeled tussar silk from Bhagalpur."})
    seed.hset(f"{p}:sku:90002", mapping={"title": "New arrival: Ilkal saree",
                                          "story": "Ilkal weave with a red pallu, Karnataka."})
    gone = [f"{p}:sku:{i:05d}" for i in (11, 12, 13)]
    seed.delete(*gone)
    later: list[dict[str, Any]] = []
    for _ in range(expected_runs + 1):  # a full pass over the changed keyspace
        later.append(sj.sync(api, sid, timeout=900))
    indexed_after = sum(int(r.get("docs_indexed") or 0) for r in later)
    evidence["change_runs"] = [(r.get("status"), r.get("docs_indexed"), r.get("docs_skipped"))
                               for r in later]
    if indexed_after != 4:
        soft.append(f"{indexed_after} keys indexed after the changes, expected 4 "
                    "(2 edited + 2 new; unchanged ones skipped)")
    if _hit_with(api, cid, "indigo Tamil Nadu cooperative fading complaint", "indigo") is None:
        soft.append("edited hash not searchable")
    if _hit_with(api, cid, "Bhagalpur tussar silk stole", "Bhagalpur") is None:
        soft.append("new key not searchable")
    rec = api.post(f"/sources/{sid}/reconcile")
    evidence["reconcile_http"] = rec.status_code
    if rec.status_code != 202:
        soft.append(f"deleted keys cannot be reconciled: {rec.status_code} "
                    f"{mask(rec.text)[:200]}")
    else:
        try:
            wait_until(lambda: _count(api, cid), timeout=300, interval=5,
                       desc="deleted keys removed", done=lambda c: c == total + 2 - 3)
        except AssertionError as exc:
            soft.append(f"reconcile did not remove exactly the 3 deleted keys: {exc}")
    evidence["documents_final"] = _count(api, cid)
    record(evidence, keys=total, first_pass_s=first_pass_s,
           keys_per_s=round(total / max(1.0, first_pass_s), 2))
    assert not soft, "; ".join(soft)


# ── SRC-REDIS-TLS-AUTH ──────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-REDIS-TLS-AUTH")
def test_redis_tls_and_auth(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                            seed: Any) -> None:
    import redis

    password = _env("RW_REDIS_PASSWORD")
    tls_seed = redis.Redis(host="127.0.0.1", port=int(os.getenv("RW_REDIS_TLS_SEED_PORT", "56380")),
                           password=password, ssl=True,
                           ssl_ca_certs=os.path.join(_tls_dir(), "ca.pem"), socket_timeout=10)
    p = f"rw:{tag()}"
    seed.prefixes.append(p)
    tls_seed.set(f"{p}:policy", "Returns accepted within 10 days at the Mysuru store only, "
                                "with the original GST invoice.")
    seed.set(f"{p}:open", "Open note readable by the ACL reader: Ranchi depot shifts at 05:30.")
    seed.set(f"secret:{p}", "Payroll export: must never be readable by the ACL reader.")
    soft: list[str] = []
    out: dict[str, Any] = {}
    try:
        cid = srcs.create_collection(api, cleanup, "rw-redis-tls")
        tls_cfg = {"host": "rw-redis-tls", "port": 6379, "auth_type": "password",
                   "password": password, "tls": True, "tls_ca_pem": _pem("ca.pem"),
                   "key_patterns": f"{p}:*"}
        sid = _source(api, cleanup, cid, tls_cfg)["id"]
        job = sj.sync(api, sid, timeout=600)
        out["tls_with_ca"] = (job.get("status"), job.get("docs_indexed"))
        if str(job.get("status")).lower() not in sj.COMPLETED or _count(api, cid) != 1:
            soft.append(f"TLS + CA + password: {job.get('status')} {job.get('error_message')}")
        elif _hit_with(api, cid, "returns accepted Mysuru store GST invoice", "Mysuru") is None:
            soft.append("TLS-synced key not searchable")
        cid2 = srcs.create_collection(api, cleanup, "rw-redis-acl")
        sid2 = _source(api, cleanup, cid2, _reader(key_patterns=f"{p}:*"))["id"]
        job2 = sj.sync(api, sid2, timeout=600)
        out["acl_reader"] = (job2.get("status"), job2.get("docs_indexed"))
        if str(job2.get("status")).lower() not in sj.COMPLETED or _count(api, cid2) != 1:
            soft.append(f"ACL reader on its own keys: {job2.get('status')} "
                        f"{job2.get('error_message')}")
        failures = {
            "TLS without the CA": ({**tls_cfg, "tls_ca_pem": ""}, ("certificate", "ssl", "tls")),
            "TLS with a wrong CA": ({**tls_cfg, "tls_ca_pem": _pem("other-ca.pem")},
                                    ("certificate", "ssl", "tls")),
            "plain TCP to the TLS port": ({**tls_cfg, "tls": False, "tls_ca_pem": ""},
                                          ("connection", "closed", "reset", "timeout", "tls",
                                           "protocol")),
            "wrong password": ({**tls_cfg, "password": "not-the-password-0000"},
                               ("wrongpass", "invalid", "password", "auth")),
            "ACL user on keys outside its pattern": (_reader(key_patterns=f"secret:{p}"),
                                                     ("noperm", "permission", "no permissions")),
            "unreachable port": (_reader(port=6390, key_patterns=f"{p}:*"),
                                 ("connect", "refused", "timeout", "unreachable")),
        }
        for name, (cfg, words) in failures.items():
            v = sj.validate(api, family=FAMILY, source_type="redis", config=cfg)
            sid_f = _source(api, cleanup, cid2, cfg)["id"]
            jf = sj.sync(api, sid_f, timeout=300)
            err = f"{jf.get('error_message')} {v.get('errors')}".lower()
            out[name] = {"validate": v.get("valid"), "sync": jf.get("status"),
                         "indexed": jf.get("docs_indexed"), "error": mask(jf.get("error_message"))[:200]}
            if str(jf.get("status")).lower() in sj.COMPLETED:
                soft.append(f"{name}: sync completed ({jf.get('docs_indexed')} indexed)")
            elif not any(w in err for w in words):
                soft.append(f"{name}: no reason among {words}: {mask(err)[:200]}")
            if password in err:
                soft.append(f"{name}: the password is in the error")
        if _hit_with(api, cid2, "payroll export", "Payroll export") is not None:
            soft.append("a key outside the ACL pattern was indexed")
        # A user that may not run JSON.GET: the JSON key fails alone, the rest syncs.
        seed.execute_command("JSON.SET", f"{p}:doc", "$", json.dumps(
            {"note": "Pallet labels for the Hubballi depot use the new GS1 layout."}))
        cid3 = srcs.create_collection(api, cleanup, "rw-redis-nojson")
        sid3 = _source(api, cleanup, cid3, _reader(
            username="rwnojson", password=_env("RW_REDIS_NOJSON_PASSWORD"),
            key_patterns=f"{p}:*"))["id"]
        j3 = sj.sync(api, sid3, timeout=300)
        out["no JSON.GET"] = {"sync": j3.get("status"), "indexed": j3.get("docs_indexed"),
                              "failed": j3.get("docs_failed"),
                              "error": mask(j3.get("error_message"))[:200]}
        if str(j3.get("status")).lower() != "partial" or int(j3.get("docs_indexed") or 0) != 1 \
                or "json.get" not in str(j3.get("error_message")).lower() \
                or f"{p}:doc" not in str(j3.get("error_message")):
            soft.append(f"user without JSON.GET: {out['no JSON.GET']} (expected partial: the "
                        "plain key indexed, the JSON key failed with its reason)")
        # Refusals on save.
        refused = {
            "platform redis": {"host": "redis", "port": 6379},
            "localhost": {"host": "localhost", "port": 6379},
            "metadata IP": {"host": "169.254.169.254", "port": 6379},
            "uri to platform redis": {"uri": "redis://redis:6379/0"},
            "uri query options": {"uri": "rediss://rw-redis-tls:6379/0?ssl_cert_reqs=none"},
            "unknown type": {"host": "rw-redis", "types": "string,bloom"},
            "sentinel naming platform redis": {"mode": "sentinel", "sentinels": "redis:26379",
                                               "sentinel_master": "mymaster"},
        }
        codes = {}
        for name, cfg in refused.items():
            r = api.post("/sources", json={"name": f"rw-redis-ref-{tag()}", "family": FAMILY,
                                           "source_type": "redis", "connection_config": cfg,
                                           "collection_id": cid2})
            codes[name] = r.status_code
            if r.status_code < 300:
                cleanup("DELETE", f"/sources/{r.json().get('source_id')}")
                jr = sj.sync(api, str(r.json().get("source_id")), timeout=300)
                codes[name] = f"201; sync {jr.get('status')}: {mask(jr.get('error_message'))[:120]}"
                if str(jr.get("status")).lower() in sj.COMPLETED:
                    soft.append(f"{name}: accepted and synced")
        out["refusals"] = codes
        if any(c != 422 for c in codes.values()):
            soft.append(f"not refused on save (422): {codes}")
    finally:
        with contextlib.suppress(Exception):
            tls_seed.delete(f"{p}:policy")
            seed.delete(f"secret:{p}", f"{p}:doc")
        tls_seed.close()
    evidence.update(out)
    assert not soft, "; ".join(soft)
