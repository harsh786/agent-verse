# Real-world scenario suite

Scenarios that drive the **live local Docker stack** over HTTP exactly like an external
client (nothing here imports `app`), with realistic generated fixtures, and assert real
outcomes: content, counts, citations, statuses, audit rows and timings within bounds.

Everything is opt-in (`AGENTVERSE_REAL_WORLD=1`). Run the whole suite plus the Playwright
UI spec and get a report:

```bash
AGENTVERSE_TENANT_FILE=/path/tenant.json scripts/run_real_world.sh /tmp/rw-report
# just some scenarios:
RW_ONLY="kb_complex or kb_retrieval" RW_SKIP_UI=1 scripts/run_real_world.sh /tmp/rw-report
```

or directly:

```bash
cd agent-verse-backend
AGENTVERSE_REAL_WORLD=1 AGENTVERSE_TENANT_FILE=/path/tenant.json RW_RESULTS_FILE=/tmp/rw.jsonl \
  uv run pytest tests/real_world --no-cov -W default -rs
uv run python -m tests.real_world.report /tmp/rw.jsonl "" /tmp/rw-report
```

The report (`real_world_report.md` / `.json`) has one verdict per scenario (parametrized
tests rolled up), skip reasons, a metrics table (hit@k, MRR, answer / citation accuracy,
latency percentiles, throughput, cost, per-strategy quality) and failure details with
matching backend log lines. Secrets are masked everywhere.

Offline validation (no stack): `uv run pytest tests/real_world_harness --no-cov` checks the
fixtures against the platform's own extractors, chunker, workflow DSL/compiler and the
scoring/report code; `uv run pytest tests/real_world --collect-only` checks collection.

## Scenarios

| Scenario | File | Needs (else SKIPPED with reason) |
|---|---|---|
| KB-COMPLEX-CORPUS (per format: pdf, docx, pptx, xlsx, csv, html, md, scan_pdf, png, zip) | `test_kb_complex.py` | OCR formats skip only when the stack reports no OCR engine (503) |
| KB-COMPLEX-EMBEDDINGS, KB-COMPLEX-LIFECYCLE (dedup / update-on-edit / delete), KB-COMPLEX-CSV-SCALE | `test_kb_complex.py` | – |
| KB-UPLOAD-HARD (19 difficult uploads: 2-column / table / owner-encrypted / CJK / partly scanned / 3-page scanned PDFs, DOCX with tracked changes + headers/footers + merged table cells, Hindi DOCX, PPTX notes + tables + groups, XLSX with title rows + merged cells + formulas, 20,000-row CSV, cp1252 CSV, HTML with page chrome, Arabic RTL HTML, MD with code, mixed-language MD, sideways and low-quality PNGs, nested mixed ZIP) | `test_kb_upload_hard.py` | – |
| KB-UPLOAD-REFUSALS (password PDF, truncated PDF / PNG, garbage DOCX, empty file, zip bomb, nested zip bomb), KB-UPLOAD-SIZE-LIMIT (exactly the limit / one byte over), KB-UPLOAD-DUPLICATES | `test_kb_upload_hard.py` | – |
| KB-RETRIEVAL-HARD (29 known-answer questions) | `test_kb_retrieval.py` | – |
| KB-TENANT-ISOLATION | `test_kb_retrieval.py` | `RW_SECOND_TENANT_API_KEY` / `RW_SECOND_TENANT_FILE` |
| KB-STRATEGIES (every RAG strategy incl. ColBERT when ready) | `test_kb_retrieval.py` | – |
| KB-SOURCES-SYNC-RSS | `test_kb_sources_sync.py` | `RW_FIXTURE_PUBLIC_URL` or `RW_FIXTURE_REACHABLE=1` |
| SRC-REDIS-INCREMENTAL / SRC-MONGO-INCREMENTAL / SRC-S3-INCREMENTAL | `test_kb_sources_sync.py` | `RW_REDIS_URL` / `RW_MONGO_URI` / `RW_S3_*` |
| SRC-OBJ-MIXED (first + incremental sync: add / modify / touch / delete + reconcile, 12 formats), SRC-OBJ-LARGE (multipart objects, size cap), SRC-OBJ-FAILURES (bad secret / unknown key / missing bucket / unreachable endpoint) — each for `minio` (path-style) and `s3` (virtual-hosted, custom region) | `test_src_object_store.py` | `RW_S3_*` (MinIO) / `RW_AWS_*` (AWS-like S3) |
| SRC-OBJ-PAGINATION (1,050 objects, cancel + resume), SRC-OBJ-FILTERS, SRC-OBJ-RETRY (denied + corrupt objects, DLQ, operator retry), SRC-OBJ-STS (session token), SRC-OBJ-REFUSAL (internal endpoints), SRC-OBJ-DUPLICATES (same bytes under two keys, two sources on one prefix) | `test_src_object_store.py` | as above; SRC-OBJ-RETRY changes MinIO policies via `docker exec` (`RW_MINIO_CONTAINER`) |
| SRC-DB-SYNC (realistic schema, view, JSON / NULL / UTF-8, a bulk load larger than a batch; inserted / updated / deleted rows + reconcile), SRC-DB-TABLE-RETRY (an ungranted table is synced once granted), SRC-DB-FAILURES (bad password / unknown user / unreachable port / missing database / missing table / internal hosts) — PostgreSQL and MySQL | `test_src_oltp.py` | `RW_PG_ROOT_PASSWORD` / `RW_MYSQL_ROOT_PASSWORD` |
| SRC-MONGO-SYNC (replica set: 3 collections, BSON types, a 150-item array, 9-level nesting; inserts / change-stream updates / reconciled deletes), SRC-MONGO-HOST-CHANGE, SRC-MONGO-D2 (legacy v5 ids moved to v8), SRC-MONGO-TLS (requireTLS: CA, no / wrong CA, every TLS weakening refused), SRC-MONGO-STALL (hung server with timeoutMS=0), SRC-MONGO-REFUSAL, SRC-MONGO-FAILURES | `test_src_mongodb.py` | `RW_MONGO_ROOT_PASSWORD`, `RW_MONGO_READER_PASSWORD`, `RW_TLS_DIR` |
| SRC-MONGO-KILL-SWITCH | `test_src_mongodb.py` | `RW_MONGO_KILL_SWITCH=off` on a stack with `INGESTION_CONNECTOR_MONGODB_ENABLED=false` (+ `RW_MONGO_KILL_SOURCE_ID`) |
| MCP-MONGO-REGISTER (catalog payload, masked display_url, test, tools/list), MCP-MONGO-TOOLS (workflow tool steps: limit clamps, read-only aggregate, refused operators, error id), MCP-MONGO-ERRORS, MCP-MONGO-ISOLATION, MCP-MONGO-HITL (supervised agent's delete waits for approval; real LLM) | `test_mcp_mongodb.py` | `RW_MONGO_ROOT_PASSWORD`, `RW_MONGO_TOOL_PASSWORD`; `RW_SECOND_TENANT_FILE` for ISOLATION |
| MCP-MONGO-KILL-SWITCH | `test_mcp_mongodb.py` | `RW_MONGO_MCP_KILL_SWITCH=off` on a stack with `MCP_CONNECTOR_MONGODB_ENABLED=false` (+ `RW_MONGO_KILL_CONNECTOR_ID`) |
| SRC-REDIS-TYPES (every type incl. RedisJSON, key patterns, caps, types filter), SRC-REDIS-INCREMENTAL-RESUME (keyspace > max_keys_per_sync over several runs; edits, new keys, reconciled deletes), SRC-REDIS-TLS-AUTH (TLS + CA, ACL reader, a user without JSON.GET -> partial, honest failures, save-time refusals) | `test_src_redis.py` | `RW_REDIS_PASSWORD`, `RW_REDIS_ACL_PASSWORD`, `RW_REDIS_NOJSON_PASSWORD`, `RW_TLS_DIR` |
| SRC-ES-SYNC (index pattern, repeated _ids across indices, PIT paging, incremental + reconciled deletes), SRC-ES-MAPPINGS (explicit mapping, nested, missing sort field), SRC-ES-AUTH-FAILURES (API key, honest failures, refusals) | `test_src_elasticsearch.py` | `RW_ES_PASSWORD`, `RW_ES_READER_PASSWORD` |
| WEB-URL-BOILERPLATE (help-center page in nav / cookie banner / sidebar / footer / inline JS+CSS → article only, Q→A cites the URL), WEB-URL-FORMATS (PDF from a download link served as octet-stream, DOCX, text, Markdown by URL; page citation), WEB-URL-REDIRECTS (301 / 308 reported as moves, 302 followed; redirects to metadata / postgres / redis / backend / localhost refused 400; loop 422), WEB-URL-FAILURES (404 / 410 / 500 / 503 → 502 naming the status, a page that never answers → 504, a closed port → 502), WEB-URL-LARGE (12 MiB HTML and a 60 MB download → 413 quickly; a 1.5 MB markup-heavy page indexed), WEB-URL-REINGEST-HOLD (re-ingest replaces, unchanged is a no-op, a document legal hold blocks ingest/url and reingest with 409 until released), WEB-URL-CHARSET (windows-1252 / Shift_JIS / KOI8-R / windows-1256 / ISO-8859-7 by meta, header, http-equiv, XML declaration; undeclared UTF-8 Hindi) | `test_src_web.py` | the web fixture container (`RW_WEB_CONTROL_URL`), `rw-web` / `rw-web-b` on the operator ingestion allowlist; `RW_PG_CONTAINER` / `RW_REDIS_CONTAINER` release the hold |
| WEB-CRAWL-SITE (depth / page limits, same-host scope, robots.txt Disallow + Crawl-delay, sitemap orphan, canonical + duplicate + query-string / fragment variants, link loops, endless calendar, internal links and redirect; Q→A with the page URL), WEB-CRAWL-INCREMENTAL (changed / added / removed pages, unchanged skipped, reconcile removes the 404 page), WEB-CRAWL-RETRY (503 page → DLQ → operator retry indexes it), WEB-CRAWL-SSRF (internal seeds 422 on save, internal links / redirect hops never requested) | `test_src_web.py` | as above |
| KB-REEMBED-MIGRATION | `test_kb_reembed_scale.py` | – |
| KB-SCALE-SMOKE (~5,000 docs) | `test_kb_reembed_scale.py` | `RW_SCALE=1` (best with `RW_ENTERPRISE_API_KEY`) |
| WF-COMPLEX-PIPELINE, WF-FAILURE-RECOVERY | `test_wf_complex.py` | the stack must reach the fixture server (`RW_FIXTURE_PUBLIC_URL`) — skipped when the HTTP step's SSRF guard refuses it |
| WF-CANCEL-AND-APPROVAL | `test_wf_complex.py` | – |
| TRIGGER-CHAIN, TRIGGER-SIGNED-WEBHOOK | `test_trigger_chain.py` | – |
| SCHED-CRUD, SCHED-PLAN-FLOOR, SCHED-NL | `test_sched_realistic.py` | – |
| SCHED-FIRES-GOAL, SCHED-FIRES-WORKFLOW | `test_sched_realistic.py` | plan floor ≤ `RW_SCHEDULE_MAX_WAIT`, or `RW_ENTERPRISE_API_KEY` |
| GOAL-MULTISTEP-RAG | `test_goal_complex.py` | – (uses web search for the tool step without `RW_FIXTURE_PUBLIC_URL`) |
| GOAL-STRATEGIES, GOAL-STRATEGIES-HIGH-RISK (supervisor, debate, mixture_of_agents) | `test_goal_complex.py` | skipped when the strategy is downgraded (`STRATEGY_RUNTIME_V2_TENANT_ALLOWLIST`) |
| EVAL-GOLDEN, EVAL-GOLDEN-VERSIONING | `test_eval_golden.py` | – |
| GOV-PII-GUARDRAIL, GOV-POLICY-APPROVAL | `test_gov_guardrails.py` | – |
| GOV-GRANT-DENY | `test_gov_guardrails.py` | `RW_GRANTS_ENFORCED=1` (stack runs with `ENFORCE_AGENT_GRANTS`) |
| GOV-BUDGET-CAP | `test_gov_guardrails.py` | an admin key (skipped on 403) |
| KB-REAL-DOCS, KB-REEMBED, KB-RSS*, SRC-REDIS, SRC-MONGO-SYNC, WF-HITL-*, SCHEDULED-WF-HITL, WF-SCHEDULE-PLAN-FLOOR, WF-PUBLISH-APPROVAL, GOAL-HIGH-RISK-* | earlier files | see each module docstring |

## Fixtures

Small fixtures are committed under `fixtures/`: `kb_questions.json` (known-answer
questions: expected document, the chunk text that proves the right chunk, accepted answer
variants), `eval_golden_v1.json` (10 checked golden tasks + the v2 edit) and
`orders.json` (the fixture server's order batch). Large ones are generated at runtime and
deterministically by `corpus.py` (the 60–120 page PDF, 5,000-row CSV, scans, ZIP, …),
`corpus_hard.py` (the KB-UPLOAD-* documents, refusals, the at-limit DOCX) and
`wf_complex.py` (workflow YAML). `fixture_server.py` is the local HTTP server the stack
calls back into (side-effect counters, a flaky endpoint, a failure switch, publish
capture, changing RSS feeds) and, run standalone in a container
(`--serve`), the programmable web site of the WEB-* scenarios (`web_site.py`: pages with
any status / headers / bytes / delay / "fail the first N", robots.txt, redirects, and a
request log with Host header and timestamps).

## Environment variables

Credentials are never printed; every key is registered with the masker.

Run one real-world session at a time per tenant: the session-end sweep deletes the
tenant's leftover workflows, collections and sources — including those of a
concurrently running session.

| Variable | Default | Purpose |
|---|---|---|
| `AGENTVERSE_REAL_WORLD` | – | `1` enables the suite (the runner sets it) |
| `AGENTVERSE_API_KEY` / `AGENTVERSE_TENANT_FILE` | – | test tenant key (or JSON file with `api_key`) — required |
| `AGENTVERSE_BASE_URL` | `http://localhost:8000` | backend URL |
| `BASE_URL`, `API_BASE_URL` | `:5173`, `:8000` | Playwright frontend / API URLs |
| `RW_RESULTS_FILE` | – | JSONL result rows for the report (the runner sets it) |
| `RW_ONLY`, `RW_PYTEST_ARGS`, `RW_SKIP_UI` | – | runner: `-k` filter, extra pytest args, skip Playwright |
| `RW_LOG_CONTAINERS` | compose backend/worker/beat | containers searched for failure log evidence |
| `RW_SECOND_TENANT_API_KEY` / `RW_SECOND_TENANT_FILE` | – | a key of a different tenant (KB-TENANT-ISOLATION) |
| `RW_ENTERPRISE_API_KEY` / `RW_ENTERPRISE_TENANT_FILE` | – | enterprise-plan tenant: short schedule floors, scale smoke |
| `RW_APPROVER_API_KEY` | – | second key of the same tenant (four-eyes publish approval) |
| `RW_FIXTURE_PORT` | random | port of the local fixture server |
| `RW_FIXTURE_HOST` | `host.docker.internal` | how containers reach this host |
| `RW_FIXTURE_PUBLIC_URL` | – | public URL (e.g. a tunnel) of the fixture server; needed by workflow HTTP steps and connectors, whose SSRF / egress guards refuse private addresses |
| `RW_FIXTURE_REACHABLE` | – | `1` when the operator allowlisted the fixture host for connectors |
| `RW_PDF_PAGES` | `72` | pages of the generated policy manual (60–120) |
| `RW_LARGE_CSV_ROWS` | `20000` | rows of the KB-UPLOAD-HARD gate-transaction CSV |
| `RW_UPLOAD_LIMIT_BYTES` | `52428800` | the stack's `KNOWLEDGE_MAX_UPLOAD_BYTES` (KB-UPLOAD-SIZE-LIMIT) |
| `RW_HEADING_ALIGN_MIN` | `0.5` | min share of facts chunked with their section heading |
| `RW_HIT5_MIN`, `RW_ANSWER_ACC_MIN`, `RW_CITATION_ACC_MIN` | `0.8`, `0.7`, `0.7` | KB-RETRIEVAL-HARD thresholds |
| `RW_STRATEGIES` | all listed | comma-separated RAG strategies for KB-STRATEGIES |
| `RW_STRATEGY_QUESTIONS`, `RW_STRATEGY_HIT5_MIN` | `10`, `0.5` | questions per strategy, per-strategy hit@5 floor |
| `RW_RSS_SYNC_MODE` | `full` | sync mode of the RSS source |
| `RW_REDIS_URL` (+ `RW_REDIS_SEED_URL`) | – | Redis as the stack / as this host reaches it |
| `RW_REDIS_SEED_CONTAINER` | – | (SRC-REDIS) seed keys via `docker exec … redis-cli` |
| `RW_MONGO_URI` (+ `RW_MONGO_SEED_URI`, `RW_MONGO_DB`) | –, –, `rw_demo` | MongoDB source |
| `RW_MONGO_COLLECTION`, `RW_MONGO_FACT` | `articles`, – | (SRC-MONGO-SYNC) existing collection / searchable fact |
| `RW_S3_ENDPOINT`, `RW_S3_BUCKET`, `RW_S3_ACCESS_KEY`, `RW_S3_SECRET_KEY` | – | S3 / MinIO source |
| `RW_S3_SEED_ENDPOINT`, `RW_S3_SOURCE_TYPE`, `RW_S3_REGION` | endpoint, `minio`, `us-east-1` | seeding endpoint, connector type, region |
| `RW_S3_ACCESS_KEY`, `RW_S3_SECRET_KEY`, `RW_S3_SEED_ACCESS_KEY`, `RW_S3_SEED_SECRET_KEY`, `RW_S3_BUCKET`, `RW_MINIO_CONTAINER` | –, –, –, –, `rw-p1b`, `agentverse-backend-minio-1` | SRC-OBJ-* (minio): read-only connector user, seeding user, bucket, container for `mc admin policy` |
| `RW_AWS_ACCESS_KEY`, `RW_AWS_SECRET_KEY`, `RW_AWS_ROOT_USER`, `RW_AWS_ROOT_PASSWORD`, `RW_AWS_ENDPOINT`, `RW_AWS_SEED_ENDPOINT`, `RW_AWS_BUCKET`, `RW_AWS_REGION` | –, –, –, –, `http://rw-s3:9000`, `http://localhost:59000`, `rw-aws-docs`, `ap-south-1` | SRC-OBJ-* (s3): an S3 endpoint with virtual-hosted buckets (MinIO with `MINIO_DOMAIN`), STS for SRC-OBJ-STS |
| `RW_OBJ_PAGINATION_OBJECTS`, `RW_OBJ_CANCEL_AFTER`, `RW_OBJ_LARGE_OK_BYTES`, `RW_OBJ_LARGE_OVER_BYTES` | `1050`, `250`, 9 MiB, 14 MiB | SRC-OBJ-PAGINATION / SRC-OBJ-LARGE sizes |
| `RW_PG_ROOT_PASSWORD`, `RW_PG_HOST`, `RW_PG_SEED_PORT`; `RW_MYSQL_ROOT_PASSWORD`, `RW_MYSQL_HOST`, `RW_MYSQL_SEED_PORT` | –, `rw-pg`, `56432`; –, `rw-mysql`, `53306` | SRC-DB-*: admin passwords (seeding from this host) and the hosts the stack reaches |
| `RW_DB_CUSTOMERS`, `RW_DB_SHIPMENTS`, `RW_DB_EVENTS`, `RW_DB_BATCH` | `30`, `120`, `1500`, `500` | SRC-DB-SYNC table sizes and connector batch size |
| `RW_MONGO_ROOT_PASSWORD`, `RW_MONGO_READER_PASSWORD`, `RW_MONGO_TOOL_PASSWORD`, `RW_MONGO_SEED_PORT`, `RW_MONGO_TLS_SEED_PORT`, `RW_PG_CONTAINER` | –, –, –, `57017`, `57018`, `agentverse-backend-postgres-1` | SRC-MONGO-* / MCP-MONGO-*: throwaway `rw-mongo` (replica set `rs0`, alias `rw-mongo-alt`), `rw-mongo-tls` (requireTLS), `rw-mongo-stall`; users `rwroot` (seeding), `rwreader` (`read` on `rw_p1c`), `rwtool` (`readWrite` on `rw_shop`); SRC-MONGO-D2 rewrites chunk ids via `docker exec … psql` |
| `RW_MONGO_ORDERS`, `RW_MONGO_CUSTOMERS`, `RW_MONGO_PRODUCTS`, `RW_MONGO_TOOL_DOCS`, `RW_PROBE_K` | `160`, `30`, `40`, `1500`, `10` | MongoDB fixture sizes; search depth of the "searchable" probes (ranks are recorded) |
| `RW_REDIS_PASSWORD`, `RW_REDIS_ACL_PASSWORD`, `RW_REDIS_NOJSON_PASSWORD`, `RW_REDIS_SEED_PORT`, `RW_REDIS_TLS_SEED_PORT`, `RW_REDIS_KEYS` | –, –, –, `56379`, `56380`, `600` | SRC-REDIS-*: throwaway `rw-redis` (requirepass + ACL users `rwreader` / `rwnojson`), `rw-redis-tls` |
| `RW_ES_PASSWORD`, `RW_ES_READER_PASSWORD`, `RW_ES_URL`, `RW_ES_SEED_URL`, `RW_ES_LOGS_PER_MONTH`, `RW_ES_BATCH` | –, –, `http://rw-es:9200`, `http://127.0.0.1:59200`, `350`, `200` | SRC-ES-*: throwaway Elasticsearch 8 (security on); reader `rwreader` (`read` + `view_index_metadata` on `rw-*`) |
| `RW_WEB_CONTROL_URL`, `RW_WEB_HOST`, `RW_WEB_OTHER_HOST`, `RW_REDIS_CONTAINER` | `http://127.0.0.1:58080`, `rw-web:8080`, `rw-web-b:8080`, `agentverse-backend-redis-1` | WEB-*: control port of the web fixture container (`fixture_server.py --serve`, see `web_site.py`), the names the stack fetches it by (operator-allowlisted), the Redis whose legal-hold cache WEB-URL-REINGEST-HOLD clears |
| `RW_TLS_DIR` | – | directory with the test CA (`ca.pem`) and an unrelated CA (`other-ca.pem`) for the TLS cases |
| `RW_REEMBED_TIMEOUT`, `RW_REEMBED_STABILITY_MIN` | `600`/`300`, `0.9` | re-embed wait; share of questions keeping their top document |
| `RW_SCALE`, `RW_SCALE_DOCS`, `RW_SCALE_CONCURRENCY` | –, `5000`, `8` | scale smoke opt-in, size, parallelism |
| `RW_SCALE_MIN_DOCS_PER_S`, `RW_SCALE_MAX_P95_MS` | `5`, `5000` | throughput floor, search p95 bound during ingest |
| `RW_GOAL_TIMEOUT` | `480` | seconds to wait for a goal |
| `RW_GATE_TIMEOUT`, `RW_FINISH_TIMEOUT` | `300`, `240` | workflow: reach the approval gate / finish |
| `RW_SCHEDULE_MAX_WAIT` | `960` | longest schedule wait before a firing scenario skips |
| `RW_HITL_PERSIST`, `RW_PERSIST_DELAY` | `1`, `45` | WF-HITL-RESTART toggle / pause length |
| `RW_EVAL_TIMEOUT`, `RW_EVAL_PASS_MIN` | `1500`, `0.7` | eval run wait; min share of golden tasks passing |
| `RW_GRANTS_ENFORCED` | – | `1` when the stack enforces agent grants |
| `RW_RSS_URL`, `RW_FEED_HOST`, `RW_KB_URL_DOC` | public feed, `host.docker.internal`, PEP 20 | earlier KB scenarios |
