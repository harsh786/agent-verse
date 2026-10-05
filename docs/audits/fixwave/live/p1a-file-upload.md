# P1a — file upload (A1) on the live stack (2026-10-05)

Branch `live/p1a-file-upload` (from `main` @ `1af77a278`), 16 commits, nothing pushed.
Raw output of the final live run is in `p1a-file-upload/` next to this file:
`real_world_report.md/.json`, `backend_results.jsonl` and `backend_pytest.log`. The files were scanned
for the tenant keys, the `.env` provider keys and the `av_` / `nvapi-` / `sk-` patterns: 0 hits.

## 1. Deployment

- **How the stack was built.** It was built and started from this worktree's compose file:
  `docker-compose -f .claude/worktrees/p1a/agent-verse-backend/infra/docker-compose.yml build/up -d --no-deps`
  for `backend worker subgoal-worker beat workflow-worker`. The frontend was built once at the start.
  - The compose project name is fixed (`name: agentverse-backend`), so the same containers and volumes were reused.
  - No volumes were dropped and no other tenant's data was touched.
  - `agent-verse-backend/.env` in the worktree is an untracked symlink to the main checkout's `.env`. It is gitignored and was never committed.
- **Live code state.** The image COPYs `app/`, and compose bind-mounts `app/{org,gateway,bootstrap,agent/graph.py}` and the migrations from
  the checkout the compose file lives in. The running containers therefore point at this worktree.
  - Main's compose file was not modified, so there is nothing to restore in the repo.
  - After merging, redeploy from the main checkout (`docker-compose -f infra/docker-compose.yml up -d --build …`). That rebinds
    the mounts to main. Removing the worktree before then would break those mounts.
- **Rebuild cost.** No migrations changed. A code-only rebuild takes about 50 s.
- **Health.** After each of the 4 redeploys, `/health/ready` returned 200, and the image has tesseract 5.3 with `eng`, `hin` and `osd`.
- **Tenants.** Credentials from P0 were used: the enterprise tenant as primary, via `/private/tmp/claude-501/rw/env.sh`.

## 2. Verdict per format (owner rule: verified live first; code changed only where a scenario failed)

Every format was run through KB-COMPLEX-CORPUS, plus KB-COMPLEX-LIFECYCLE for DOCX and the new KB-UPLOAD-HARD
difficult cases, live on 2026-10-05. The final run was 12:28–12:54 UTC; a targeted rerun followed at about 13:05 UTC.

| Format | Before (P0 + first P1a runs) | After | Verdict |
|---|---|---|---|
| PDF | pdf, 2-column and CJK passed; **owner-password-only encrypted → 422**; tables: 2/3 facts | encrypted PDF readable (P1a-8); every other PDF case passes; pdf-tables still 2/3 (§5.1) | **OPEN** — retrieval ranking of one table row (P2) |
| DOCX | **edit added a 2nd copy (9 → 17 chunks)**; tracked insertions dropped; header/footer not read; heading alignment 0.67 | one document, unchanged chunk ids kept (1.0), old clause gone; insertions, header and footer indexed; alignment 1.0 | **COMPLETE (fixed: P1a-4, -5, -7, -9 + follow-up)** |
| PPTX | pptx and pptx-notes (notes, table, group shapes) pass | same | **COMPLETE (already)** |
| XLSX | **merged title row became the header; merged region cells reached only the first row** | `Title:` caption, real header row, region on every row | **COMPLETE (fixed: P1a-10 + follow-up)** |
| CSV | **20,000-row CSV cut at 10,000 rows, reported `truncated: false`** (the asked-about row was lost) | all 20,000 rows (2,857 chunks, 150.7 s), row found and answered; truncation is reported | **COMPLETE (fixed: P1a-11)** |
| HTML | **nav, cookie banner, sidebar, newsletter footer and skip link indexed** (the image has no trafilatura/bs4, so the regex fallback ran); Arabic RTL passed | chrome dropped; headings, lists and table rows kept | **COMPLETE (fixed: P1a-12)** |
| MD | md, md-code and md-mixed-lang pass | same. One final-run search hit a transient `hybrid (strategy deadline exceeded)` 503; the rerun passed | **COMPLETE (already)** |
| Scanned PDF (OCR) | **422 "scanned images need OCR"**; a typed letter's scanned annex silently dropped | per-page OCR with page citations: corpus scan, 3-page scan and mixed PDF pass | **COMPLETE (fixed: P1a-1, -6)** |
| PNG (OCR) | **low-quality receipt misread** (TJ-5531 → TJ-5534, so the RAG answer quoted the wrong job); the sideways photo was only rescued by LLM vision | both read by Tesseract (0.93 / 0.95 confidence, no vision call) | **COMPLETE (fixed: P1a-2, -6)** |
| ZIP | **415 "unsupported binary file"**; zip bombs got the same 415 | nested mixed archive indexed per member with `archive.zip/member` citations; bombs refused 422 in under 1 s | **COMPLETE (fixed: P1a-3)** |

Cross-format behaviour:
- **Dedup.** Identical bytes under another name or extension are deduplicated and now return the stored `document_id` (P1a-5).
- **Delete.** Removes the document's chunks and vectors and leaves its neighbour intact.
- **Refusals.** Password PDF, truncated PDF and PNG, garbage DOCX, empty file and both zip bombs each get a 422 that names the reason,
  nothing is stored and the stack stays healthy.
- **Size limit.** A DOCX of exactly 52,428,800 bytes ingests (7.3 s); one byte more is a 413 (0.46 s).

## 3. Scenario results

| Scenario | First run (P1a baseline) | Final |
|---|---|---|
| KB-COMPLEX-CORPUS (10 formats) | 8/10 (scan_pdf 422, zip 415) | **10/10** |
| KB-COMPLEX-EMBEDDINGS | pass | pass (719 chunks, 100 % embedded, 2048-d) |
| KB-COMPLEX-LIFECYCLE | FAIL (2 copies, 9 → 17 chunks, stale clause served) | **pass** (1 copy, liability + invoice chunk ids unchanged, 8 chunks deleted) |
| KB-COMPLEX-CSV-SCALE | pass | pass |
| KB-RETRIEVAL-HARD (regression check on the new chunker) | — (P0: hit@1 0.885, hit@5 0.962, MRR 0.917) | **pass**: hit@1 0.966, hit@5 1.0, MRR 0.983, answer acc 0.931, citation acc 1.0 |
| KB-REAL-DOCS | FAIL at the goal step (risk classifier, as in P0) | FAIL at the goal step (worker queue starvation, §5.3). Its upload/search part passes (7/7 formats ranked first) |
| KB-UPLOAD-HARD (19 new) | 9/19 | **18/19** (pdf-tables open). md-mixed-lang hit a transient search timeout in the final run and passed the rerun |
| KB-UPLOAD-REFUSALS (new) | FAIL (zip bombs 415) | **pass** (7/7 refused with reasons) |
| KB-UPLOAD-SIZE-LIMIT (new) | pass | pass |
| KB-UPLOAD-DUPLICATES (new) | FAIL (dedup returned `document_id: null`) | **pass** |

Final-run timings and outcomes (upload seconds, chunks):

| Case | Upload time and chunks | Outcome |
|---|---|---|
| pdf-2col | 1.96 s, 4 chunks | |
| pdf-owner-encrypted | 0.57 s, 1 chunk | |
| pdf-cjk | 0.61 s, 1 chunk | |
| pdf-mixed-scan | 5.78 s, 2 chunks | 1 OCR page |
| scan-3p | 7.45 s, 3 chunks | pages cited correctly |
| docx-redlines | 0.75 s, 4 chunks | |
| xlsx-merged | 0.88 s, 1 chunk | |
| csv-large | 150.7 s, 2,857 chunks | |
| html-boilerplate | 1.83 s, 1 chunk | |
| png-rotated / png-lowq | 1.8 / 0.52 s | |
| zip-nested | 0.9 s, 5 chunks | 4 members indexed; legacy `.doc` skipped with its reason; `__MACOSX`/`.DS_Store` ignored; `../../` name normalised |
| Corpus PDF (72 pages) | 6.7 s, 72 chunks | 8/8 facts with page citations |

Answers and citations: every `/rag/query` in KB-UPLOAD-HARD answered with the correct citation and page, except where noted in §5.
The final run needed 0 provider retries.

## 4. Fixes (each test-first; ruff + mypy strict clean on changed code)

| Commit | Fix | Root cause |
|---|---|---|
| `ddf6552e6` P1a-1 | Pages without a text layer are OCR'd page by page (300 dpi, poppler): page citations kept, `ocr_pages` / `pages_without_text` reported. 503 without any OCR engine, 422 when OCR finds nothing, 422 above 60 scanned pages. A partly scanned PDF without OCR is indexed with a warning | The upload path only OCR'd images; `extract_pdf_pages` raised on a textless PDF and silently skipped textless pages of mixed PDFs |
| `97ae47a4f` P1a-2 | OCR reads `eng` first. `hin+eng` is used only on a weak pass and kept only if its text is ≥ 15 % Devanagari. Weak pages get OSD rotation | Every page was read as `hin+eng`, which misreads Latin digits with high confidence; there was no orientation handling |
| `f216389e6` P1a-3 | ZIP expansion (`app/ingestion/archive.py`). Streaming, 1 MiB blocks, central-directory pre-check. Limits: 100:1 ratio above 1 MiB, 2× upload limit in total, per-member size, 1,000 members, depth 3. Skips are reported. One document per archive with `<archive>/<member>` citations | Archive extraction did not exist anywhere in ingestion |
| `7436a95eb` P1a-4 | Stable upload document id `uuid5(tenant, collection, file name)` with `replace_document=True`; content-derived chunk ids. Legacy random-id copies are removed. A document under legal hold → 409. `replace_existing=false` opt-out | `document_id = uuid4()` per upload, persisted without replace |
| `c51c9163e` P1a-5 | `KnowledgeStore.document_id_by_hash`; dedup returns the holder's id. Includes an e2e_full test on real Postgres for P1a-4/5 | The dedup branch hardcoded `document_id: None` |
| `ea288593a` P1a-6 | Autocontrast clips only the bright end; an empty preprocessed reading is retried on the plain page | `autocontrast(cutoff=2)` clipped 2 % at the dark end. On a sparse A4 scan that turned the page black (Tesseract: one empty word, "confidence" 0.95) |
| `0a4e79e81` P1a-7 | Structure-aware chunker `chunk_structured`: whole lines up to 512 tokens, a new chunk per heading section, heading path carried into continuations, line overlap. DOCX headings become markdown | Fixed 512-token windows shifted every later chunk on any edit (only 2 of 8 chunks survived a one-clause amendment; now 7 of 8) and cut rows and sentences |
| `8cdc7f87c` P1a-8 | Permissions-only encrypted PDFs (RC4 / AES-128 / AES-256) are opened with the empty password; password PDFs → 422 "needs a password" | Every encrypted PDF was refused |
| `57f958aa5` P1a-9 (+ `013e5f487`) | Tracked insertions are read (deletions excluded), in paragraphs and table cells. Headers and footers are extracted once, as their own sections | `Paragraph.text` reads only direct-child runs; headers and footers were never read |
| `9c5a6e5f1` P1a-10 (+ `f1886e46d`) | Title rows become captions; the header is the first row with ≥ 2 labels; merged ranges (read from the sheet XML, linear sweep) fill every covered cell | First non-empty row was taken as the header; read-only sheets expose no merged ranges |
| `845c40cbf` P1a-11 | CSV cap raised from 10,000 to 100,000 rows; truncation reported (`truncated`, a warning) | `CSVParser.MAX_ROWS = 10_000`, reported nowhere |
| `bbcff5010` P1a-12 | HTML read with lxml (a runtime dependency): chrome dropped, `<main>`/`<article>` preferred, headings, lists, code and table rows kept | The image ships neither trafilatura nor bs4, so the regex tag-strip fallback indexed the whole page |
| `4021f0aca` test(real-world) | KB-UPLOAD-HARD / REFUSALS / SIZE-LIMIT / DUPLICATES (`corpus_hard.py`, `test_kb_upload_hard.py`); README documents the scenarios and env vars | — |

Unit / integration tests added: about 80, in 13 new files under `tests/{api,ingestion,ocr,knowledge,e2e_full}`.
Suites run green on the final branch state:
- `tests/ingestion`, `tests/ocr`, `tests/knowledge` and `tests/rag`: 3,816 passed (191 skipped, Docker-only)
- every `tests/api` knowledge/upload/ingest test plus `tests/real_world_harness`: 537 passed
- `tests/rag/test_persisted_rag_store.py` (48) and the new e2e_full test, on testcontainers Postgres
- `ruff check` and `mypy app` (strict, 1,884 files) clean

## 5. Open items

1. **pdf-tables: "How much does an out-of-gauge lift cost?" misses the top 5 (P2 retrieval).**
   - **Extraction is faithful.** The page-1 chunk contains `Out-of-gauge lift per lift 18,600 - pre-booked`, and the other two
     table questions (reefer rate, delay penalty) are answered and cited.
   - **Probe evidence.** On a 6-chunk scratch collection, even the exact phrase "out-of-gauge lift" ranks that chunk 4th.
     `trigram_score` is 0.0 for every hit and the fused score equals `vector_score`, so the lexical legs add nothing for the
     hyphenated term. The dense vector of a page mixing prose with an 11-row table loses to shorter chunks.
   - **To do in P2:** check the FTS/BM25/trigram legs for hyphenated terms (P0 §4.4 already found `plainto_tsquery` AND-ing every
     term); consider table-aware PDF extraction (rows as `header: value`, as DOCX/HTML now do), or table-sized chunks.
2. **Answer synthesis 503s under provider throttling (P2/P5, P0 theme 1).** "Answer synthesis is unavailable" and one
   `hybrid (strategy deadline exceeded)` search occurred in intermediate runs. The scenario now retries twice and records each retry
   (0 in the final run).
3. **KB-REAL-DOCS goal never started (P5/P9).** The goal stayed in `planning` for 480 s.
   - At the time, `goals.enterprise` had 25 queued tasks and `maintenance` had 3,391, on a single 2-slot worker (P0 §4.1).
   - Earlier the same day the goal failed on the risk-classifier false positive (P0 §4.14).
   - Ingestion for this scenario passes.
4. **Formula cells without a cached value** (workbooks written by scripts that never ran Excel's calculation) are still dropped:
   `data_only=True` gives `None`. Not exercised by a failing scenario; noted.
5. **Re-embedding on edit.** Unchanged chunks keep their ids and text, but every chunk of an edited file is re-embedded.
   Reusing embeddings by chunk id would cut cost on large documents.
6. **Product decision for the owner: replace by file name.** Re-uploading the same file name now replaces the document. Generic
   names (`scan.pdf`, `image.png`) from different sources would overwrite each other. Mitigations in place: the response says
   `replaced` / `replaced_document_ids`, `replace_existing=false` keeps both, and legal hold is respected.

## 6. New findings outside A1

- `GET /knowledge/search?top_k=21..100` → 503 "Retrieval service is unavailable". The endpoint accepts up to 100, but
  `RAGExecutionRequest.top_k` is capped at 20, and the resulting ValidationError is mapped to 503 instead of 422 (P2).
- `POST /knowledge/ingest/url` (replace by stable URL id) deletes a previous version without the legal-hold check that
  document delete and now file upload perform (P1d).
- `HTMLParser` is also the connector WEB_PAGE / HTML parser, so P1a-12 changes connector output too, for the better. URL ingest
  (`_fetch_url_content`) still uses its own regex extraction (P0 §4.15, P1d).
- The `maintenance` Celery queue holds 3,391 tasks on a worker that also serves `goals.enterprise` (P9).
