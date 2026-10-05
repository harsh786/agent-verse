# P1b: object storage (A2) and OLTP databases (A3) on the live stack (2026-10-05)

Branch `live/p1b-storage-oltp` (from `main` @ `18cb0676d`): 10 fixes and 3 test commits. Nothing was pushed.

The raw output of every live run is in `p1b-storage-oltp/` next to this file: `results.jsonl` and `summary.txt` per run, plus
`a2-probe-before-p1b2.txt`, the first MinIO sync before any fix. The copied files were scanned for the tenant keys, the
MinIO / S3 / database passwords and the `av_` / `nvapi-` / `sk-` patterns, with 0 hits. Raw pytest logs stayed outside the
repo, because the first runs printed fixture secrets in tracebacks. That is fixed in `49fc28095`.

## 1. Verdicts

| Source | Before (live baseline) | After (live) | Verdict |
|---|---|---|---|
| **S3** (custom endpoint + region, virtual-hosted, STS) | `.pptx` / `.zip` objects failed with a Postgres NUL-byte error. A cancelled sync lost the rest of the bucket. Dedup dropped duplicate objects. Failed objects could not be recovered. Session tokens were ignored. UI-created sources ran anonymous. | Every SRC-OBJ scenario passes for the `s3` flavor | **COMPLETE (fixed: P1b-1, -2, -3, -4, -5, -6, -8, -9, -10)** |
| **MinIO** (path-style) | Same as S3. In addition, after one manual sync every later sync answered `already_running` until the API restarted. | Every SRC-OBJ scenario passes, and the UI wizard works end to end | **COMPLETE (fixed: P1b-1, -2, -3, -4, -5, -6, -8, -9, -10)** |
| **PostgreSQL** | **0 rows synced.** The text cursor `'1970-01-01'` was bound to a `timestamptz` column (asyncpg `DataError`) for every table. There was one cursor for all tables, one batch per sync and no deletion listing. | SRC-DB-SYNC, SRC-DB-TABLE-RETRY and SRC-DB-FAILURES pass | **COMPLETE (fixed: P1b-7, plus P1b-1 / -5 shared)** |
| **MySQL** | **0 rows synced.** `tables` was ignored: `(1065, 'Query was empty')`. Reconcile answered 422. A missing table gave no reason. | SRC-DB-SYNC, SRC-DB-TABLE-RETRY and SRC-DB-FAILURES pass | **COMPLETE (fixed: P1b-7)** |

The final verification ran on images rebuilt from the branch, without the dev mount described in §2. That run
(`p1b-storage-oltp/final/`) passed 10 of 10:
- SRC-OBJ-MIXED[minio]
- SRC-OBJ-FAILURES[minio, s3]
- SRC-OBJ-STS, SRC-OBJ-REFUSAL and SRC-OBJ-DUPLICATES
- SRC-DB-TABLE-RETRY[pg, mysql] and SRC-DB-FAILURES[pg, mysql]

The longer scenarios passed earlier on the same code:
- SRC-OBJ-MIXED[s3] and SRC-OBJ-LARGE[minio, s3] in `run5/` and `run4/`
- SRC-OBJ-PAGINATION, SRC-DB-SYNC[mysql] and SRC-DB-TABLE-RETRY[mysql] in `run6/`
- SRC-DB-SYNC[pg] in `a3-run1/`
- SRC-OBJ-FILTERS and SRC-OBJ-RETRY in `run4/`

The scenario files did not change in between, except for the assertion changes noted in §3.

### Owner's S3 / MinIO checklist (each line verified live)

| Requirement | Result | Evidence |
|---|---|---|
| Path-style addressing (MinIO) | PASS | All `[minio]` scenarios use `http://minio:9000`, `source_type=minio` |
| Virtual-hosted addressing (AWS-style) | PASS | `[s3]` flavor: `addressing_style: virtual` against `rw-s3` (MinIO with `MINIO_DOMAIN`). Requests go to `<bucket>.rw-s3`, and every resolved host is egress-checked |
| Custom endpoint URL and region | PASS | `[s3]` uses `endpoint_url=http://rw-s3:9000`, `region=ap-south-1` |
| Access key / secret auth | PASS | Least-privilege users: MinIO `rwp1b` has ListBucket + GetObject on one bucket; the AWS-like store has `rwaws` |
| Session-token auth | PASS (fixed P1b-3) | SRC-OBJ-STS: STS AssumeRole credentials validate and sync. A garbage token gives `failed: InvalidTokenId …` |
| Prefix + recursive listing, pagination over 1,000 objects | PASS (fixed P1b-3) | SRC-OBJ-FILTERS: exactly 3 of 6 keys (prefix, nested `deep/er/b.md`, include `*.md,*.pdf`, exclude `*/drafts/*`). SRC-OBJ-PAGINATION: see below |
| Large (multipart) objects | PASS | SRC-OBJ-LARGE, both flavors (see below) |
| Mixed formats through the A1 extractors | PASS (fixed P1b-2) | SRC-OBJ-MIXED: 12 formats. Each fact ranked **1st** with its `s3://…` object cited (12/12 per flavor) |
| Incremental sync: add / modify (ETag + mtime) / delete + reconcile | PASS (fixed P1b-3, -6) | See the SRC-OBJ-MIXED row below |
| Failed-object retry (USR-4) | PASS (fixed P1b-4, -5) | SRC-OBJ-RETRY (see below) |
| Honest failure: bad credentials, missing bucket, unreachable endpoint | PASS (message fixed P1b-10) | SRC-OBJ-FAILURES, both flavors (see below) |
| Internal-address refusal except the allowlisted MinIO | PASS | SRC-OBJ-REFUSAL (see below) |
| UI wizard: create, test connection, preview, sync | PASS (fixed P1b-8, -9) | §4 |

SRC-OBJ-MIXED details:
- **Modify:** new bytes give a new ETag and mtime. The object is re-indexed, the new text is served and the old text is gone.
- **Touch:** the same bytes with a new mtime are fetched and then skipped as `dedup`.
- **Add:** both new objects are indexed.
- **Delete:** 2 deletes are removed by `POST /sources/{id}/reconcile` (KB-44).
- **Counts:** sync 2 processed exactly the 4 changed objects (3 indexed, 1 skipped).

SRC-OBJ-PAGINATION details:
- 1,050 objects (two ListObjectsV2 pages) were seeded in shuffled key/time order in 4.4 s.
- The first sync was cancelled after 268 documents: the cancel returned 202 and the job ended `cancelled`.
- The resumed sync indexed the other **782 in 184.8 s, with 0 missing** (5.7 docs/s).

SRC-OBJ-LARGE details:
- A 9.4 MB CSV uploaded as 2 multipart parts (ETag `…-2`) gave 2,001 chunks in 236–246 s. A marker row near the end is searchable.
- A 14 MB object (3 parts) is a counted **permanent** failure: `object exceeds the 10485760-byte size cap`, recorded in the DLQ.

SRC-OBJ-RETRY details:
- **Setup:** a Deny-GetObject policy on `restricted/*`, plus a corrupt PDF.
- **Sync 1:** `partial`, 2 failures, both in the DLQ with reasons (`AccessDenied … (permanent)` and `not a readable PDF`).
- **Access restored:** `POST /ingestion/dlq/{id}/retry` indexes the object.
- **PDF replaced upstream:** the next sync indexes it.
- **Result:** 0 DLQ entries left open.

SRC-OBJ-FAILURES details (each fails in about 3 s with `status=failed`, `docs_failed=1`):

| Case | Error reported |
|---|---|
| Bad secret | `SignatureDoesNotMatch …` |
| Unknown key | `InvalidAccessKeyId …` |
| Missing bucket, MinIO policy user | `AccessDenied` (MinIO does not reveal whether a bucket exists) |
| Missing bucket, virtual-hosted | Host `<bucket>.rw-s3` unresolvable, refused fail-closed |
| Unreachable port | `EndpointConnectionError` |

`/sources/validate` marks each case invalid and names the S3 code.

SRC-OBJ-REFUSAL details:
- 12 endpoints are refused with 422 "blocked destination", for both `minio` and `s3`: platform postgres and redis, metadata IP and hostname, localhost, 127.0.0.1, ::1, 0.0.0.0,
  host.docker.internal, 10.x, backend and `file://`.
- The allowlisted `minio` is accepted.
- `minio` without `endpoint_url` is `needs_configuration`.

## 2. Deployment, infra and env changes

- **Stack built from this worktree**, as in P1a: `docker-compose -f .claude/worktrees/p1b/agent-verse-backend/infra/docker-compose.yml build`, then
  `up -d --no-deps` for `backend worker subgoal-worker beat workflow-worker`. The frontend was rebuilt from the worktree for the UI check. No
  volumes were dropped. The worktree `.env` is an untracked symlink to the main checkout's `.env`. The containers now run images built
  from the branch, and the compose bind mounts (`app/{bootstrap,org,gateway,agent/graph.py}`, migrations) point at this worktree, so
  redeploy from `main` after merging.
- **Dev iteration only.** While fixing, a compose override (`/private/tmp/claude-501/rw/p1b/p1b-dev-override.yml`, never committed)
  mounted the worktree's `app/`, so a fix needed only a restart. The final run used rebuilt images without the override.
- **Disk.** The first build failed with `no space left on device` in the colima VM (98 % of 157 GB). Dangling images were pruned (7.5 GB
  reclaimed), and nothing else was removed.
- **Operator egress allowlist (local only, owner-approved).** One block was appended to the main checkout's `agent-verse-backend/.env`.
  It is gitignored, and a backup of the previous file is at `/private/tmp/claude-501/rw/p1b/dotenv.before-p1b`:
  ```
  INGESTION_ALLOW_INTERNAL_SOURCES=true
  INGESTION_INTERNAL_SOURCE_ALLOWLIST=minio,rw-s3,rw-pg,rw-mysql
  ```
  - **Why:** the connector egress guard refuses every private address, and the compose MinIO and the test databases live on the compose network.
  - **Scope:** only these names and their subdomains, which the virtual-hosted `<bucket>.rw-s3` needs, are allowed. An in-container probe confirmed that `postgres`,
    `redis` and `169.254.169.254` stay blocked, and SRC-OBJ-REFUSAL / SRC-DB-FAILURES check the same live.
  - **Defaults:** production defaults are unchanged (`app/core/config.py`: off and empty). Delete the block to restore.
- **Extra containers** on `agentverse-backend_default`, labelled `p1b=live-test`. Remove them with
  `docker rm -f $(docker ps -aq --filter label=p1b=live-test)`.

  | Container | Image | Purpose | Host port |
  |---|---|---|---|
  | `agentverse-rw-pg` | `postgres:16` | Test database, alias `rw-pg` | `127.0.0.1:56432`; 55432 is taken by a local macOS postgres |
  | `agentverse-rw-mysql` | `mysql:8.0`, utf8mb4 | Test database, alias `rw-mysql` | `127.0.0.1:53306` |
  | `agentverse-rw-s3` | MinIO | AWS-like S3: `MINIO_DOMAIN=rw-s3`, region `ap-south-1`, aliases `rw-s3`, `rw-aws-docs.rw-s3`, `rw-aws-missing.rw-s3` | `127.0.0.1:59000` |
  | `agentverse-rw-ingestion-worker` | Branch image | Celery worker for the `ingestion` queue only. The compose `worker` serves `ingestion` together with `maintenance`, which held a 6,567-task backlog, so a sync waited minutes before starting (§6.1) | – |
- **Compose MinIO.**
  - Buckets `rw-p1b` and `rw-p1b-aws`.
  - User `rwp1b` with policy `rwp1b-readonly` (ListBucket + GetObject on those buckets only).
  - SRC-OBJ-RETRY temporarily attaches and then removes a Deny policy.
- **Credentials** live only in `/private/tmp/claude-501/rw/p1b/infra.env` (mode 600). Each database scenario creates and drops its own schema or
  database and a least-privilege reader (SELECT on the granted tables only; Postgres `NOSUPERUSER`). Postgres sessions are read-only.
- **UI test tenant.** `rw-p1b-ui` (free plan) was created through `POST /tenants/signup` for the browser check. The enterprise tenant from P0
  ran every scenario.

## 3. Scenarios (new: `tests/real_world/test_src_object_store.py`, `test_src_oltp.py`)

| Scenario | First run on the old code | Final |
|---|---|---|
| SRC-OBJ-MIXED [minio, s3] | `.pptx` / `.zip` failed with `CharacterNotInRepertoireError … 0x00` (probe; P1b-2). Incremental and reconcile worked | **pass** ×2 |
| SRC-OBJ-PAGINATION (1,050 objects, cancel + resume) | Cancel never reached the worker (P1b-1). After a cancelled or crashed run the LastModified cursor skipped not-yet-listed keys (P1b-3, reproduced in a unit test; the live run is on the fixed code) | **pass**: 268 + 782, 0 missing |
| SRC-OBJ-FILTERS | Fixture notes under 50 characters were skipped as `empty_content`; the fixture was lengthened (§6.5) | **pass** |
| SRC-OBJ-LARGE [minio, s3] | pass | **pass** ×2 |
| SRC-OBJ-RETRY | Operator retry marked the AccessDenied entry permanent without re-fetching; the repaired PDF's entry stayed open (P1b-4). The second sync was then silently skipped for backoff, and the test waited 600 s on a job that never existed (P1b-5) | **pass** |
| SRC-OBJ-FAILURES [minio, s3] | Syncs were honest. Validate said only `403 Forbidden` (P1b-10) | **pass** ×2 |
| SRC-OBJ-STS | `InvalidTokenId`: the session token was never passed (P1b-3) | **pass** |
| SRC-OBJ-REFUSAL | `minio` without an endpoint was accepted as `ok`, and the sync could only fail (P1b-4) | **pass** |
| SRC-OBJ-DUPLICATES | The backup copy was skipped as `dedup`, so only 1 of 2 keys was represented, and deleting it upstream would have removed the content (P1b-6) | **pass** |
| SRC-DB-SYNC [pg, mysql] | **0 / 1,770 rows** on both engines | **pass** ×2 (see below) |
| SRC-DB-TABLE-RETRY [pg, mysql] | Failed on the same errors as SRC-DB-SYNC (no row synced) | **pass** ×2: `partial` naming the ungranted `payroll` table (permission / privilege); after `GRANT` the next sync indexed its rows, which are older than the other table's cursor |
| SRC-DB-FAILURES [pg, mysql] | pg pass. mysql: a missing table said `Query was empty` | **pass** ×2 (see below) |

SRC-DB-SYNC passes on both engines:

| Check | PostgreSQL | MySQL |
|---|---|---|
| Rows indexed | 1,770 | 1,770 |
| Sync 1 time | 420 s | 444 s |

The 1,770 rows are customers 30, shipments 120, a 1,500-row bulk load with one timestamp, and the 120-row join view.
- **Probes:** 5 of 5 searchable — a NULL-safe note, a JSON value, the last row of the bulk load, a UTF-8 name and the view.
- **Incremental:** the 3 inserted, 2 updated and 2 view rows are indexed (7).
- **Deletes:** reconcile removes the 2 deleted rows, leaving 1,771 documents.

SRC-DB-FAILURES on both engines:
- Bad password, unknown user, unreachable port, missing database and missing table each fail honestly with the driver's reason, and
  `validate` reports them invalid.
- 9 internal hosts are refused with 422: postgres, pgbouncer, redis, localhost, 127.0.0.1, metadata, host.docker.internal, 10.x and a socket path.

Assertion changes made while verifying:
- **Mixed incremental.** It now uses `cursor_lookback_seconds=5`, so sync 2 is exact. With the default 60 s look-back, sync 2 also
  re-lists objects changed in the minute before sync 1 and dedup-skips them.
- **DB incremental.** It counts rows indexed by every job after sync 1, because the beat may run a scheduled sync of the same source first (§6.3).
- **Accepted messages.** The MinIO `AccessDenied` and the fail-closed virtual-host message are accepted for "missing bucket", and
  "privilege" is accepted for MySQL's denied table.

## 4. UI wizard (browser, live backend, `localhost:5173`)

1. **Before (frontend built from main).**
   - The object-storage form saved `access_key_id` / `secret_access_key` at the top of `connection_config`. The stored config
     confirms it: `{"bucket": …, "access_key_id": "rwp1b", "secret_access_key": "********"}`.
   - The connector reads `connection_config.credentials` (see `18cb0676d:…/s3_connector.py:174`), so every UI-created source ran anonymous.
   - The form had no session-token or addressing-style field, and `s3` had no endpoint field.
   - After "Sync Now" the History tab kept `never_synced` / `undefined sync`, and the list kept `0 docs`, although the job completed with 1 document.
2. **After (rebuilt from this branch).**
   - The MinIO wizard has an endpoint, an addressing-style select, the keys and an optional session token.
   - **Test connection** shows "Connection OK · 92 ms". **Create** works.
   - **Preview** shows "1 documents previewed: `s3://rw-p1b/ui-wizard/tirupur.md`, dry_run, 1 chunk".
   - **Sync Now** shows `pending` and then `completed — incremental sync`, and the list refreshed its last-synced time.
   - The run reported `Skipped 1` because the "before" source had already indexed the same object into the same collection (§6.2).

## 5. Fixes (TDD: a failing unit / integration test first, then the live scenario)

| Commit | Fix | Root cause |
|---|---|---|
| `7bd1f8334` P1b-1 | Sync lock and cancel flag on the shared Redis: the API tracker gets the runtime Redis, and sync and reconcile tasks attach it. Lock release accepts str or bytes and logs failures | Neither tracker had Redis. The worker could not release the API's lock, so every later manual sync was `already_running` until an API restart; a beat sync ran beside a manual one; cancel never arrived. `release_lock` called `.decode()` on str and swallowed the error |
| `5b14bf120` P1b-2 | Connector documents use the A1 (upload) extractors: PPTX; ZIP (bounded, bombs refused, skipped members reported); DOCX; HTML; per-page PDF with OCR. Other NUL-byte content fails as `unsupported binary content` | The connector pipeline had no PPTX or ZIP path and decoded the bytes as text. Postgres refused the NUL bytes |
| `7e12cb938` P1b-3 | S3 cursor `{since, after, run}`: per-document resume after the last key (`StartAfter`); the completed listing publishes watermark = listing start by the store's `Date` header minus a look-back (`cursor_lookback_seconds`, default 60 s); legacy cursors honoured. Adds `session_token`, `addressing_style`, and errors carrying the S3 code | The cursor was the newest LastModified, committed every 100 docs, while keys are listed by name. A cancelled or crashed run skipped unreached older objects for good, and a change during a run could be missed. The token and addressing style were never passed |
| `13d78ff69` P1b-4 | Operator DLQ retry re-fetches even a "permanent" failure. A sync resolves the source's open DLQ entries for documents it indexed (batched `doc_id = ANY`). `docs_discovered` is reported. `minio` without an endpoint is `needs_configuration` | The permanent check ran before the operator's `force`. Stale entries stayed open, and their replay would have overwritten newer versions |
| `6e45ac514` P1b-5 | Operator syncs and reindexes skip the failure backoff | A manual sync answered `queued` with a job id was skipped for backoff without any job record |
| `7ac4ba21c` P1b-6 | Connector dedup is per document (`exists_by_hash(document_id=…)`, `ingest_chunks_async(duplicates_within_document=True)`). Uploads, RPA and OCR keep collection-wide dedup | The same bytes under another key were skipped, so that object was never represented and could vanish on reconcile |
| `641addb07` P1b-7 | PostgreSQL and MySQL: typed per-table keyset positions (`sql_rows.py`), `ORDER BY cursor, pk` batches until each table is exhausted, a failed table keeps its position, primary keys from config / catalog / `id`, `iter_live_doc_ids` for reconcile, validated identifiers, read-only sessions with statement timeouts, NULL-free row text, JSON-safe metadata. MySQL `tables`; validate checks each table | See the §1 baselines |
| `f8953cd83` P1b-8 | The UI form nests the keys under `credentials` and adds a session token, an `s3` endpoint and the addressing style. The connector also reads flat keys already stored | UI-created sources ran anonymous |
| `1ceeab05c` P1b-9 | The source drawer follows the queued job id until it finishes, then refreshes sources, documents and the DLQ | A single invalidation read the previous status and polling stopped |
| `b08e73ca1` P1b-10 | The connection check lists one key and reports the S3 code | HEAD Bucket errors have no body |
| `8e3bc7e13`, `1211b4ff6`, `49fc28095` | Live scenarios, the README (scenarios, env vars, one session at a time), and secrets kept out of tracebacks | – |

New and changed tests:
- **New files:** `test_sync_lock_shared_redis`, `test_connector_formats_a1`, `test_s3_listing_cursor`, `test_scheduler_completed_cursor`, `test_dlq_resolution_p1b`,
  `test_postgresql_rows_integration` and `test_mysql_rows_integration` (real Postgres 16 and MySQL 8 through testcontainers), plus vitest cases.
- **Updated** to the new behaviour: the S3, PostgreSQL and MySQL connector tests.

Suites run on the final tree:
- `tests/ingestion`, `tests/ocr`, `tests/knowledge` and `tests/rag`: **3,849 passed** (196 skipped).
- The ingestion, source, knowledge, upload and DLQ tests in `tests/api`: 622 passed.
- Both integration files: 5 passed.
- vitest `src/features/ingestion`: 201 passed.
- `ruff check .` and `mypy app` (strict, 1,890 files): clean. Frontend `tsc --noEmit` and eslint: clean.

## 6. Open items

1. **Ingestion queue starvation (P9).** The compose `worker` consumes `ingestion` together with `maintenance`, which held 6,567 queued tasks
   (mostly `dispatch_outbox`, `flush_audit_wal` and `forward_siem_outbox`). A manual sync waited more than 10 minutes to start. The scenarios ran with a dedicated
   ingestion worker (§2). Compose needs its own ingestion worker, or the maintenance producers need throttling.
2. **S3 / MinIO document ids are not source-scoped** (`s3://bucket/key`). Two sources reading the same object into the *same* collection
   share one document: the second is dedup-skipped and the document stays attributed to the first source. Different collections work
   (SRC-OBJ-DUPLICATES). Moving to `stable_doc_id` needs a re-index or migration, so it is left to the owner.
3. **Beat re-dispatches a never-synced source every minute while its first sync runs.** The runs are skipped while locked, but one
   jitter-delayed task then runs an extra incremental sync right after. The result is correct, but the work is wasted. The task should re-check
   "still due" when it runs.
4. **S3 limitations.**
   - On AWS, a multipart object's LastModified is the upload's start. An upload begun more than `cursor_lookback_seconds` before a run's listing and
     completed after it can be missed. Reconcile only deletes.
   - Real AWS was not reachable (no credentials). The AWS default-endpoint path (no `endpoint_url`) is covered by unit tests only. Virtual-hosted
     addressing, custom endpoint, region and STS were verified against `rw-s3`.
5. **The quality gate drops text shorter than 50 characters as `empty_content`.** A short note object or a narrow row (for example `tag: urgent`)
   is skipped and counted, but its content is not indexed. This is a product decision for the owner (P2).
6. **Throughput.** About 4–6 small documents per second, because each row or object is embedded on its own: 1,770 rows took 420–444 s. A
   million-row table would take days. Batching embeddings across documents belongs to P9.
7. **Not implemented.** PostgreSQL `logical_replication` / `pg_notify` CDC (falls back to query mode with a warning). Rows whose cursor column is
   NULL are never synced. MySQL `query` mode keeps a single-value cursor (ties), so `tables` mode is the robust one.
8. **UI.** The source detail drawer is clipped at small viewports, so the tab body is hidden below the tabs (seen at 1024×768).
9. **Harness.** A real-world session's end-of-session sweep deletes the sources of any other session running on the same tenant (one run lost a
   source that way). Run one session at a time; the README says so.
