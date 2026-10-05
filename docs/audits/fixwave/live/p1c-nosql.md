# P1c: MongoDB, Redis and Elasticsearch on the live stack (A5, 2026-10-05/06)

Branch `live/p1c-nosql` (from `main` @ `8965f46ca`; `main` merged in at `077303128` and again at `3bcd9cc80`, one alembic head
`e7b1c4d9a2f6`): 13 fixes, 4 test commits, a README update and a test follow-up. Nothing was pushed.

The raw output of the live runs is in `p1c-nosql/` next to this file (`results.jsonl` + `summary.txt` per run). The copies
were scanned for the tenant keys, every throwaway password (MongoDB, Redis, Elasticsearch, test CA) and the
`av_` / `nvapi-` / `sk-` patterns: 0 hits.

| Run | What | Code |
|---|---|---|
| `regress1/` | P1b regression (SRC-OBJ-*, SRC-DB-*) on the merged code | images built from `8965f46ca` |
| `mongo1/` | First MongoDB run (baseline) | same |
| `redis1/` | First Redis run (baseline) | worktree via dev mount, Mongo fixes only |
| `mongo3/`, `mcp3/`, `kill1/`, `redis2/`, `legacy1/`, `es1/` | Runs after each fix | worktree via dev mount |
| `final/` | Final verification | images rebuilt from the merged branch, no dev mount |

## 1. Verdicts

| Source | Before (live baseline) | After (live) | Verdict |
|---|---|---|---|
| **MongoDB ingestion** | Synced (the earlier mongo-ingestion fix wave held), but: every TLS-weakening / platform-file / proxy / ambient-identity URI was **saved (201)** and only its syncs failed; a misspelt collection **completed with 0 documents**; the default reranker cut every chunk at 512 characters, so an order's distinguishing `notes` field (rendered last) was **not retrievable** (exact match ranked 17th of 20) | Every SRC-MONGO-* scenario passes | **COMPLETE (fixed: P1c-1, P1c-2, P1c-3, P1c-4, P1c-13)** |
| **MongoDB MCP connector** | Catalog flow, masking, test, tools/list, limit clamps, refused operators, error ids and the kill switch already worked. Another tenant's connector **health history answered 200 []** (the other routes 404) | Every MCP-MONGO-* scenario passes; UI catalog flow verified in the browser | **COMPLETE (fixed: P1c-5)** |
| **Redis** | A keyspace larger than `max_keys_per_sync` **never progressed** past the first SCAN batch (600 keys at 250/sync stayed at 250; later edits mostly missed); deleted keys **could not be reconciled (422)**; one NOPERM key (**JSON.GET** not granted) **failed the whole sync**; URL query options **saved** then failed | Every SRC-REDIS-* scenario passes | **COMPLETE (fixed: P1c-7, P1c-8, P1c-9, P1c-10, P1c-13)** |
| **Elasticsearch** | **No document could be read from Elasticsearch 8**: every search sorted on `_id` (400 "Fielddata access on the _id field is disallowed"); an index pattern collapsed equal `_id`s of different indices; a document without the sort field poisoned the cursor; no API keys; no reconcile; **no UI** to create the Source | Every SRC-ES-* scenario passes; UI form verified in the browser | **COMPLETE (fixed: P1c-6, P1c-11, P1c-12, P1c-13)** |

**Regression.** The P1b scenarios ran first on the merged code: **12 of 12 passed** (`regress1/`): SRC-OBJ-MIXED[minio],
-FILTERS, -RETRY, -FAILURES[minio, s3], -STS, -REFUSAL, -DUPLICATES, SRC-DB-TABLE-RETRY[pg, mysql],
SRC-DB-FAILURES[pg, mysql]. The same set passes again in `final/`. No regression was found.

**Final verification** (`final/` + `final-es/`, images rebuilt from the merged branch, no dev mount): **33 of 33 passed** in 26 min —
SRC-MONGO-SYNC (230 documents, every probe ranked 1st, MRR 1.0), -HOST-CHANGE, -D2, -TLS, -STALL, -REFUSAL, -FAILURES; MCP-MONGO-REGISTER,
-TOOLS, -ERRORS, -ISOLATION, -HITL; SRC-REDIS-TYPES, -INCREMENTAL-RESUME, -TLS-AUTH; SRC-ES-SYNC (700 documents, both probes 1st),
-MAPPINGS, -AUTH-FAILURES; SRC-REDIS-INCREMENTAL, SRC-MONGO-INCREMENTAL; and 13 P1b scenarios (the 12 above plus SRC-OBJ-MIXED[s3]).
The two kill-switch scenarios need a stack started with the flags off; they passed in `kill1/` on the same connector code.

### MongoDB ingestion: the owner's checklist (each line verified live)

| Requirement | Result | Evidence |
|---|---|---|
| First sync, several collections | PASS | SRC-MONGO-SYNC: 230 documents (orders 160 with ObjectId `_id`, customers 30 with string `_id`, products 40 with int `_id`) in 63–128 s |
| Incremental: new documents | PASS | 3 inserts read by the `_id` scan |
| Updates via change stream | PASS | 2 in-place `$set` updates (no timestamp moved) read from the replica set's change stream: sync 2 indexed exactly 5 (3 new + 2 updated); the new text is served, the old text is not |
| Updates via timestamp cursor | PASS | SRC-MONGO-TLS: a standalone server (no change stream), `cursor_field=updated_at`: sync 2 indexed exactly the 1 bumped document |
| Deletes via reconcile | PASS | `POST /sources/{id}/reconcile` removed exactly the 2 deleted documents (233 → 231) |
| BSON types | PASS | Decimal128 `1249.75` and Int64 `9007199254740993` exact; Binary as `<binary 72 bytes>` / `<binary subtype 4, 16 bytes>`; Regex `/^SKU-0[0-9]{3}$/i`; `Timestamp(1790000000, 7)`; `null`; `false` |
| Arrays > 100, deep nesting | PASS | Item 3 of a 150-item array is retrievable, item 121 is not, and the chunk carries `… 50 more item(s) of 150 not indexed`; a field 9 levels deep is kept as JSON and ranks 1st |
| Searchable with citations | PASS (fixed P1c-3, -4) | 7 probes, each ranked **1st** (MRR 1.0), citation `mongodb://rw-mongo:27017/rw_p1c/<collection>/<_id>` |
| Host change, no duplicates | PASS | SRC-MONGO-HOST-CHANGE: same server as `rw-mongo-alt` plus a `cursor_field` change (a full re-read without deleting first): 40 read, **0 re-indexed**, same 40 ids |
| D2 one-time id reindex | PASS | SRC-MONGO-D2: 10 documents put back under their legacy v5 ids (chunk rows rewritten, migration state cleared, as an upgraded install has them); 1 edited and 1 deleted upstream. Sync 2: migration `completed`, scanned 30, migrated 9, deleted 1; **0 v5 copies left**, 29 documents, the edit served |
| Stalled server, `timeoutMS=0` | PASS | SRC-MONGO-STALL: the URI sets `timeoutMS=0`, `socketTimeoutMS=0`, `;serverSelectionTimeoutMS=0` … against a server that accepts and never answers: validate invalid in **10.0 s**, sync `failed` in **10.1 s**, `Could not reach the MongoDB server … (error id …)` |
| TLS options | PASS | `rw-mongo-tls` (requireTLS, private CA): `tls_ca_pem` works; no CA, a wrong CA and plain TCP fail in ~10.5 s with the classified TLS message and an error id |
| `;` URI-bypass refusal | PASS (fixed P1c-1) | `?…;tlsAllowInvalidHostnames=true`, `%3BtlsInsecure=true`, `;tlsCertificateKeyFile=/app/.env`, `;proxyHost=…`, `%3BproxyHost=…` → **422 on save** (before: 201, then failed syncs) |
| Internal hosts | PASS | postgres, redis, metadata IP, localhost, backend, a multi-host list with one internal member, `mongodb+srv://redis` → 422; the allowlisted `rw-mongo` → 201 |
| Honest failures | PASS (fixed P1c-2) | wrong password / unknown user → `Authentication failed …`; a database without privilege → `Not authorized …`; unreachable port → `Could not reach …`; a missing collection → **`partial` / `failed` naming it** (before: `completed`, 0 documents) — each with an error id, no topology text |
| Kill switch | PASS | `INGESTION_CONNECTOR_MONGODB_ENABLED=false`: create 422, validate invalid, sync of an existing Source 422, health `ok: false` — each naming the flag |

### MongoDB MCP connector: the owner's checklist

| Requirement | Result | Evidence |
|---|---|---|
| Real UI catalog flow, `auth_type=connection_string` | PASS | Browser (frontend from this branch): Connectors → Catalog → MongoDB (Built-in, "Connection URL") → Configure: name `orders-db-ui`, URI without userinfo, database, username, masked password, auth source → Register; the list shows the MongoDB badge and `mongodb://rw-mongo:27017/rw_shop?replicaSet=rs0` (no credentials); **Test → "OK · 105 ms"**; the detail page lists the 8 tools. MCP-MONGO-REGISTER sends the same payload (renamed connection, `type=builtin-mongodb`) |
| Credentials masked in every response | PASS | register / GET / list / PUT responses carry neither the password nor `rwtool:`; `auth_config.url` is `<redacted>`; a PUT echoing the masked values keeps the sealed DSN working (test passes again) |
| `display_url` | PASS | `mongodb://rw-mongo:27017/rw_shop?replicaSet=rs0&authSource=admin` |
| Test connection, list tools | PASS | `passed` in ~70 ms; tools/list has find, find_one, insert_one, update_one, delete_one, aggregate, list_collections, count |
| find with limit clamps | PASS | 1,501 documents: no limit → 100; `limit 5` → 5; `0`, `-5`, `50000` → **1000** (the maximum) |
| Read-only aggregate | PASS | `$match` + `$group` + `$sort` → 5 groups whose counts sum to `count_documents` |
| `$out` / `$where` refused | PASS | `$out`, cross-database `$merge`, `$where`, `$function` → "operator … is not allowed"; no `pwned_out_*` / `merged_*` collection appeared |
| delete_one pauses for HITL | PASS | MCP-MONGO-HITL (supervised agent, real LLM, tools granted): a pending approval for the delete; the document is **still there while pending**; after approval it is deleted and the other 1,500 documents are intact. See §6.2 for what the agent did next |
| Cross-tenant 404 | PASS (fixed P1c-5) | another tenant: GET, test, tools, **health**, PUT, DELETE → 404, not listed |
| Sanitised errors with an id | PASS | wrong password → `Authentication failed … (error id …)`; a database the user may not read → `Not authorized … (error id …)`; a hung server → failed in < 60 s with an error id; no topology, no `rwtool:` |
| Save-time refusals | PASS | postgres, metadata IP, `tlsInsecure`, `;tlsAllowInvalidCertificates`, `tlsCAFile=/etc/passwd`, MONGODB-AWS → 422 |
| Kill switch | PASS | `MCP_CONNECTOR_MONGODB_ENABLED=false`: register 422, test `connector_disabled`, a workflow tool call refused with the operator's reason |

### Redis checklist

| Requirement | Result | Evidence |
|---|---|---|
| Data types | PASS | string, hash, list, set, zset (member **with score** `97.25`), stream, RedisJSON (nested) — each a document, each ranked in the top results with a `redis://rw-redis:6379/0/<key>` citation |
| Key patterns | PASS | six patterns; a key outside them is not indexed; a second Source with `types=hash,json` indexed exactly 3 |
| Caps | PASS | a 6.7 KB string with `max_value_bytes=4096` and an 80-field hash with `max_items=50`: the parts past the caps are not indexed (flagged `truncated`) |
| Incremental, keyspace over several runs | PASS (fixed P1c-7) | 600 keys at `max_keys_per_sync=250`: 250 + 250 + 100 in 3 runs (162 s); then 2 edited + 2 new keys indexed (4), 595 unchanged skipped |
| Deletes | PASS (fixed P1c-8) | reconcile removed exactly the 3 deleted keys (602 → 599) |
| TLS / auth | PASS | `rw-redis-tls` + CA + requirepass; ACL reader `rwreader` (`~rw:*`); no CA / wrong CA → `CERTIFICATE_VERIFY_FAILED`; plain TCP to TLS → connection reset; wrong password → `invalid username-password pair`; keys outside the ACL → `No permissions to access a key` |
| One unreadable key | PASS (fixed P1c-9) | a user without JSON.GET: `partial`, the plain key indexed, the JSON key failed with `NoPermissionError … json.get` |
| Honest failures / refusals | PASS (fixed P1c-10) | unreachable port fails; platform redis, localhost, metadata IP, `redis://redis`, a Sentinel naming platform redis → 422; URL query options and unknown types → **422 on save** (before: 201) |

### Elasticsearch checklist

| Requirement | Result | Evidence |
|---|---|---|
| Index patterns | PASS (fixed P1c-6) | `rw-<t>-logs-*` over two monthly indices whose `_id`s repeat: **700 documents** (before the fix the pattern was the key: equal `_id`s would have collapsed); citations name the concrete index (`…/rw-…-logs-2026.10/_doc/evt-00023`) |
| Pagination over many documents | PASS (fixed P1c-6) | 700 documents in pages of 200 through a point in time + `search_after` on `[@timestamp, _shard_doc]` (scroll where PIT is missing); 176 s |
| Mappings | PASS | explicit mapping (text, keyword, date, object, **nested** comments): body and nested comment retrievable; a document without the sort field indexed and the cursor not poisoned |
| Incremental | PASS | 3 new + 2 updated (newer `@timestamp`) → sync 2 indexed exactly 5 (1 boundary re-read skipped) |
| Deletes | PASS (fixed P1c-6) | reconcile removed the 2 deleted documents (703 → 701) |
| Auth | PASS (fixed P1c-6, -12) | basic (`rwreader`: `read` + `view_index_metadata` on `rw-*`); **API key**; a `read`-only role syncs (integration) |
| Honest failures | PASS | wrong password / no credentials / bogus API key → 401; an index the reader may not read → 403; missing index → 404 `index_not_found`; the default `@timestamp` on an index that does not map it → `sort_field '@timestamp' is not mapped …; set sort_field …`; unreachable port fails; each in ~3 s |
| Refusals | PASS | postgres, redis, metadata, localhost, backend, `file://` → 422 |
| UI | PASS (fixed P1c-11) | Browser: Add Source → NoSQL Database now lists elasticsearch / opensearch → the new form (URL, index, auth mode, masked password / API key, sort field, re-read window, batch size) → **Test connection: "Connection OK · 148 ms"** against `rw-es`. (The free UI tenant is at its 2-source quota, so the Source itself was created by the API scenarios.) |

## 2. Deployment, infra and env changes

- **Stack built from this worktree**, as in P1b: `docker-compose -f .claude/worktrees/p1c/agent-verse-backend/infra/docker-compose.yml build`
  (db-migrate, backend, worker, subgoal-worker, beat, workflow-worker, frontend), the one-shot `db-migrate`, then `up -d --no-deps`
  for the app services and the frontend. Migrations ran to `c3f9a1d7e5b2` at the start and to `f6a9d4e2b8c5` (single head) after
  merging `main` (`077303128`). The second merge (`3bcd9cc80`, P8b, migration head `e7b1c4d9a2f6`) came after the final run and is not
  deployed; after it the touched unit suites (8,192 + 630), ruff and mypy (1,918 files) were re-run clean. No volume was dropped. The worktree `.env` is an untracked symlink to the main checkout's `.env`. The containers now
  run images built from this branch; redeploy from `main` after merging.
- **Dev iteration only:** `/private/tmp/claude-501/rw/p1c/p1c-dev-override.yml` (never committed) mounted the worktree's `app/`.
  The final run used rebuilt images without it (checked: no `app/` bind mount).
- **Kill-switch run:** a second override set `INGESTION_CONNECTOR_MONGODB_ENABLED=false` and `MCP_CONNECTOR_MONGODB_ENABLED=false`
  on the app services for SRC-MONGO-KILL-SWITCH / MCP-MONGO-KILL-SWITCH, then the stack was recreated without it (flags unset again).
- **Dedicated ingestion worker** `agentverse-rw-ingestion-worker` (P1b, §6.1 there) re-created from the new image each time.
- **Operator egress allowlist (local only).** The main checkout's `agent-verse-backend/.env` (gitignored; backup at
  `/private/tmp/claude-501/rw/p1c/dotenv.before-p1c`) line became
  `INGESTION_INTERNAL_SOURCE_ALLOWLIST=minio,rw-s3,rw-pg,rw-mysql,rw-mongo,rw-mongo-alt,rw-mongo-tls,rw-mongo-stall,rw-redis,rw-redis-tls,rw-es`
  (`INGESTION_ALLOW_INTERNAL_SOURCES=true` unchanged from P1b). In-container probe: the new names resolve and pass; `postgres` and `redis`
  stay blocked (and every refusal scenario checks the same live). Production defaults are unchanged.
- **Extra containers** on `agentverse-backend_default`, labelled `p1c=live-test`
  (remove with `docker rm -f $(docker ps -aq --filter label=p1c=live-test)`):

  | Container | Image | Purpose | Host port |
  |---|---|---|---|
  | `agentverse-rw-mongo` | `mongo:7.0` | Replica set `rs0`, keyFile auth; aliases `rw-mongo`, `rw-mongo-alt`; users `rwroot`, `rwreader` (`read` on `rw_p1c`), `rwtool` (`readWrite` on `rw_shop`) | `127.0.0.1:57017` |
  | `agentverse-rw-mongo-tls` | `mongo:7.0` | `--tlsMode requireTLS`, cert from a private test CA, `--auth` | `127.0.0.1:57018` |
  | `agentverse-rw-mongo-stall` | `python:3.12-slim` | accepts TCP on 27017 and never answers | – |
  | `agentverse-rw-redis` | `redis/redis-stack-server:7.4.0-v0` | requirepass; ACL users `rwreader` (`~rw:*`, `+@read`, `+info`, `+json.get`) and `rwnojson` | `127.0.0.1:56379` |
  | `agentverse-rw-redis-tls` | same | TLS only (test CA), requirepass | `127.0.0.1:56380` |
  | `agentverse-rw-es` | `elasticsearch:8.15.3` (pulled, 1.3 GB) | single node, security on, HTTP; role `rw_reader` + user `rwreader` | `127.0.0.1:59200` |

  The P1b containers (`rw-pg`, `rw-mysql`, `rw-s3`) are untouched.
- **Credentials** only in `/private/tmp/claude-501/rw/p1c/infra.env` (mode 600); the test CA in `/private/tmp/claude-501/rw/p1c/tls/`.
- **Disk:** the colima VM was at 81–82 % (28–29 GB free) throughout; nothing was pruned.

## 3. Scenarios (new: `test_src_mongodb.py`, `test_mcp_mongodb.py`, `test_src_redis.py`, `test_src_elasticsearch.py`, `mongo_seed.py`)

| Scenario | First live run | Final |
|---|---|---|
| SRC-MONGO-SYNC | indexed 230/230 and change-stream updates worked; 3 probes not retrievable (the deep field, the updated note, the `RTO-5531` order) — P1c-3 / -4 | **pass**, every probe 1st |
| SRC-MONGO-HOST-CHANGE | pass | **pass** |
| SRC-MONGO-D2 | pass | **pass** |
| SRC-MONGO-TLS | 6 TLS-weakening configs saved (201), syncs failed — P1c-1 | **pass** |
| SRC-MONGO-STALL | pass (10 s) | **pass** |
| SRC-MONGO-REFUSAL | 7 file / proxy / ambient-identity URIs saved (201) — P1c-1 | **pass** |
| SRC-MONGO-FAILURES | a missing collection `completed` with 0 documents — P1c-2 | **pass** |
| SRC-MONGO-KILL-SWITCH | pass | pass (`kill1/`) |
| MCP-MONGO-REGISTER / -TOOLS / -ERRORS | pass | **pass** |
| MCP-MONGO-ISOLATION | health history 200 for the other tenant — P1c-5 | **pass** |
| MCP-MONGO-HITL | the gate paused the delete and the document survived; the scenario itself was wrong twice (approval id key; the agent had no grant under `ENFORCE_AGENT_GRANTS`, so the approved call was refused `no_grant_for_agent`) | **pass** |
| MCP-MONGO-KILL-SWITCH | pass | pass (`kill1/`) |
| SRC-REDIS-TYPES | JSON.GET NOPERM failed the whole sync after 4 of 9 keys — P1c-9 (and the fixture's reader gained `+json.get`) | **pass** |
| SRC-REDIS-INCREMENTAL-RESUME | stuck at 250 of 600 keys; reconcile 422 — P1c-7, -8 | **pass** |
| SRC-REDIS-TLS-AUTH | URL query options saved (201) — P1c-10 | **pass** |
| SRC-ES-SYNC / -MAPPINGS / -AUTH-FAILURES | (P1c-6 was found first against a real Elasticsearch 8.15 testcontainer — the same image as `rw-es` — where all 6 tests failed on the `_id` sort) | **pass** |
| SRC-REDIS-INCREMENTAL, SRC-MONGO-INCREMENTAL (earlier file) | expected deletions without reconcile, and an empty family | updated (reconcile, family) — **pass** |

The probes use `top_k=10` and record each rank (`RW_PROBE_K`): ranking is P2's subject; ingestion is proven when the chunk is
retrievable with its citation. After P1c-3 / -4 every recorded MongoDB and Elasticsearch probe ranked 1st.

## 4. Fixes (TDD: a failing unit / integration test first, then the live scenario)

| Commit | Fix | Root cause |
|---|---|---|
| `f3197657a` P1c-1 | `BaseConnector.check_connection_policy` (pure, offline); MongoDB runs the shared policy through its settings parser; POST / PATCH `/sources` → 422 with the reason, `/sources/validate` lists it | Only syncs applied the MongoDB URI / TLS policy; the MCP connector already refused on register |
| `efbaade0b` P1c-2 | A configured collection that does not exist is a counted failure (`partial`, or `failed` if none exists); `listCollections` with `authorizedCollections` | MongoDB reads a missing collection as empty |
| `98b223006` P1c-3 | The cross-encoder scores the whole chunk (capped at 4,096 characters) | `content[:512]` characters, while the model's limit is 512 tokens: a record's last fields were never seen |
| `65253f086` P1c-4 | The retrieval score is min-max normalised before the 0.6 / 0.4 blend with the cross-encoder | The CE score (0..1) was blended with a raw RRF score (~0.016): lexical agreement of every leg never mattered ("Hosur" ranked 9th of 10, now 3rd) |
| `9452ac555` P1c-5 (+ `fce7e550a`) | `GET /connectors/{id}/health` checks ownership first → 404 | It queried snapshots by id only (no leak; inconsistent answer) |
| `2e8e78202` P1c-6 | Elasticsearch: PIT + `search_after` on `[sort_field, _shard_doc]` (scroll fallback), ids / citations by concrete `_index`, cursor `{field, since}` never moved by a document without the field, look-back window for date fields, mapping check with an honest error, `unmapped_type`, API keys, `iter_live_doc_ids` | Sorting on `_id` (refused by Elasticsearch 8), the pattern as the key, a sentinel sort value as the cursor |
| `083511571` P1c-10 | Redis `check_connection_policy` → 422 on save | Only syncs parsed the settings |
| `daa54b411` P1c-8 | Redis `iter_live_doc_ids` (SCAN, no values) → reconcile | No live listing |
| `9fd57435a` P1c-7, P1c-9 | Redis cursor v2 keeps the offset inside the SCAN batch; NOPERM / type-changed keys are counted failures, the rest syncs | The cursor pointed at the batch start; any `ResponseError` aborted the sync |
| `6995c7fb8` P1c-11 | Elasticsearch / OpenSearch in the Sources wizard (NoSQL Database) with their own form; the connector honours `auth_mode` | No UI entry existed |
| `d88fd4140` P1c-12 | A 403 on the mapping lookup syncs without the type | A `read`-only role was refused although it can read |
| `a166d6e6d` P1c-13 | The catalogue's `supports_deletion` is true for MongoDB, Redis, Elasticsearch / OpenSearch; a test pins it to `lists_upstream` | Metadata never updated when the listings were added |
| `5257db855`, `a76178105`, `35dd8400d`, `62ff1487f` | Scenarios + README | – |

New / changed tests: `tests/api/test_source_save_connection_policy.py` (25), `tests/context/test_rerank_ce_input.py` (3),
`tests/ingestion/test_elasticsearch_source_integration.py` (7, real Elasticsearch 8.15 with security), `tests/ingestion/test_connector_deletion_metadata.py`,
new cases in `test_mongodb_source_integration.py`, `test_redis_source_integration.py`, `test_connectors_mongodb_catalog_flow.py`,
`test_connectors_cross_tenant_extra.py`, `tests/mcp/test_connector_health_history.py`, `test_redis_connector.py`, `test_new_connectors.py`,
`test_stable_doc_ids_connectors.py`; vitest `ElasticsearchForm.test.tsx` (6).

Suites on the final tree (after merging `main`):
- `tests/context`, `tests/rag`, `tests/ingestion`, `tests/net`, `tests/mcp` + the touched API files (unit, `-m "not integration"`): **10,390 passed**, 11 skipped (Docker-only), 2 failed — the health-history tests that assumed an unknown connector answers 503; fixed in `fce7e550a` (that file: 5 passed).
- `tests/api -k "connector or source or ingestion or knowledge or dlq"`: 933 passed.
- Integration files run one at a time (testcontainers): MongoDB source (16), Redis source (17), Elasticsearch (7), MongoDB DLQ, MongoDB id migration — all pass.
- `ruff check .` clean; `mypy app` (strict, 1,914 files) clean; vitest `src/features/ingestion` + `src/features/connectors`: 371 passed; `tsc --noEmit` clean; eslint 0 errors.

## 5. Owner-rule status per item

- MongoDB ingestion: **COMPLETE (fixed: P1c-1, -2, -3, -4, -13)**.
- MongoDB MCP connector: **COMPLETE (fixed: P1c-5)**.
- Redis: **COMPLETE (fixed: P1c-7, -8, -9, -10, -13)**.
- Elasticsearch: **COMPLETE (fixed: P1c-6, -11, -12, -13)**.

## 6. Open items (routed, not fixed here)

1. **Retrieval calibration (P2).** P1c-3 / -4 fix two plain bugs in the default rerank stage. Whether the blend should be rank-based (RRF of the
   cross-encoder rank and the retrieval rank) or weighted differently is P2's call; a one-word proper-noun query still ranks the only
   lexical match 3rd, behind two documents the cross-encoder prefers. The same root cause probably explains P1a's "PDF table-row ranking"
   item (a long chunk whose fact sat past character 512).
2. **Agent core after an approved destructive step (P4 / P5).** In MCP-MONGO-HITL the approved `mongodb_delete_one` ran (`{'deleted': 1}`), then:
   - every delete raised **two** approvals (the plan step's keyword gate and the tool's `write_high` gate);
   - a step that only *reports* the result ("if deletedCount is 1, report that the order was deleted") was gated too (keyword "deleted");
   - the verifier / replanner **re-planned and re-ran the delete 7 more times** (each `deleted: 0`, each approved again) — 22 approvals,
     goal `failed` although the work was done. Reproduced in `final/` (19 approvals, goal `failed`, target deleted, 1,500 others intact).
   Safety held (nothing else deleted, no delete before approval); the behaviour belongs to P4 (HITL) / P5 (agent core).
3. **Workflow tool steps bypass the tool risk gate (P4).** A workflow `tool` step calls `mongodb_delete_one` without an approval (tool_step.py
   calls the MCP client directly). Workflows are human-authored and have their own HITL / publish approval, so this is a design question for P4.
4. **Redis document ids include the host** (`redis://host:port/db/key`). Renaming the Redis host re-ids every key (as MongoDB did before TG-13);
   with P1c-8 a reconcile removes the old copies, but they coexist until then. Moving to `stable_doc_id(config, db, key)` needs the same kind of
   one-time migration MongoDB got (owner decision 2), so it is left to the owner.
5. **Elasticsearch limits.** Updates are seen only when they move `sort_field` (no change feed); documents without the sort field are read by the
   first sync and by `_doc` Sources only; `date_nanos` uses millisecond look-back; a custom CA for HTTPS clusters is not supported (the pinned
   transport takes no `verify` context) — public CAs work.
6. **Throughput (P9).** 3.6–4 documents/s for MongoDB, Redis and Elasticsearch alike (one embedding call per document), as in P1b.
7. **Harness.** The session-end sweep removed the kill-switch fixtures created outside pytest (as the README warns); the second kill-switch
   run needs them re-created (`RW_MONGO_KILL_SOURCE_ID`, `RW_MONGO_KILL_CONNECTOR_ID`).
