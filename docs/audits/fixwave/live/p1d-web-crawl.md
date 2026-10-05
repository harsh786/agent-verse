# P1d: HTTP URL ingestion and web crawl on the live stack (A10, 2026-10-06)

Branch `live/p1d-web-crawl` (from `main` @ `a450cd03c`; `main` merged in at the end (`639d30ce7`: OI-1..5, GRD-1), one alembic
head `e7b1c4d9a2f6`): 7 fixes in 5 fix commits, 2 scenario commits and this report. Nothing was pushed. A7 (Drive /
SharePoint / Confluence / Notion) stays deferred by the owner's re-ordering.

The raw output of the live runs is in `p1d-web-crawl/` next to this file (`results.jsonl` + `summary.txt` per run). The
copies were scanned for the tenant keys and the `av_` / `nvapi-` / `sk-` patterns: 0 hits.

| Run | What | Code |
|---|---|---|
| `regress1/` | P1b + P1c regression (SRC-OBJ-*, SRC-DB-*, SRC-MONGO-*, SRC-REDIS-*, SRC-ES-*) | images built from `a450cd03c` (the branch start = `main`) |
| `baseline/` | The 11 new WEB-* scenarios on the unchanged code | same images, `rw-web` allowlisted |
| `run1/` … `run4/` | WEB-* after the fixes | images rebuilt from the branch |
| `final/` | WEB-* + the P1b / P1c regression | images rebuilt from the branch, no dev mount |
| `postmerge/` | WEB-* after merging `main` | images rebuilt from the merged branch |

## 1. Verdicts

| Item | Before (live baseline) | After (live) | Verdict |
|---|---|---|---|
| **HTTP URL ingest** (`POST /knowledge/ingest/url`, document reingest) | 7/7 WEB-URL-* failed. URL ingest used the public-only SSRF guard, so an operator-allowlisted ingestion host was refused (every connector accepts it). Code review + unit reproductions of the rest: PDF / DOCX served by URL decoded as text and indexed as garbage; every failure (404, 500, timeout, refused redirect) a `500 "Failed to fetch"`; no permanent-move report; the body read whole into memory; HTML decoded as UTF-8 whatever its charset; an XHTML page with an XML declaration fell through to the regex tag-strip; **re-ingest replaced a document under legal hold** | 7/7 WEB-URL-* pass | **COMPLETE (fixed: P1d-1, P1d-2, P1d-3, P1d-4, P1d-5)** |
| **Web crawl** (`web_crawl` Source) | 4/4 WEB-CRAWL-* failed: robots.txt ignored (`/private/` fetched), `Crawl-delay` ignored (0.51 s gaps), links with a query string or a fragment dropped, canonical / duplicate pages not handled, pages under 100 characters skipped silently, 8 of 14 pages indexed; a second sync skipped every URL seen before (a changed page was never read again); deleted pages could not be reconciled (422); a page with under 100 characters of text was dropped silently *with its links* (WEB-CRAWL-RETRY's tariff index: the sync reported `failed` with only the dead seed, the 503 page was never reached) | 4/4 WEB-CRAWL-* pass | **COMPLETE (fixed: P1d-6, P1d-7, P1d-8, P1d-9, P1d-11)** |
| Remaining regex HTML strips (P2's `_new_findings`) | parser registry HTML / WEB_PAGE, orchestrator HTML fallback, Zendesk, Confluence connector, Confluence ingestor, e-mail HTML parts, RPA fetch, web-augmented RAG | all use the P1a lxml extractor (`html_to_text`) | **COMPLETE (fixed: P1d-10)** |

**Regression.** `regress1/` (before any P1d code): 26 of 27 passed; SRC-MONGO-SYNC failed on a timing assumption of the
scenario, not the product: the automatic post-sync reconcile (KB-44, queued 30 s after the clean sync 1) removed the 2
deleted documents before the scenario counted after sync 2 (`upstream_deletions_applied … deleted=2` at 20:13:43 in the
ingestion worker log). The scenario now accepts that state (commit `2756d37eb`). SRC-ES-SYNC was deselected by the
`-k "not pagination"` filter there and ran in `final/`. **Final: 28 of 28 P1b/P1c scenarios pass on the P1d images** (§3).

## 2. Deployment, infra and env changes

- **Stack built from this worktree**, as in P1b/P1c: `docker-compose -f .claude/worktrees/p1d-web/agent-verse-backend/infra/docker-compose.yml build`
  (db-migrate, backend, worker, subgoal-worker, beat, workflow-worker; the frontend once at the start), the one-shot `db-migrate`
  (it applied P8b's **`f6a9d4e2b8c5 -> e7b1c4d9a2f6`**, "Widen every id-carrying VARCHAR(32) column to VARCHAR(64)"), then
  `up -d --no-deps --force-recreate` for the app services and a re-created `agentverse-rw-ingestion-worker` (P1b §6.1).
  Three rebuilds (start, after the fixes, after P1d-11), each ~2 min. No volume was dropped. The containers now run images
  built from this branch and bind-mount this worktree's `app/{org,gateway,bootstrap,agent/graph.py}` and migrations: redeploy
  from `main` after merging, before removing the worktree.
- **Operator egress allowlist (local only).** The main checkout's `agent-verse-backend/.env` (gitignored; backup at
  `/private/tmp/claude-501/rw/p1d/dotenv.before-p1d`) became
  `INGESTION_INTERNAL_SOURCE_ALLOWLIST=…,rw-es,rw-web,rw-web-b` (`INGESTION_ALLOW_INTERNAL_SOURCES=true` unchanged). In-container
  probe: `rw-web` / `rw-web-b` pass; `postgres`, `redis`, `host.docker.internal` stay blocked. Production defaults unchanged.
- **Extra container** `agentverse-rw-web` (`python:3.12-slim` running `tests/real_world/fixture_server.py --serve` from this
  worktree, read-only mount), label `p1d=live-test`, aliases `rw-web` / `rw-web-b` on `agentverse-backend_default`, control port
  `127.0.0.1:58080`. Remove with `docker rm -f agentverse-rw-web`. The P1b / P1c containers are untouched.
- **Env for the runner** in `/private/tmp/claude-501/rw/p1d/infra.env` (`RW_WEB_CONTROL_URL`, `RW_WEB_HOST`, `RW_WEB_OTHER_HOST`,
  `RW_PG_CONTAINER`, `RW_REDIS_CONTAINER`); runner `/private/tmp/claude-501/rw/p1d/rw.sh`, redeploy `…/redeploy.sh`.
- **Disk:** the colima VM's docker disk stayed at 81–82 % (29 GB free); nothing was pruned (no dangling images worth it).

## 3. Scenarios (new: `test_src_web.py`, `web_site.py`; `fixture_server.py` gained programmable routes)

| Scenario | Baseline (`baseline/`) | Final (`final/`, `postmerge/`) | Evidence (final) |
|---|---|---|---|
| WEB-URL-BOILERPLATE | 400 blocked (public-only guard) | **pass** | 2.3 s, 1 chunk; the 3 facts indexed, none of 10 chrome markers (nav, cookie banner, sidebar, footer, gtag JS, CSS); rank 1 with the URL; `/rag/query` "2,450 INR … [1]" citing the page |
| WEB-URL-FORMATS | 400 | **pass** | PDF from `downloads/file?id=…` served as octet-stream → `pdf`, 2 pages, the penalty cited on **page 2**; DOCX (paragraphs + table), TXT, MD each read as their kind, 0.3–0.6 s; Q→A "9,800 INR per box" cites the PDF URL |
| WEB-URL-REDIRECTS | 400 | **pass** | 301 and 308 → `moved_permanently {from,to,status}`; 302 followed with no move; redirects to metadata / postgres / redis / backend / localhost → **400 blocked** in 0.03 s, target never requested; loop → **422**; nothing indexed by a refused URL |
| WEB-URL-FAILURES | 400 | **pass** | 404 / 410 / 500 / 503 → **502** "HTTP 404 Not Found: …" (0.03 s); a page that never answers → **504** at 30.0 s; a closed port → **502** "could not connect"; 0 documents |
| WEB-URL-LARGE | 400 | **pass** | 12 MiB HTML → **413** in 0.09 s (HTML limit 10 MiB); 60 MB download → **413** in 0.08 s (declared length over the 50 MiB limit, nothing read); a 0.8 MB markup-heavy page indexed (3 chunks, 0.5 s); `/health/ready` 200 |
| WEB-URL-REINGEST-HOLD | 400 | **pass** | re-ingest: same `document_id`, `replaced: true`, "6 days" served and "4 days" gone, 1 document; unchanged → `deduplicated` with the id; **document legal hold → ingest/url 409 and reingest 409**, the held text kept; after release → 201 replaced |
| WEB-URL-CHARSET | 400 | **pass** | windows-1252 (meta, German), Shift_JIS (header, Japanese), KOI8-R (http-equiv, Russian), windows-1256 (meta, Arabic), ISO-8859-7 (XML declaration, XHTML, Greek), undeclared UTF-8 Hindi: every sentence indexed verbatim, rank 1 for a same-language query; "2,100 rupees per day" answered from the Hindi page with its URL |
| WEB-CRAWL-SITE | fail: 8/14 pages; `/private/` fetched; 0.51 s gaps; canonical article missing | **pass** | 14 documents = expected set (home, 2 sections, 7 articles, orphan from the sitemap, depth-3 archive, calendar months 1–2); depth 4 / month 3 not requested; `/private/` not requested; no `rw-web-b` request; each page requested once (17 requests); min gap **1.0 s** (robots Crawl-delay 1); the rail article reachable by 3 URLs (`article.php?id=7&utm…`, `print/7`, canonical) is **one** document at the canonical URL; internal links / redirect never requested; `max_pages=5` → exactly 5 pages, crawl recorded incomplete; 2 Q→A answered with the right page URL |
| WEB-CRAWL-INCREMENTAL | fail: reconcile 422 | **pass** | sync 1: 6 indexed; sync 2 (news/2 changed, news/5 → 404, news/6 added): **3 indexed, 3 skipped**; new text served, old not; reconcile removed exactly the 404 page (7 → 6) |
| WEB-CRAWL-RETRY | fail: `failed`, the short index page dropped with its links | **pass** | sync `partial`, 3 failures in the DLQ (503 retryable, timeout retryable, 404 seed); the 429 page fetched twice **3.0 s apart** (Retry-After 3) and indexed in the same crawl; after recovery the operator retry indexed the 503 and timeout pages and resolved both entries |
| WEB-CRAWL-SSRF | fail: 0 documents (short page dropped) | **pass** | 8 internal seeds (metadata, postgres, redis, backend, localhost, 127.0.0.1, `file://`, `[::1]`) → **422 on save**; a public page linking metadata / postgres / backend and a same-site hop redirecting to redis: none requested; the seed redirecting (307) to metadata is the one counted `blocked` failure; 1 document |

Final (`final/`, images rebuilt from the branch before the last `main` merge): **39 of 39 passed** in 47 min — the 11 WEB-*
plus 28 P1b / P1c scenarios: SRC-OBJ-MIXED[minio, s3], -FILTERS, -RETRY, -FAILURES[minio, s3], -STS, -REFUSAL, -DUPLICATES;
SRC-DB-SYNC, -TABLE-RETRY, -FAILURES [pg, mysql]; SRC-MONGO-SYNC, -HOST-CHANGE, -D2, -TLS, -STALL, -REFUSAL, -FAILURES;
SRC-REDIS-TYPES, -INCREMENTAL-RESUME, -TLS-AUTH; SRC-ES-SYNC, -MAPPINGS, -AUTH-FAILURES. After merging `main` (`639d30ce7`:
OI-1..5, GRD-1) the images were rebuilt again and the 11 WEB-* scenarios re-run: **11 of 11 passed** (`postmerge/`).

## 4. Fixes (TDD: failing unit / integration tests first, then the live scenario)

| Commit | Fix | Root cause |
|---|---|---|
| `e0a3ac771` P1d-5 | `KnowledgeStore._persist_chunks` refuses a replacement (`KnowledgeLegalHoldError` → 409) inside the writing transaction when the replaced document has chunks and an in-force hold covers it, its collection or the tenant. A new document is never blocked. Covers URL re-ingest, reingest, re-upload and connector re-sync | `/ingest/url` replaced by stable id with no hold check (P1a §6) |
| `81877b39b` P1d-1 | URL ingest fetches through the ingestion egress policy (`guarded_fetch` + `source_client`): operator allowlist honoured, every hop re-checked, 301/308 reported (`moved_permanently`, `moved_to` on chunks), streamed under the upload limit | It used the public-only guard and `request_public`, read the body whole, and reported nothing about moves |
| `81877b39b` P1d-2 | A PDF / DOCX / PPTX / XLSX / image / ZIP served by URL is read with the upload extractors (pages cited, OCR, members); kind from the bytes, then Content-Type, then the URL; structure-aware chunks with content-derived ids | Every response went through `resp.text` |
| `81877b39b` P1d-3 | Honest errors: 400 blocked, 422 redirect loop, 502 upstream status (named) / unreachable, 504 timeout, 413 too large | One `except Exception → 500 "Failed to fetch"` |
| `81877b39b` P1d-4 | `decode_web_text`: BOM, header charset, `<meta charset>` / `http-equiv` / XML declaration, UTF-8, a detected non-Latin encoding, else windows-1252 — for URL ingest, the crawl, HTML uploads and the RPA fetch; the XML declaration is dropped before lxml | UTF-8 with replacement everywhere; lxml refuses a `str` with an encoding declaration, so XHTML hit the regex fallback |
| `6dfa2a40b` P1d-6 | Every sync crawls again from the seeds (unchanged pages skipped by the pipeline's content hash) | The cursor was the set of every URL ever seen, each skipped forever |
| `6dfa2a40b` P1d-7 | Real `max_depth`; same-host scope; robots.txt `Disallow` / `Crawl-delay` (unreadable robots.txt = disallow, RFC 9309); meta robots `noindex` / `nofollow`; per-host delay; 429 / 503 `Retry-After` waited out once; 500 links per page; bounded frontier; `max_page_bytes` | `respect_robots_txt` read and ignored; depth only "> 1"; 20 links per page |
| `6dfa2a40b` P1d-8 | URL normalisation (fragment, default port, tracking / session params, sorted query); `<link rel=canonical>`; same-text pages are one document; sitemap index; linked PDF / DOCX / … read like uploads; extracted text sent as `text/markdown` | `href` regex `[^"'#?]+` dropped every link with `?` or `#`; a `.html` URL made the pipeline re-parse the extracted text as HTML |
| `6dfa2a40b` P1d-9 | A complete crawl records its live pages in the cursor; `iter_live_doc_ids` serves them → reconcile removes pages gone (404/410, unlinked, newly disallowed); an incomplete crawl deletes nothing | No live listing (reconcile 422) |
| `015f8547a` P1d-10 | `html_to_text` (the P1a lxml extractor) in the parser registry, orchestrator fallback, Zendesk, Confluence (connector + ingestor), e-mail parser, RPA fetch, web-augmented RAG | Regex tag-strips kept script/style bodies and page chrome |
| `7e4247812` P1d-11 | `max_pages` counts pages read; all requests stay under 3 × `max_pages` | Live: the page template's dead nav links used up a 5-page budget (2 documents) |
| `2756d37eb`, `0b9e0c4db` | Scenarios, fixture site, README; SRC-MONGO-SYNC timing | – |

New / changed tests: `tests/ingestion/test_web_fetch.py` (32), `tests/api/test_knowledge_url_ingest_p1d.py` (23),
`tests/rag/test_replace_legal_hold_integration.py` (5, real Postgres), `tests/ingestion/connectors/test_web_crawl_connector.py`
(rewritten against a real local site: 29), `tests/ingestion/test_html_extractor_everywhere.py` (8), new cases in
`test_html_parser_boilerplate.py`; the URL-ingest tests that patched the removed `_fetch_url_content` or mocked `httpx.AsyncClient`
wholesale now fake `_fetch_url_resource` / `AsyncClient.send` and assert real statuses (several accepted 500 for success).

Suites on the final tree (after merging `main`): `tests/api tests/ingestion tests/rag tests/rpa tests/knowledge tests/net
tests/observability tests/context` (unit): **9,299 passed**, 11 skipped; integration (one file at a time): `tests/rag/test_replace_legal_hold_integration.py` 5/5 (3 failed before the fix),
`tests/e2e_full/test_knowledge_documents_api_e2e.py::test_url_document_has_a_stable_reachable_id` pass;
`tests/security/test_no_provider_key_literals.py` + `tests/real_world_harness` 33 passed; `ruff check .` clean; `mypy app`
(strict, 1,923 files) clean.

## 5. Owner-rule status per item

- HTTP URL ingest: **COMPLETE (fixed: P1d-1, -2, -3, -4, -5)**.
- Web crawl: **COMPLETE (fixed: P1d-6, -7, -8, -9, -11)**.
- Regex HTML paths: **COMPLETE (fixed: P1d-10)**.

## 6. Open items (routed, not fixed here)

1. **URL ingest has no retry queue.** It is synchronous: a failure is an honest 4xx/5xx to the caller, who retries. The
   retry queue (USR-4) applies to Sources: a `web_crawl` (or `pdf_file` / `docx_file`) Source puts a retryable page in the
   DLQ with a replay reference (WEB-CRAWL-RETRY). A "URL" Source type for single pages would be a product decision.
2. **Pages under the pipeline's minimum length are skipped as empty** (`docs_skipped`; e.g. a 12-word archive notice).
   The crawler no longer drops short pages itself (it did below 100 characters); the pipeline's floor is P1b's / P2's call.
3. **DLQ `permanent_failure`** is set only when the retry scan first processes a non-retryable entry; right after a sync a
   404 seed shows `permanent: false` (its error text says `(permanent)`). Cosmetic; P4/P9.
4. **The crawl's live set lives in the cursor** (one id per page read, ≤ 5,000 ids ≈ 190 KB at the cap), also copied to each
   job's `cursor_before` / `cursor_after`. Fine at the default 100 pages; a crawl of thousands of pages per sync should move
   the set to a table (P9).
5. **Crawl throughput (P9).** One request at a time per Source with the politeness delay (default 1 s): ~1 page/s.
6. **Zendesk** builds its client with plain `httpx.AsyncClient` (the URL is a fixed `*.zendesk.com` host from a label-only
   subdomain, so no SSRF, but no connection pinning either). Noted for P8.
7. **Robots-declared sitemaps** (`Sitemap:` lines) are not followed; only the configured `sitemap_url` (and one level of a
   sitemap index).
