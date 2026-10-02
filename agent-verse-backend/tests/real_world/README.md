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
| KB-RETRIEVAL-HARD (29 known-answer questions) | `test_kb_retrieval.py` | – |
| KB-TENANT-ISOLATION | `test_kb_retrieval.py` | `RW_SECOND_TENANT_API_KEY` / `RW_SECOND_TENANT_FILE` |
| KB-STRATEGIES (every RAG strategy incl. ColBERT when ready) | `test_kb_retrieval.py` | – |
| KB-SOURCES-SYNC-RSS | `test_kb_sources_sync.py` | `RW_FIXTURE_PUBLIC_URL` or `RW_FIXTURE_REACHABLE=1` |
| SRC-REDIS-INCREMENTAL / SRC-MONGO-INCREMENTAL / SRC-S3-INCREMENTAL | `test_kb_sources_sync.py` | `RW_REDIS_URL` / `RW_MONGO_URI` / `RW_S3_*` |
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
deterministically by `corpus.py` (the 60–120 page PDF, 5,000-row CSV, scans, ZIP, …) and
`wf_complex.py` (workflow YAML). `fixture_server.py` is the local HTTP server the stack
calls back into (side-effect counters, a flaky endpoint, a failure switch, publish
capture, changing RSS feeds).

## Environment variables

Credentials are never printed; every key is registered with the masker.

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
