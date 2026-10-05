# AgentVerse real-world scenario report

Generated 2026-10-05T18:24:48+0530 against the live local stack.

Tests: failed=3, passed=34
Scenarios: failed=2, passed=8

## Scenarios

| Scenario | Result | Tests (pass/fail/skip) | Skip reason |
|---|---|---|---|
| KB-COMPLEX-CORPUS | **passed** | 10/0/0 |  |
| KB-COMPLEX-CSV-SCALE | **passed** | 1/0/0 |  |
| KB-COMPLEX-EMBEDDINGS | **passed** | 1/0/0 |  |
| KB-COMPLEX-LIFECYCLE | **passed** | 1/0/0 |  |
| KB-REAL-DOCS | **failed** | 0/1/0 |  |
| KB-RETRIEVAL-HARD | **passed** | 1/0/0 |  |
| KB-UPLOAD-DUPLICATES | **passed** | 1/0/0 |  |
| KB-UPLOAD-HARD | **failed** | 17/2/0 |  |
| KB-UPLOAD-REFUSALS | **passed** | 1/0/0 |  |
| KB-UPLOAD-SIZE-LIMIT | **passed** | 1/0/0 |  |

## Metrics

| Scenario | Test | Metrics |
|---|---|---|
| KB-COMPLEX-CORPUS | test_kb_complex_format[pdf] | upload_ms=6714, chunks=72, facts=8, facts_top5=8, heading_alignment=1.0 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[docx] | upload_ms=1341, chunks=8, facts=5, facts_top5=5, heading_alignment=1.0 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[pptx] | upload_ms=2911, chunks=1, facts=3, facts_top5=3 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[xlsx] | upload_ms=3255, chunks=4, facts=2, facts_top5=2 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[csv] | upload_ms=30631, chunks=624, facts=1, facts_top5=1 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[html] | upload_ms=612, chunks=3, facts=2, facts_top5=2, heading_alignment=1.0 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[md] | upload_ms=298, chunks=2, facts=2, facts_top5=2, heading_alignment=1.0 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[scan_pdf] | upload_ms=2767, chunks=1, facts=1, facts_top5=1 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[png] | upload_ms=840, chunks=1, facts=1, facts_top5=1 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[zip] | upload_ms=443, chunks=3, facts=2, facts_top5=2 |
| KB-COMPLEX-EMBEDDINGS | test_kb_complex_embeddings | total_chunks=719, coverage_pct=100.0 |
| KB-COMPLEX-LIFECYCLE | test_kb_dedup_update_delete | unchanged_chunks_preserved=1.0 |
| KB-RETRIEVAL-HARD | test_kb_retrieval_hard | questions=29, hit_at_1=0.966, hit_at_5=1.0, mrr=0.983, answer_accuracy=0.931, answers_produced=29, citation_accuracy=1.0, search_latency_ms.n=29, search_latency_ms.mean=1977.12, search_latency_ms.p50=1435.919, search_latency_ms.p95=3451.858, search_latency_ms.max=12781.673, rag_latency_ms.n=29, rag_latency_ms.mean=6873.843, rag_latency_ms.p50=5368.28, rag_latency_ms.p95=14809.384, rag_latency_ms.max=18315.988 |
| KB-UPLOAD-HARD | test_hard_upload[pdf-2col] | upload_s=1.96, chunks=4, facts=2, facts_top5=2, rag_answered=1, rag_cited=1, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[pdf-tables] | upload_s=0.36, chunks=2, facts=3, facts_top5=2, rag_answered=2, rag_cited=2, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[pdf-owner-encrypted] | upload_s=0.57, chunks=1, facts=1, facts_top5=1, rag_answered=1, rag_cited=1, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[pdf-cjk] | upload_s=0.61, chunks=1, facts=2, facts_top5=2, rag_answered=1, rag_cited=1, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[pdf-mixed-scan] | upload_s=5.78, chunks=2, facts=2, facts_top5=2, rag_answered=1, rag_cited=1, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[scan-3p] | upload_s=7.45, chunks=3, facts=3, facts_top5=3, rag_answered=2, rag_cited=2, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[docx-redlines] | upload_s=0.75, chunks=4, facts=4, facts_top5=4, rag_answered=2, rag_cited=2, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[docx-hindi] | upload_s=0.43, chunks=1, facts=1, facts_top5=1, rag_answered=1, rag_cited=1, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[pptx-notes] | upload_s=0.72, chunks=1, facts=2, facts_top5=2, rag_answered=1, rag_cited=1, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[xlsx-merged] | upload_s=0.88, chunks=1, facts=3, facts_top5=3, rag_answered=2, rag_cited=2, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[csv-large] | upload_s=150.72, chunks=2857, facts=1, facts_top5=1, rag_answered=1, rag_cited=1, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[csv-cp1252] | upload_s=1.48, chunks=1, facts=1, facts_top5=1, rag_answered=0, rag_cited=0, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[html-boilerplate] | upload_s=1.83, chunks=1, facts=2, facts_top5=2, rag_answered=1, rag_cited=1, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[html-arabic] | upload_s=0.4, chunks=1, facts=1, facts_top5=1, rag_answered=1, rag_cited=1, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[md-code] | upload_s=0.35, chunks=3, facts=2, facts_top5=2, rag_answered=1, rag_cited=1, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[md-mixed-lang] | upload_s=0.56, chunks=2 |
| KB-UPLOAD-HARD | test_hard_upload[png-rotated] | upload_s=1.8, chunks=1, facts=1, facts_top5=1, rag_answered=1, rag_cited=1, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[png-lowq] | upload_s=0.52, chunks=1, facts=1, facts_top5=1, rag_answered=1, rag_cited=1, rag_retries=0 |
| KB-UPLOAD-HARD | test_hard_upload[zip-nested] | upload_s=0.9, chunks=5, facts=4, facts_top5=4, rag_answered=2, rag_cited=2, rag_retries=0 |
| KB-UPLOAD-REFUSALS | test_upload_refusals | refusals=7, refused_correctly=7 |
| KB-UPLOAD-SIZE-LIMIT | test_upload_size_limit | at_limit_s=7.31, over_limit_s=0.46 |
| KB-UPLOAD-DUPLICATES | test_upload_duplicates | documents=2 |

## Tests

| Scenario | Suite | Result | Duration (s) | Key evidence | Failure detail |
|---|---|---|---|---|---|
| KB-COMPLEX-CORPUS | backend | **passed** | 29.3 | {"collection_id": "ef8dbd55e1ab43ea9f72d3a845c0019f", "file": "larkspur-ops-policy-manual.pdf", "bytes": 85227, "upload_http": 201, "chunks": 72, "expected_chunks": [50, 146], "pages_reported": 72, "fact_ranks": {"pdf-co... |  |
| KB-COMPLEX-CORPUS | backend | **passed** | 15.4 | {"collection_id": "ef8dbd55e1ab43ea9f72d3a845c0019f", "file": "bramblewood-vendor-master-agreement.docx", "bytes": 39088, "upload_http": 201, "chunks": 8, "expected_chunks": [5, 30], "fact_ranks": {"docx-termination": 1,... |  |
| KB-COMPLEX-CORPUS | backend | **passed** | 4.3 | {"collection_id": "ef8dbd55e1ab43ea9f72d3a845c0019f", "file": "q3-fy27-operations-review.pptx", "bytes": 43170, "upload_http": 201, "chunks": 1, "expected_chunks": [1, 5], "fact_ranks": {"pptx-notes-hosur": 1, "pptx-cost... |  |
| KB-COMPLEX-CORPUS | backend | **passed** | 16.1 | {"collection_id": "ef8dbd55e1ab43ea9f72d3a845c0019f", "file": "fleet-and-fuel-fy27.xlsx", "bytes": 7335, "upload_http": 201, "chunks": 4, "expected_chunks": [1, 6], "truncated": false, "fact_ranks": {"xlsx-vehicle": 1, "... |  |
| KB-COMPLEX-CORPUS | backend | **passed** | 4.0 | {"collection_id": "ef8dbd55e1ab43ea9f72d3a845c0019f", "file": "shipment-ledger-2026.csv", "bytes": 355913, "upload_http": 201, "chunks": 624, "expected_chunks": [59, 798], "fact_ranks": {"csv-shipment-status": 1}, "table... |  |
| KB-COMPLEX-CORPUS | backend | **passed** | 4.5 | {"collection_id": "ef8dbd55e1ab43ea9f72d3a845c0019f", "file": "it-disaster-recovery-runbook.html", "bytes": 4567, "upload_http": 201, "chunks": 3, "expected_chunks": [1, 11], "fact_ranks": {"html-tier2-rpo": 1, "html-fai... |  |
| KB-COMPLEX-CORPUS | backend | **passed** | 4.4 | {"collection_id": "ef8dbd55e1ab43ea9f72d3a845c0019f", "file": "larkctl-deploy-guide.md", "bytes": 1632, "upload_http": 201, "chunks": 2, "expected_chunks": [1, 5], "fact_ranks": {"md-canary": 1, "md-rollback-logs": 1}, "... |  |
| KB-COMPLEX-CORPUS | backend | **passed** | 1.6 | {"collection_id": "ef8dbd55e1ab43ea9f72d3a845c0019f", "file": "delivery-note-dn-58213-scan.pdf", "bytes": 138785, "upload_http": 201, "chunks": 1, "expected_chunks": [1, 3], "pages_reported": 1, "fact_ranks": {"scan-rece... |  |
| KB-COMPLEX-CORPUS | backend | **passed** | 1.2 | {"collection_id": "ef8dbd55e1ab43ea9f72d3a845c0019f", "file": "wh9-whiteboard-photo.png", "bytes": 30624, "upload_http": 201, "chunks": 1, "expected_chunks": [1, 2], "fact_ranks": {"png-fire-drill": 1}} |  |
| KB-COMPLEX-CORPUS | backend | **passed** | 3.4 | {"collection_id": "ef8dbd55e1ab43ea9f72d3a845c0019f", "file": "people-ops-bundle.zip", "bytes": 647, "upload_http": 201, "chunks": 3, "expected_chunks": [3, 9], "fact_ranks": {"zip-earned-leave": 1, "zip-laptop-desk": 1}... |  |
| KB-COMPLEX-EMBEDDINGS | backend | **passed** | 0.9 | {"collection_id": "ef8dbd55e1ab43ea9f72d3a845c0019f", "embedder": {"status": "available", "provider": "dedicated", "model": "nvidia/nemotron-3-embed-1b", "dimension": 2048, "failed_providers": []}, "chunks_uploaded": 719... |  |
| KB-COMPLEX-LIFECYCLE | backend | **passed** | 10.8 | {"collection_id": "94afd8dcd863479cbb55b55fb911f0ea", "v1": {"chunks": 8, "document_id": "5743204cc6245127b61302848d9089af"}, "reupload": {"http": 201, "chunks": 0, "deduplicated": true}, "unchanged_chunk_ids_before": {"... |  |
| KB-COMPLEX-CSV-SCALE | backend | **passed** | 1.5 | {"top_sources": ["shipment-ledger-2026.csv", "shipment-ledger-2026.csv", "shipment-ledger-2026.csv", "shipment-ledger-2026.csv", "shipment-ledger-2026.csv"], "csv_chunks": 624} |  |
| KB-RETRIEVAL-HARD | backend | **passed** | 256.8 | {"collection_id": "ef8dbd55e1ab43ea9f72d3a845c0019f", "excluded_not_ingested": [], "by_kind": {"numeric": {"n": 6, "top5": 6, "correct": 6}, "negation": {"n": 2, "top5": 2, "correct": 1}, "fact": {"n": 3, "top5": 3, "cor... |  |
| KB-UPLOAD-HARD | backend | **passed** | 20.9 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "meridian-annual-report-fy26.pdf", "bytes": 4617, "upload_http": 201, "upload_s": 1.96, "chunks": 4, "expected_chunks": [2, 8], "response": {"pages": 2, "ocr_... |  |
| KB-UPLOAD-HARD | backend | **failed** | 29.8 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "saltmarsh-tariff-schedule-2027.pdf", "bytes": 3363, "upload_http": 201, "upload_s": 0.36, "chunks": 2, "expected_chunks": [2, 6], "response": {"pages": 2, "o... | E assert not ["tariff-oog: not in the top 5 (top: ['heron-tugs-receipt-tj-5531-photo.png', 'terminal-handbook-bundle.zip/handbook/tariff-extract.pdf', 'harbour-services-agreement-hsa-4471.docx'])"] |
| KB-UPLOAD-HARD | backend | **passed** | 15.3 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "marine-circular-ms-118.pdf", "bytes": 1832, "upload_http": 201, "upload_s": 0.57, "chunks": 1, "expected_chunks": [1, 2], "response": {"pages": 1, "ocr_pages... |  |
| KB-UPLOAD-HARD | backend | **passed** | 15.8 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "east-asia-terminal-guide-cjk.pdf", "bytes": 45280, "upload_http": 201, "upload_s": 0.61, "chunks": 1, "expected_chunks": [1, 2], "response": {"pages": 1, "oc... |  |
| KB-UPLOAD-HARD | backend | **passed** | 39.8 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "vessel-incident-letter-vil-3307.pdf", "bytes": 52864, "upload_http": 201, "upload_s": 5.78, "chunks": 2, "expected_chunks": [2, 4], "response": {"pages": 2, ... |  |
| KB-UPLOAD-HARD | backend | **passed** | 25.8 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "crane-inspection-report-cir-2026-0912-scan.pdf", "bytes": 147408, "upload_http": 201, "upload_s": 7.45, "chunks": 3, "expected_chunks": [3, 6], "response": {... |  |
| KB-UPLOAD-HARD | backend | **passed** | 34.5 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "harbour-services-agreement-hsa-4471.docx", "bytes": 38921, "upload_http": 201, "upload_s": 0.75, "chunks": 4, "expected_chunks": [1, 4], "response": {"pages"... |  |
| KB-UPLOAD-HARD | backend | **passed** | 15.3 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "staff-circular-42-hindi.docx", "bytes": 36904, "upload_http": 201, "upload_s": 0.43, "chunks": 1, "expected_chunks": [1, 2], "response": {"pages": null, "ocr... |  |
| KB-UPLOAD-HARD | backend | **passed** | 14.5 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "berth-planning-review-q3.pptx", "bytes": 37327, "upload_http": 201, "upload_s": 0.72, "chunks": 1, "expected_chunks": [1, 3], "response": {"pages": null, "oc... |  |
| KB-UPLOAD-HARD | backend | **passed** | 27.2 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "crane-maintenance-plan-fy27.xlsx", "bytes": 6409, "upload_http": 201, "upload_s": 0.88, "chunks": 1, "expected_chunks": [1, 4], "response": {"pages": null, "... |  |
| KB-UPLOAD-HARD | backend | **passed** | 26.1 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "gate-transactions-2026.csv", "bytes": 1691189, "upload_http": 201, "upload_s": 150.72, "chunks": 2857, "expected_chunks": [314, 3774], "response": {"pages": ... |  |
| KB-UPLOAD-HARD | backend | **passed** | 5.0 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "fournisseurs-legacy-export.csv", "bytes": 141, "upload_http": 201, "upload_s": 1.48, "chunks": 1, "expected_chunks": [1, 1], "response": {"pages": null, "ocr... |  |
| KB-UPLOAD-HARD | backend | **passed** | 21.9 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "kestrel-wharf-berth-booking.html", "bytes": 4047, "upload_http": 201, "upload_s": 1.83, "chunks": 1, "expected_chunks": [1, 2], "response": {"pages": null, "... |  |
| KB-UPLOAD-HARD | backend | **passed** | 11.5 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "safety-circular-9-arabic.html", "bytes": 654, "upload_http": 201, "upload_s": 0.4, "chunks": 1, "expected_chunks": [1, 1], "response": {"pages": null, "ocr_p... |  |
| KB-UPLOAD-HARD | backend | **passed** | 17.5 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "crane-telemetry-runbook.md", "bytes": 3531, "upload_http": 201, "upload_s": 0.35, "chunks": 3, "expected_chunks": [1, 4], "response": {"pages": null, "ocr_pa... |  |
| KB-UPLOAD-HARD | backend | **failed** | 46.1 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "multilingual-staff-notice.md", "bytes": 483, "upload_http": 201, "upload_s": 0.56, "chunks": 2, "expected_chunks": [1, 3], "response": {"pages": null, "ocr_p... | E AssertionError: GET /knowledge/search -> 503: {"detail":"RAG strategy execution failed: hybrid (strategy deadline exceeded)"} |
| KB-UPLOAD-HARD | backend | **passed** | 10.9 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "gate-pass-gp-77120-sideways.png", "bytes": 35581, "upload_http": 201, "upload_s": 1.8, "chunks": 1, "expected_chunks": [1, 1], "response": {"pages": null, "o... |  |
| KB-UPLOAD-HARD | backend | **passed** | 10.4 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "heron-tugs-receipt-tj-5531-photo.png", "bytes": 59070, "upload_http": 201, "upload_s": 0.52, "chunks": 1, "expected_chunks": [1, 1], "response": {"pages": nu... |  |
| KB-UPLOAD-HARD | backend | **passed** | 39.6 | {"collection_id": "547f8abad824428da0a3325958d88c9d", "file": "terminal-handbook-bundle.zip", "bytes": 36158, "upload_http": 201, "upload_s": 0.9, "chunks": 5, "expected_chunks": [4, 10], "response": {"pages": null, "ocr... |  |
| KB-UPLOAD-REFUSALS | backend | **passed** | 6.6 | {"refusals": {"pdf-encrypted": {"http": 422, "s": 0.35, "detail": "board-minutes-restricted.pdf: the PDF is encrypted and needs a password to open; upload an unprotected copy"}, "pdf-truncated": {"http": 422, "s": 0.05, ... |  |
| KB-UPLOAD-SIZE-LIMIT | backend | **passed** | 9.1 | {"limit": 52428800, "at_limit": {"http": 201, "s": 7.31, "chunks": 1}, "over_limit": {"http": 413, "s": 0.46, "detail": "Upload exceeds the 50 MiB limit"}} |  |
| KB-UPLOAD-DUPLICATES | backend | **passed** | 1.5 | {"uploads": {"ops-bulletin-31.md": {"http": 201, "chunks": 1, "deduplicated": false, "document_id": "7b288a80a24355ef96c7134510a91051"}, "ops-bulletin-31-copy.md": {"http": 201, "chunks": 0, "deduplicated": true, "docume... |  |
| KB-REAL-DOCS | backend | **failed** | 489.4 | {"collection_id": "4bcfc13e8b314ec5ab257717294c71a4", "uploads": {"halcyon-change-notice.pdf": {"http": 201, "chunks": 1}, "zephyrine-contract.docx": {"http": 201, "chunks": 1}, "quokka-pay-launch.pptx": {"http": 201, "c... | E AssertionError: timed out after 480s waiting for KB goal 63e805e1ac8c4070be3e7eefea9d6daa; last={"goal_id": "63e805e1ac8c4070be3e7eefea9d6daa", "status": "planning", "goal": "Using only our knowledge base, answer and cite the source document for each: (1) When is the Project Halcyon database migra... |

## Failure details

### KB-UPLOAD-HARD (failed)

`tests/real_world/test_kb_upload_hard.py::test_hard_upload[pdf-tables]`

```
tests/real_world/test_kb_upload_hard.py:188: in test_hard_upload
    assert not soft, "; ".join(soft)
E   AssertionError: tariff-oog: not in the top 5 (top: ['heron-tugs-receipt-tj-5531-photo.png', 'terminal-handbook-bundle.zip/handbook/tariff-extract.pdf', 'harbour-services-agreement-hsa-4471.docx'])
E   assert not ["tariff-oog: not in the top 5 (top: ['heron-tugs-receipt-tj-5531-photo.png', 'terminal-handbook-bundle.zip/handbook/tariff-extract.pdf', 'harbour-services-agreement-hsa-4471.docx'])"]
```

### KB-UPLOAD-HARD (failed)

`tests/real_world/test_kb_upload_hard.py::test_hard_upload[md-mixed-lang]`

```
tests/real_world/test_kb_upload_hard.py:139: in test_hard_upload
    hits = _search(api, cid, q.question, top_k=5)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/real_world/test_kb_upload_hard.py:59: in _search
    body = api.json_ok("GET", "/knowledge/search", params=params)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/real_world/helpers.py:129: in json_ok
    assert resp.status_code in expect, (
           ^^^^^^^^^^^^^^^^^^^^^^^^^^
E   AssertionError: GET /knowledge/search -> 503: {"detail":"RAG strategy execution failed: hybrid (strategy deadline exceeded)"}
```

### KB-REAL-DOCS (failed)

`tests/real_world/test_knowledge.py::test_kb_real_documents`

```
tests/real_world/test_knowledge.py:153: in test_kb_real_documents
    final = wait_until(lambda: api.json_ok("GET", f"/goals/{goal_id}"), timeout=GOAL_TIMEOUT,
tests/real_world/helpers.py:163: in wait_until
    raise AssertionError(
E   AssertionError: timed out after 480s waiting for KB goal 63e805e1ac8c4070be3e7eefea9d6daa; last={"goal_id": "63e805e1ac8c4070be3e7eefea9d6daa", "status": "planning", "goal": "Using only our knowledge base, answer and cite the source document for each: (1) When is the Project Halcyon database migration window? (2) On what date does the Zephyrine Analytics contract auto-renew? (3) In which city does Quokka Pay launch first?", "priority": "normal", "dry_run": false, "agent_id": "a03fbb22f77046668c1e396427245f0f", "workflow_mode": "single_agent", "created_at": "2026-10-05T12:46:24.962040+00:00", "event_count": 0, "provider_warning": null, "result_artifact": {"version": 1, "kind": "empty", "title": "Using only our knowledge base, answer and cite the source document for each: (1) When is the Project Halcyon database migration window? (2) On what date does the Zephyrine Analytics contract a
```

