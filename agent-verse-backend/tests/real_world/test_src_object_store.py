"""SRC-OBJ-*: S3 and MinIO knowledge sources, end to end on the live stack (P1b / A2).

Two flavours run the same stories:

* ``minio`` — the compose MinIO (``http://minio:9000``), path-style addressing,
  ``source_type=minio``, a read-only user limited to one bucket.
* ``s3`` — an AWS-like S3 endpoint (``http://rw-s3:9000``, a MinIO started with
  ``MINIO_DOMAIN=rw-s3`` and ``MINIO_REGION=ap-south-1``), ``source_type=s3`` with a
  custom ``endpoint_url`` + ``region`` and **virtual-hosted** addressing
  (``<bucket>.rw-s3``), so the AWS code path is exercised without AWS.

Both hosts must be on the operator egress allowlist (``INGESTION_ALLOW_INTERNAL_SOURCES``
+ ``INGESTION_INTERNAL_SOURCE_ALLOWLIST``); everything else internal stays refused
(SRC-OBJ-REFUSAL checks that).

Environment (else SKIPPED, naming what is missing):

* minio: ``RW_S3_ACCESS_KEY`` / ``RW_S3_SECRET_KEY`` (connector user, read-only),
  ``RW_S3_SEED_ACCESS_KEY`` / ``RW_S3_SEED_SECRET_KEY`` (seeding, from this host),
  ``RW_S3_BUCKET`` (default ``rw-p1b``), ``RW_MINIO_CONTAINER`` (policy changes via
  ``docker exec … mc``; default ``agentverse-backend-minio-1``).
* s3: ``RW_AWS_ACCESS_KEY`` / ``RW_AWS_SECRET_KEY``, ``RW_AWS_ROOT_USER`` /
  ``RW_AWS_ROOT_PASSWORD`` (seeding + STS), ``RW_AWS_BUCKET`` (default ``rw-aws-docs``).

Scenarios: SRC-OBJ-MIXED (first + incremental sync with add / modify / touch /
delete + reconcile), SRC-OBJ-PAGINATION (1,050 objects, cancel mid-sync, resume),
SRC-OBJ-FILTERS, SRC-OBJ-LARGE (multipart objects, the size cap), SRC-OBJ-RETRY
(denied + corrupt objects, DLQ, operator retry), SRC-OBJ-FAILURES (bad credentials,
missing bucket, unreachable endpoint), SRC-OBJ-STS (session-token credentials),
SRC-OBJ-REFUSAL (internal addresses), SRC-OBJ-DUPLICATES (same bytes under two keys,
two sources on one prefix).
"""

from __future__ import annotations

import contextlib
import json
import os
import random
import subprocess
import time
from dataclasses import dataclass
from typing import Any

import pytest

from tests.real_world import kb
from tests.real_world import source_jobs as sj
from tests.real_world import sources as srcs
from tests.real_world.helpers import LiveAPI, mask, register_secret, tag, wait_until
from tests.real_world.metrics import norm, record, source_of
from tests.real_world.storage_objects import big_csv, daily_notes, mixed_objects

FAMILY = "object_storage"


@dataclass(frozen=True)
class Flavor:
    name: str
    source_type: str
    endpoint: str  # as the stack reaches it
    seed_endpoint: str  # as this host reaches it
    bucket: str
    region: str
    access: str
    secret: str
    seed_access: str
    seed_secret: str
    extra: dict[str, Any]

    def config(self, prefix: str, **over: Any) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "endpoint_url": self.endpoint, "bucket": self.bucket, "prefix": prefix,
            "region": self.region,
            "credentials": {"access_key_id": self.access, "secret_access_key": self.secret},
            **self.extra,
        }
        cfg.update(over)
        return cfg

    def seed_client(self) -> Any:
        import boto3
        from botocore.config import Config

        return boto3.client("s3", endpoint_url=self.seed_endpoint, region_name=self.region,
                            aws_access_key_id=self.seed_access,
                            aws_secret_access_key=self.seed_secret,
                            config=Config(s3={"addressing_style": "path"}))


def _flavor(name: str) -> Flavor:
    if name == "minio":
        need = ("RW_S3_ACCESS_KEY", "RW_S3_SECRET_KEY", "RW_S3_SEED_ACCESS_KEY",
                "RW_S3_SEED_SECRET_KEY")
        missing = [n for n in need if not os.getenv(n)]
        if missing:
            pytest.skip(f"needs {', '.join(missing)}: a MinIO bucket the stack can reach")
        fl = Flavor("minio", "minio", os.getenv("RW_S3_ENDPOINT", "http://minio:9000"),
                    os.getenv("RW_S3_SEED_ENDPOINT", "http://localhost:9000"),
                    os.getenv("RW_S3_BUCKET", "rw-p1b"), "us-east-1",
                    os.environ["RW_S3_ACCESS_KEY"], os.environ["RW_S3_SECRET_KEY"],
                    os.environ["RW_S3_SEED_ACCESS_KEY"], os.environ["RW_S3_SEED_SECRET_KEY"], {})
    else:
        need = ("RW_AWS_ACCESS_KEY", "RW_AWS_SECRET_KEY", "RW_AWS_ROOT_USER",
                "RW_AWS_ROOT_PASSWORD")
        missing = [n for n in need if not os.getenv(n)]
        if missing:
            pytest.skip(f"needs {', '.join(missing)}: an AWS-like S3 endpoint (rw-s3)")
        fl = Flavor("s3", "s3", os.getenv("RW_AWS_ENDPOINT", "http://rw-s3:9000"),
                    os.getenv("RW_AWS_SEED_ENDPOINT", "http://localhost:59000"),
                    os.getenv("RW_AWS_BUCKET", "rw-aws-docs"),
                    os.getenv("RW_AWS_REGION", "ap-south-1"),
                    os.environ["RW_AWS_ACCESS_KEY"], os.environ["RW_AWS_SECRET_KEY"],
                    os.environ["RW_AWS_ROOT_USER"], os.environ["RW_AWS_ROOT_PASSWORD"],
                    {"addressing_style": "virtual"})
    for secret in (fl.secret, fl.seed_secret, fl.access):
        register_secret(secret)
    return fl


@pytest.fixture(params=["minio", "s3"])
def flavor(request: pytest.FixtureRequest) -> Flavor:
    return _flavor(request.param)


@pytest.fixture
def minio() -> Flavor:
    return _flavor("minio")


@pytest.fixture
def aws() -> Flavor:
    return _flavor("s3")


class Seeded:
    """Objects put under one scenario prefix; removed again at the end."""

    def __init__(self, fl: Flavor, label: str) -> None:
        self.fl = fl
        self.s3 = fl.seed_client()
        self.prefix = f"rw-{label}-{tag()}/"
        self.keys: set[str] = set()

    def put(self, key: str, data: bytes, content_type: str = "text/plain") -> None:
        self.s3.put_object(Bucket=self.fl.bucket, Key=self.prefix + key, Body=data,
                           ContentType=content_type)
        self.keys.add(key)

    def put_multipart(self, key: str, data: bytes, content_type: str, part: int) -> str:
        from boto3.s3.transfer import TransferConfig

        import io as _io
        self.s3.upload_fileobj(_io.BytesIO(data), self.fl.bucket, self.prefix + key,
                               ExtraArgs={"ContentType": content_type},
                               Config=TransferConfig(multipart_threshold=part,
                                                     multipart_chunksize=part))
        self.keys.add(key)
        return str(self.s3.head_object(Bucket=self.fl.bucket, Key=self.prefix + key)["ETag"])

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.fl.bucket, Key=self.prefix + key)
        self.keys.discard(key)

    def uri(self, key: str) -> str:
        return f"s3://{self.fl.bucket}/{self.prefix}{key}"

    def cleanup(self) -> None:
        for key in list(self.keys):
            with contextlib.suppress(Exception):
                self.delete(key)


@pytest.fixture
def seeded() -> Any:
    made: list[Seeded] = []

    def make(fl: Flavor, label: str) -> Seeded:
        s = Seeded(fl, label)
        made.append(s)
        return s

    yield make
    for s in made:
        s.cleanup()


def _source(api: LiveAPI, cleanup: Any, fl: Flavor, cid: str, prefix: str,
            **over: Any) -> str:
    extra = {k: over.pop(k) for k in ("include_patterns", "exclude_patterns") if k in over}
    return sj.create_source(api, cleanup, family=FAMILY, source_type=fl.source_type,
                            config=fl.config(prefix, **over), collection_id=cid,
                            **extra)["id"]


def _doc_ids(api: LiveAPI, cid: str) -> set[str]:
    return {str(d.get("id")) for d in kb.all_documents(api, cid)[0]}


def _rank(api: LiveAPI, cid: str, query: str, uri: str, needle: str) -> int | None:
    """1-based rank of the first hit from ``uri`` carrying ``needle``, or None."""
    for i, h in enumerate(kb.search(api, cid, query, top_k=5), start=1):
        if source_of(h) == uri and norm(needle)[:40] in norm(h.get("content")):
            return i
    return None


def _served(api: LiveAPI, cid: str, text: str) -> bool:
    return any(norm(text[:60]) in norm(h.get("content"))
               for h in kb.search(api, cid, text, top_k=8))


def _assert_completed(job: dict[str, Any], label: str) -> None:
    assert str(job.get("status")).lower() in sj.COMPLETED, f"{label} not completed: {job}"


# ── SRC-OBJ-MIXED ───────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-OBJ-MIXED")
def test_obj_mixed_first_and_incremental(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                         flavor: Flavor, seeded: Any) -> None:
    seed = seeded(flavor, f"mixed-{flavor.name}")
    mixed, notes = mixed_objects(), daily_notes(20)
    for o in [*mixed, *notes]:
        seed.put(o.key, o.data, o.content_type)
    cid = srcs.create_collection(api, cleanup, f"rw-obj-{flavor.name}")
    # A short look-back (default 60 s) so sync 2 lists exactly what changed.
    sid = _source(api, cleanup, flavor, cid, seed.prefix, cursor_lookback_seconds=5)
    evidence.update(flavor=flavor.name, prefix=seed.prefix, source_id=sid, collection_id=cid)
    time.sleep(6)  # the seeded objects are older than sync 1's watermark

    job1 = sj.sync(api, sid)
    evidence["sync1"] = job1
    soft: list[str] = []
    total = len(mixed) + len(notes)
    if job1.get("docs_indexed") != total or job1.get("docs_failed"):
        soft.append(f"sync 1 indexed {job1.get('docs_indexed')}/{total}, "
                    f"failed {job1.get('docs_failed')}: {job1.get('error_message')}")
    ids1 = _doc_ids(api, cid)
    if ids1 != {seed.uri(o.key) for o in [*mixed, *notes]}:
        soft.append(f"collection holds {len(ids1)} documents, expected the {total} object URIs")
    ranks: dict[str, int | None] = {}
    for o in mixed:
        ranks[o.key] = _rank(api, cid, o.probe, seed.uri(o.key), o.fact)
    evidence["fact_ranks"] = ranks
    misses = [k for k, r in ranks.items() if r is None]
    if misses:
        soft.append(f"facts not in the top 5 with their object cited: {misses}")

    # Upstream changes: modify (new bytes → new ETag + mtime), touch (same bytes,
    # new mtime, same ETag), add two, delete two.
    edited = mixed[1]
    new_docx_fact = "The Halvorsen cold-chain clause now requires reefer probes every 4 minutes."
    from tests.real_world.storage_objects import DOCX, _docx

    seed.put(edited.key, _docx("Halvorsen cold-chain addendum v2", [new_docx_fact]), DOCX)
    touched = mixed[6]
    seed.put(touched.key, touched.data, touched.content_type)
    added = daily_notes(2, start=500)
    for o in added:
        seed.put(o.key, o.data, o.content_type)
    gone = notes[:2]
    for o in gone:
        seed.delete(o.key)

    job2 = sj.sync(api, sid)
    evidence["sync2"] = job2
    if str(job2.get("status")).lower() not in sj.COMPLETED:
        soft.append(f"sync 2 not completed: {job2}")
    # 1 modified + 2 added indexed; the touched object is at most a dedup skip;
    # untouched objects are not fetched again.
    if job2.get("docs_indexed") != 3:
        soft.append(f"sync 2 indexed {job2.get('docs_indexed')}, expected 3 "
                    "(1 modified + 2 added)")
    processed = int(job2.get("docs_indexed") or 0) + int(job2.get("docs_skipped") or 0)
    if processed != 4:  # 1 modified + 1 touched (a dedup skip) + 2 added
        soft.append(f"sync 2 processed {processed} objects, expected the 4 changed ones")
    if not _served(api, cid, new_docx_fact):
        soft.append("the modified object's new text is not served")
    if _served(api, cid, edited.fact):
        soft.append("the modified object's OLD text is still served")
    for o in added:
        if _rank(api, cid, o.probe, seed.uri(o.key), o.fact) is None:
            soft.append(f"added object {o.key} not searchable")

    status = sj.reconcile(api, sid)
    gone_ids = {seed.uri(o.key) for o in gone}
    try:
        after = wait_until(lambda: _doc_ids(api, cid), timeout=240, interval=5,
                           desc="reconciliation", done=lambda ids: not (ids & gone_ids))
    except AssertionError as exc:
        after = _doc_ids(api, cid)
        soft.append(f"reconcile ({status}) did not remove deleted objects: {exc}")
    expected = {seed.uri(o.key) for o in [*mixed, *notes[2:], *added]}
    evidence["documents_after"] = len(after)
    if after != expected:
        soft.append(f"after reconcile: missing {sorted(expected - after)[:4]}, "
                    f"extra {sorted(after - expected)[:4]}")
    record(evidence, sync1_s=job1.get("wall_s"), sync2_s=job2.get("wall_s"),
           objects=total, facts_top5=sum(1 for r in ranks.values() if r))
    assert not soft, "; ".join(soft)


# ── SRC-OBJ-PAGINATION ──────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-OBJ-PAGINATION")
def test_obj_pagination_cancel_resume(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                      minio: Flavor, seeded: Any) -> None:
    """1,050 objects (two ListObjectsV2 pages) uploaded in shuffled key order; the
    first sync is cancelled part-way, the next must index every remaining object."""
    n = int(os.getenv("RW_OBJ_PAGINATION_OBJECTS", "1050"))
    seed = seeded(minio, "pages")
    notes = daily_notes(n)
    order = list(range(n))
    random.Random(7).shuffle(order)
    t0 = time.monotonic()
    for i in order:  # upload order != key order, so mtime order != listing order
        seed.put(notes[i].key, notes[i].data, notes[i].content_type)
    evidence["seed_s"] = round(time.monotonic() - t0, 1)
    cid = srcs.create_collection(api, cleanup, "rw-obj-pages")
    sid = _source(api, cleanup, minio, cid, seed.prefix)
    evidence.update(prefix=seed.prefix, source_id=sid, collection_id=cid)

    job_id = sj.trigger(api, sid)
    cancel_at = int(os.getenv("RW_OBJ_CANCEL_AFTER", "250"))
    wait_until(lambda: len(_doc_ids(api, cid)), timeout=900, interval=5,
               desc=f"{cancel_at} documents before cancelling", done=lambda c: c >= cancel_at)
    resp = api.post(f"/sources/{sid}/sync/cancel")
    evidence["cancel_http"] = resp.status_code
    job1 = sj.mask_job(sj.wait_job(api, sid, job_id, timeout=900))
    evidence["sync1"] = job1
    before_resume = len(_doc_ids(api, cid))
    evidence["indexed_before_resume"] = before_resume

    t1 = time.monotonic()
    job2 = sj.sync(api, sid, timeout=1800)
    evidence["sync2"] = job2
    ids = _doc_ids(api, cid)
    expected = {seed.uri(o.key) for o in notes}
    missing = sorted(expected - ids)
    evidence["missing_after_resume"] = len(missing)
    soft: list[str] = []
    if resp.status_code != 202 or str(job1.get("status")).lower() != "cancelled":
        soft.append(f"cancel -> {resp.status_code}, first job {job1.get('status')} "
                    f"(cancel never reached the worker?)")
    if missing:
        soft.append(f"{len(missing)} of {n} objects never indexed after the resumed sync "
                    f"(e.g. {missing[:3]})")
    _assert_completed(job2, "resumed sync")
    sample = random.Random(3).sample(notes, 8)
    unranked = [o.key for o in sample
                if _rank(api, cid, o.probe, seed.uri(o.key), o.fact) is None]
    if unranked:
        soft.append(f"sampled objects not searchable: {unranked}")
    record(evidence, objects=n, resume_s=round(time.monotonic() - t1, 1),
           docs_per_s=round(len(ids) / max(1.0, float(job2.get('wall_s') or 1)), 2))
    assert not soft, "; ".join(soft)


# ── SRC-OBJ-FILTERS ─────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-OBJ-FILTERS")
def test_obj_prefix_and_patterns(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                 minio: Flavor, seeded: Any) -> None:
    seed = seeded(minio, "filters")
    layout = {
        "ops/a.md": True, "ops/deep/er/b.md": True, "ops/c.pdf": True,
        "ops/drafts/d.md": False,  # excluded
        "ops/e.txt": False,  # not included
        "other/f.md": False,  # outside the configured prefix
    }
    mixed = {o.key.rsplit(".", 1)[-1]: o for o in mixed_objects()}
    for key in layout:
        src = mixed["pdf"] if key.endswith(".pdf") else mixed["md"]
        body = src.data if key.endswith(".pdf") else (
            f"# Yard note {key}\n\nFilter probe for {key}: the Zirakpur cross-dock keeps "
            f"its {key} checklist with the shift supervisor until the weekly audit.\n").encode()
        seed.put(key, body, src.content_type)
    cid = srcs.create_collection(api, cleanup, "rw-obj-filters")
    sid = _source(api, cleanup, minio, cid, seed.prefix + "ops/",
                  include_patterns=["*.md", "*.pdf"], exclude_patterns=["*/drafts/*"])
    job = sj.sync(api, sid)
    evidence.update(sync=job, prefix=seed.prefix)
    ids = _doc_ids(api, cid)
    want = {seed.uri(k) for k, keep in layout.items() if keep}
    evidence["documents"] = sorted(i.rsplit("/", 1)[-1] for i in ids)
    _assert_completed(job, "filtered sync")
    assert ids == want, f"indexed {sorted(ids)}, expected {sorted(want)}"
    sj.reconcile(api, sid)
    time.sleep(40)
    assert _doc_ids(api, cid) == want, "reconcile removed documents that still match"


# ── SRC-OBJ-LARGE ───────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-OBJ-LARGE")
def test_obj_large_multipart(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                             flavor: Flavor, seeded: Any) -> None:
    seed = seeded(flavor, f"large-{flavor.name}")
    marker = "Consignment CN-ULTRA-5150 was held at Ludhiana ICD for fumigation."
    ok_size = int(os.getenv("RW_OBJ_LARGE_OK_BYTES", str(9 * 1024 * 1024)))
    big_size = int(os.getenv("RW_OBJ_LARGE_OVER_BYTES", str(14 * 1024 * 1024)))
    ok_body = big_csv(ok_size, marker_row=90_000, marker=marker)
    etag_ok = seed.put_multipart("ledger-ok.csv", ok_body, "text/csv", 5 * 1024 * 1024)
    etag_big = seed.put_multipart("ledger-big.csv", big_csv(big_size, 10, "x"), "text/csv",
                                  5 * 1024 * 1024)
    evidence.update(ok_bytes=len(ok_body), etags=[etag_ok, etag_big])
    cid = srcs.create_collection(api, cleanup, f"rw-obj-large-{flavor.name}")
    sid = _source(api, cleanup, flavor, cid, seed.prefix)
    job = sj.sync(api, sid, timeout=1800)
    evidence["sync"] = job
    soft: list[str] = []
    if "-" not in etag_ok.strip('"'):
        soft.append(f"seed object was not a multipart upload (ETag {etag_ok})")
    if seed.uri("ledger-ok.csv") not in _doc_ids(api, cid):
        soft.append(f"the {len(ok_body)}-byte multipart object was not indexed: {job}")
    elif not _served(api, cid, marker):
        soft.append("the marker row near the end of the large object is not searchable")
    entries = sj.dlq(api, sid)
    evidence["dlq"] = [{"doc": e.get("doc_id"), "error": mask(e.get("error_message"))[:200]}
                       for e in entries]
    over = [e for e in entries if str(e.get("doc_id", "")).endswith("ledger-big.csv")]
    if not over or "size cap" not in str(over[0].get("error_message")):
        soft.append("the over-cap object is not reported with its size-cap reason")
    if int(job.get("docs_failed") or 0) != 1:
        soft.append(f"expected exactly 1 counted failure (the over-cap object): {job}")
    record(evidence, large_sync_s=job.get("wall_s"))
    assert not soft, "; ".join(soft)


# ── SRC-OBJ-RETRY ───────────────────────────────────────────────────────────


def _mc(*args: str) -> str:
    container = os.getenv("RW_MINIO_CONTAINER", "agentverse-backend-minio-1")
    out = subprocess.run(["docker", "exec", container, "mc", *args], capture_output=True,
                         text=True, timeout=60, check=False)
    return out.stdout + out.stderr


@pytest.mark.scenario("SRC-OBJ-RETRY")
def test_obj_failed_object_retry(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                 minio: Flavor, seeded: Any) -> None:
    """A denied object (USR-1/USR-4) and a corrupt one: counted with their reasons,
    in the DLQ; after the cause is fixed the operator retry / next sync index them."""
    seed = seeded(minio, "retry")
    good = mixed_objects()[6]
    seed.put("good.md", good.data, good.content_type)
    secret_fact = "The Rourkela vault inventory is reconciled by auditor Meher Bakshi."
    seed.put("restricted/vault.md", f"# Vault\n\n{secret_fact}\n".encode(), "text/markdown")
    seed.put("broken.pdf", b"%PDF-1.4\n1 0 obj << /Type /Catalog >>\n% truncated", "application/pdf")
    policy = f"rwp1b-deny-{tag()}"
    deny = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Deny", "Action": ["s3:GetObject"],
        "Resource": [f"arn:aws:s3:::{minio.bucket}/{seed.prefix}restricted/*"]}]}
    path = f"/tmp/{policy}.json"
    container = os.getenv("RW_MINIO_CONTAINER", "agentverse-backend-minio-1")
    subprocess.run(["docker", "exec", "-i", container, "sh", "-c", f"cat > {path}"],
                   input=json.dumps(deny), text=True, check=True, timeout=30)
    _mc("alias", "set", "local", "http://localhost:9000", minio.seed_access, minio.seed_secret)
    evidence["policy_create"] = _mc("admin", "policy", "create", "local", policy, path)[-120:]
    evidence["policy_attach"] = _mc("admin", "policy", "attach", "local", policy, "--user",
                                    minio.access)[-120:]
    try:
        cid = srcs.create_collection(api, cleanup, "rw-obj-retry")
        sid = _source(api, cleanup, minio, cid, seed.prefix)
        job1 = sj.sync(api, sid)
        evidence["sync1"] = job1
        entries = sj.dlq(api, sid)
        evidence["dlq1"] = [{"doc": e.get("doc_id"), "error": mask(e.get("error_message"))[:220],
                             "permanent": e.get("permanent_failure")} for e in entries]
        soft: list[str] = []
        if str(job1.get("status")).lower() != "partial" or job1.get("docs_failed") != 2:
            soft.append(f"sync 1 should be partial with 2 failures: {job1}")
        denied = [e for e in entries if str(e.get("doc_id")).endswith("restricted/vault.md")]
        broken = [e for e in entries if str(e.get("doc_id")).endswith("broken.pdf")]
        if not denied or "AccessDenied" not in str(denied[0].get("error_message")):
            soft.append("the denied object is not in the DLQ with an AccessDenied reason")
        if not broken or "pdf" not in str(broken[0].get("error_message")).lower():
            soft.append("the corrupt PDF is not in the DLQ with a parse reason")
    finally:
        _mc("admin", "policy", "detach", "local", policy, "--user", minio.access)
        _mc("admin", "policy", "remove", "local", policy)
    # Fix both causes: access is restored (above); the PDF is replaced upstream.
    fixed_fact = "Broken manual replaced: the Jharsuguda siding closes at 22:15."
    from tests.real_world.storage_objects import _pdf

    seed.put("broken.pdf", _pdf(["Siding hours", fixed_fact]), "application/pdf")
    if denied:
        resp = api.post(f"/ingestion/dlq/{denied[0]['id']}/retry")
        evidence["retry_http"] = resp.status_code
        try:
            wait_until(lambda: _served(api, cid, secret_fact), timeout=240, interval=6,
                       desc="the denied object indexed by the operator retry")
        except AssertionError:
            soft.append("operator retry after restoring access did not index the object")
    job2 = sj.sync(api, sid)
    evidence["sync2"] = job2
    if not _served(api, cid, fixed_fact):
        soft.append("the repaired PDF was not indexed by the next sync")
    time.sleep(5)
    open_entries = sj.dlq(api, sid)
    evidence["dlq_after"] = [{"doc": e.get("doc_id"), "retries": e.get("retry_count"),
                              "permanent": e.get("permanent_failure")} for e in open_entries]
    if open_entries:
        soft.append(f"{len(open_entries)} DLQ entries still open after both objects were "
                    f"indexed: {[e.get('doc_id') for e in open_entries]}")
    assert not soft, "; ".join(soft)


# ── SRC-OBJ-FAILURES ────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-OBJ-FAILURES")
def test_obj_honest_failures(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                             flavor: Flavor, seeded: Any) -> None:
    seed = seeded(flavor, f"fail-{flavor.name}")
    seed.put("a.md", b"# a\n\nThe Silchar depot opens at 06:00.\n", "text/markdown")
    host = flavor.endpoint.split("//", 1)[1].split(":")[0]
    cases = {
        "bad_secret": (flavor.config(seed.prefix, credentials={
            "access_key_id": flavor.access, "secret_access_key": "wrong-" + tag()}),
            ("SignatureDoesNotMatch", "InvalidAccessKeyId", "403", "Forbidden")),
        "unknown_key": (flavor.config(seed.prefix, credentials={
            "access_key_id": "nosuchuser" + tag(), "secret_access_key": "x" * 20}),
            ("InvalidAccessKeyId", "403", "Forbidden")),
        # A policy-limited user gets AccessDenied for a bucket it may not see
        # (MinIO does not reveal whether it exists); an AWS-style store says NoSuchBucket,
        # or — virtual-hosted — the bucket host does not resolve (refused, fail closed).
        "missing_bucket": (flavor.config(seed.prefix, bucket=f"rw-missing-{tag()}"),
                           ("NoSuchBucket", "404", "Not Found", "AccessDenied", "resolve")),
        "unreachable": (flavor.config(seed.prefix, endpoint_url=f"http://{host}:9999"),
                        ("connect", "Connection", "refused", "Could not connect")),
    }
    results: dict[str, Any] = {}
    soft: list[str] = []
    cid = srcs.create_collection(api, cleanup, f"rw-obj-fail-{flavor.name}")
    for name, (cfg, words) in cases.items():
        v = sj.validate(api, family=FAMILY, source_type=flavor.source_type, config=cfg)
        sid = sj.create_source(api, cleanup, family=FAMILY, source_type=flavor.source_type,
                               config=cfg, collection_id=cid)["id"]
        t0 = time.monotonic()
        job = sj.sync(api, sid, timeout=600)
        results[name] = {"validate_valid": v.get("valid"),
                         "validate_error": mask(v.get("errors"))[:240],
                         "job": {k: job.get(k) for k in ("status", "docs_failed", "docs_indexed",
                                                         "error_message", "wall_s")},
                         "fail_s": round(time.monotonic() - t0, 1)}
        if v.get("valid"):
            soft.append(f"{name}: /sources/validate reported valid")
        if str(job.get("status")).lower() != "failed" or not int(job.get("docs_failed") or 0):
            soft.append(f"{name}: sync not reported failed: {job.get('status')}/"
                        f"{job.get('docs_failed')}")
        msg = str(job.get("error_message") or "")
        if not any(w.lower() in msg.lower() for w in words):
            soft.append(f"{name}: error does not say why ({msg[:160]!r})")
        if results[name]["fail_s"] > 150:
            soft.append(f"{name}: failure took {results[name]['fail_s']} s")
    evidence["cases"] = results
    assert not _doc_ids(api, cid), "a failing source indexed documents"
    assert not soft, "; ".join(soft)


# ── SRC-OBJ-STS ─────────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-OBJ-STS")
def test_obj_session_token(api: LiveAPI, cleanup: Any, evidence: dict[str, Any], aws: Flavor,
                           seeded: Any) -> None:
    """Temporary credentials (access key + secret + session token) from STS AssumeRole."""
    import boto3

    seed = seeded(aws, "sts")
    fact = "The Tezpur tea bonded store releases consignments on Tuesdays only."
    seed.put("sts.md", f"# Tezpur\n\n{fact}\n".encode(), "text/markdown")
    sts = boto3.client("sts", endpoint_url=aws.seed_endpoint, region_name=aws.region,
                       aws_access_key_id=aws.access, aws_secret_access_key=aws.secret)
    creds = sts.assume_role(RoleArn="arn:aws:iam::000000000000:role/rw", RoleSessionName="rw",
                            DurationSeconds=3600)["Credentials"]
    for v in creds.values():
        register_secret(str(v))
    temp = {"access_key_id": creds["AccessKeyId"], "secret_access_key": creds["SecretAccessKey"],
            "session_token": creds["SessionToken"]}
    cid = srcs.create_collection(api, cleanup, "rw-obj-sts")
    v = sj.validate(api, family=FAMILY, source_type=aws.source_type,
                    config=aws.config(seed.prefix, credentials=temp))
    sid = _source(api, cleanup, aws, cid, seed.prefix, credentials=temp)
    job = sj.sync(api, sid)
    bad = dict(temp, session_token="garbage" + tag())
    sid_bad = _source(api, cleanup, aws, cid, seed.prefix + "nothing/", credentials=bad)
    job_bad = sj.sync(api, sid_bad)
    evidence.update(validate=v, sync=job, sync_bad_token=job_bad)
    soft: list[str] = []
    if not v.get("valid"):
        soft.append(f"validate with session-token credentials failed: {v.get('errors')}")
    if str(job.get("status")).lower() not in sj.COMPLETED or not _served(api, cid, fact):
        soft.append(f"sync with session-token credentials did not index the object: {job}")
    if str(job_bad.get("status")).lower() != "failed":
        soft.append(f"a garbage session token is not an honest failure: {job_bad}")
    assert not soft, "; ".join(soft)


# ── SRC-OBJ-REFUSAL ─────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-OBJ-REFUSAL")
def test_obj_internal_address_refusal(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                      minio: Flavor) -> None:
    cid = srcs.create_collection(api, cleanup, "rw-obj-refusal")
    internal = ["http://postgres:5432", "http://redis:6379", "http://169.254.169.254",
                "http://metadata.google.internal", "http://localhost:9000",
                "http://127.0.0.1:9000", "http://[::1]:9000", "http://0.0.0.0:9000",
                "http://host.docker.internal:9000", "http://10.0.0.5:9000",
                "http://backend:8000", "file:///etc/passwd"]
    results: dict[str, Any] = {}
    soft: list[str] = []
    for stype in ("minio", "s3"):
        for ep in internal:
            r = sj.create_source(api, cleanup, family=FAMILY, source_type=stype,
                                 config=minio.config("x/", endpoint_url=ep), collection_id=cid,
                                 expect=422)
            results[f"{stype} {ep}"] = str(r.get("detail"))[:120]
    ok = sj.create_source(api, cleanup, family=FAMILY, source_type="minio",
                          config=minio.config("x/"), collection_id=cid)
    results["allowlisted minio"] = ok["_http"]
    no_ep = minio.config("x/")
    no_ep.pop("endpoint_url")
    r = api.post("/sources", json={"name": f"rw-noep-{tag()}", "family": FAMILY,
                                   "source_type": "minio", "connection_config": no_ep,
                                   "collection_id": cid})
    if r.status_code < 300:
        cleanup("DELETE", f"/sources/{r.json().get('source_id')}")
        st = r.json().get("config_status")
        if st == "ok":
            soft.append("a minio source without endpoint_url is accepted as configured")
    results["minio without endpoint"] = r.status_code
    evidence["results"] = results
    assert not soft, "; ".join(soft)


# ── SRC-OBJ-DUPLICATES ──────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-OBJ-DUPLICATES")
def test_obj_duplicate_bytes_and_shared_prefix(api: LiveAPI, cleanup: Any,
                                               evidence: dict[str, Any], minio: Flavor,
                                               seeded: Any) -> None:
    """The same bytes under two keys (a backup copy) and two sources reading one
    prefix into two collections: every object stays represented and cited, and
    deleting one copy upstream never loses the content the other still holds."""
    seed = seeded(minio, "dups")
    fact = "The Kandla salt terminal weighbridge is recalibrated every 90 days by Saurashtra Metrology."
    body = f"# Weighbridge\n\n{fact}\n".encode()
    copies = ("backup/weighbridge.md", "policies/weighbridge.md")
    for key in copies:
        seed.put(key, body, "text/markdown")
    seed.put("other.md", b"# Other\n\nThe Paradip coal berth uses conveyor C4 for all "
             b"night-shift rakes from the Talcher mines.\n", "text/markdown")
    cid_a = srcs.create_collection(api, cleanup, "rw-obj-dups-a")
    cid_b = srcs.create_collection(api, cleanup, "rw-obj-dups-b")
    sid_a = _source(api, cleanup, minio, cid_a, seed.prefix)
    sid_b = _source(api, cleanup, minio, cid_b, seed.prefix)
    job_a, job_b = sj.sync(api, sid_a), sj.sync(api, sid_b)
    ids_a, ids_b = _doc_ids(api, cid_a), _doc_ids(api, cid_b)
    evidence.update(sync_a=job_a, sync_b=job_b, docs_a=sorted(ids_a), docs_b=sorted(ids_b))
    soft: list[str] = []
    want = {seed.uri(k) for k in (*copies, "other.md")}
    if ids_a != want or ids_b != want:
        soft.append(f"each collection should hold all 3 objects: A={len(ids_a)} B={len(ids_b)}")
    # Delete the copy that IS indexed (if only one is); the other still exists upstream.
    gone = next((k for k in copies if seed.uri(k) in ids_a), copies[0])
    evidence["deleted_upstream"] = gone
    seed.delete(gone)
    sj.reconcile(api, sid_a)
    try:
        wait_until(lambda: _doc_ids(api, cid_a), timeout=180, interval=5, desc="reconcile",
                   done=lambda ids: seed.uri(gone) not in ids)
    except AssertionError:
        soft.append("reconcile did not remove the deleted copy")
    if not _served(api, cid_a, fact):
        soft.append("deleting one copy made the content unsearchable although the other copy "
                    "still exists upstream")
    if not _served(api, cid_b, fact):
        soft.append("collection B lost content when source A reconciled")
    assert not soft, "; ".join(soft)
