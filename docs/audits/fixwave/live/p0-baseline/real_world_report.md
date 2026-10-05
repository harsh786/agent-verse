# AgentVerse real-world scenario report

Generated 2026-10-05T15:13:49+0530 against the live local stack.

Tests: failed=21, passed=36, skipped=7
Scenarios: failed=18, passed=26, skipped=7

## Scenarios

| Scenario | Result | Tests (pass/fail/skip) | Skip reason |
|---|---|---|---|
| EVAL-GOLDEN | **failed** | 0/1/0 |  |
| EVAL-GOLDEN-VERSIONING | **failed** | 0/1/0 |  |
| GOAL-HIGH-RISK-APPROVE | **failed** | 0/1/0 |  |
| GOAL-HIGH-RISK-DENY | **passed** | 1/0/0 |  |
| GOAL-MULTISTEP-RAG | **failed** | 0/1/0 |  |
| GOAL-STRATEGIES | **failed** | 0/3/0 |  |
| GOAL-STRATEGIES-HIGH-RISK | **failed** | 2/1/0 |  |
| GOV-BUDGET-CAP | **failed** | 0/1/0 |  |
| GOV-GRANT-DENY | **failed** | 0/1/0 |  |
| GOV-PII-GUARDRAIL | **failed** | 0/1/0 |  |
| GOV-POLICY-APPROVAL | **failed** | 0/1/0 |  |
| KB-COMPLEX-CORPUS | **failed** | 8/2/0 |  |
| KB-COMPLEX-CSV-SCALE | **passed** | 1/0/0 |  |
| KB-COMPLEX-EMBEDDINGS | **passed** | 1/0/0 |  |
| KB-COMPLEX-LIFECYCLE | **failed** | 0/1/0 |  |
| KB-REAL-DOCS | **failed** | 0/1/0 |  |
| KB-REEMBED | **failed** | 0/1/0 |  |
| KB-REEMBED-MIGRATION | **passed** | 1/0/0 |  |
| KB-RETRIEVAL-HARD | **passed** | 1/0/0 |  |
| KB-RSS | **passed** | 1/0/0 |  |
| KB-RSS-LOCAL | **passed** | 1/0/0 |  |
| KB-SCALE-SMOKE | **passed** | 1/0/0 |  |
| KB-SOURCES-SYNC-RSS | **skipped** | 0/0/1 | needs RW_FIXTURE_PUBLIC_URL (a tunnel to the local fixture server; the stack's connector egress guard refuses host.docker.internal) or RW_FIXTURE_REACHABLE=1 when the operator allowlisted the fixture host |
| KB-STRATEGIES | **failed** | 0/1/0 |  |
| KB-TENANT-ISOLATION | **passed** | 1/0/0 |  |
| SCHED-CRUD | **passed** | 1/0/0 |  |
| SCHED-FIRES-GOAL | **failed** | 0/1/0 |  |
| SCHED-FIRES-WORKFLOW | **passed** | 1/0/0 |  |
| SCHED-NL | **passed** | 1/0/0 |  |
| SCHED-PLAN-FLOOR | **passed** | 1/0/0 |  |
| SCHEDULED-WF-HITL | **passed** | 1/0/0 |  |
| SRC-MONGO-INCREMENTAL | **skipped** | 0/0/1 | needs RW_MONGO_URI: a MongoDB the stack can reach (egress-allowlisted) |
| SRC-MONGO-SYNC | **passed** | 1/0/0 |  |
| SRC-REDIS | **passed** | 1/0/0 |  |
| SRC-REDIS-INCREMENTAL | **skipped** | 0/0/1 | needs RW_REDIS_URL: a Redis the stack can reach (egress-allowlisted) |
| SRC-S3-INCREMENTAL | **skipped** | 0/0/1 | needs RW_S3_ENDPOINT, RW_S3_BUCKET, RW_S3_ACCESS_KEY, RW_S3_SECRET_KEY: an S3/MinIO bucket the stack can reach (egress-allowlisted) |
| TRIGGER-CHAIN | **failed** | 0/1/0 |  |
| TRIGGER-SIGNED-WEBHOOK | **passed** | 1/0/0 |  |
| UI-APPROVALS-LIVE | **passed** | 1/0/0 |  |
| UI-KB-DOCS | **passed** | 1/0/0 |  |
| WF-APPROVALS-TENANT-ISOLATION | **passed** | 1/0/0 |  |
| WF-CANCEL-AND-APPROVAL | **passed** | 1/0/0 |  |
| WF-COMPLEX-PIPELINE | **skipped** | 0/0/1 | the workflow HTTP step's SSRF guard refused the local fixture server (SSRF guard [workflow]: hostname 'host.docker.internal' resolved to blocked IP '192.168.5.2' (anti-rebinding check)SSRF guard [workflow]: hostname 'host.docker.i); set RW_FIXTURE_PUBLIC_URL t... |
| WF-FAILURE-RECOVERY | **skipped** | 0/0/1 | the workflow HTTP step's SSRF guard refused the local fixture server (SSRF guard [workflow]: hostname 'host.docker.internal' resolved to blocked IP '192.168.5.2' (anti-rebinding check)SSRF guard [workflow]: hostname 'host.docker.i); set RW_FIXTURE_PUBLIC_URL t... |
| WF-HITL-APPROVE | **passed** | 1/0/0 |  |
| WF-HITL-CANCEL | **passed** | 1/0/0 |  |
| WF-HITL-REJECT | **passed** | 1/0/0 |  |
| WF-HITL-RESTART | **passed** | 1/0/0 |  |
| WF-INPUT-DEFAULTS | **passed** | 1/0/0 |  |
| WF-PUBLISH-APPROVAL | **failed** | 0/1/0 |  |
| WF-SCHEDULE-PLAN-FLOOR | **skipped** | 0/0/1 | plan enterprise allows every-minute schedules (floor 60) |

## Metrics

| Scenario | Test | Metrics |
|---|---|---|
| EVAL-GOLDEN | test_eval_golden_dataset | own_pass_rate=0.0, platform_task_pass_rate=0.0, verdict_agreement=1.0 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[pdf] | upload_ms=3070, chunks=72, facts=8, facts_top5=8, heading_alignment=1.0 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[docx] | upload_ms=807, chunks=8, facts=5, facts_top5=5, heading_alignment=0.6667 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[pptx] | upload_ms=510, chunks=1, facts=3, facts_top5=3 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[xlsx] | upload_ms=694, chunks=4, facts=2, facts_top5=2 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[csv] | upload_ms=22576, chunks=586, facts=1, facts_top5=1 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[html] | upload_ms=454, chunks=2, facts=2, facts_top5=2, heading_alignment=1.0 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[md] | upload_ms=247, chunks=1, facts=2, facts_top5=2, heading_alignment=1.0 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[scan_pdf] | upload_ms=13, chunks=0 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[png] | upload_ms=780, chunks=1, facts=1, facts_top5=1 |
| KB-COMPLEX-CORPUS | test_kb_complex_format[zip] | upload_ms=12, chunks=0 |
| KB-COMPLEX-EMBEDDINGS | test_kb_complex_embeddings | total_chunks=675, coverage_pct=100.0 |
| KB-COMPLEX-LIFECYCLE | test_kb_dedup_update_delete | unchanged_chunks_preserved=1.0 |
| KB-REEMBED-MIGRATION | test_kb_reembed_while_querying | queries_during_reembed=178, failed_queries=0, query_latency_ms.n=178, query_latency_ms.mean=570.181, query_latency_ms.p50=446.716, query_latency_ms.p95=1488.728, query_latency_ms.max=2371.681, top1_stability=1.0, hit_at_5_before=1.0, hit_at_5_after=1.0 |
| KB-SCALE-SMOKE | test_kb_scale_smoke | docs=5000, errors=0, elapsed_s=235.9, docs_per_s=21.196, rate_limited_retries=0, listed=5000, reported_total=5000 |
| KB-RETRIEVAL-HARD | test_kb_retrieval_hard | questions=26, hit_at_1=0.885, hit_at_5=0.962, mrr=0.917, answer_accuracy=0.962, answers_produced=26, citation_accuracy=1.0, search_latency_ms.n=26, search_latency_ms.mean=1180.348, search_latency_ms.p50=1068.644, search_latency_ms.p95=1631.921, search_latency_ms.max=2306.425, rag_latency_ms.n=26, rag_latency_ms.mean=5730.709, rag_latency_ms.p50=3975.46, rag_latency_ms.p95=12018.216, rag_latency_ms.max=12074.08 |
| KB-STRATEGIES | test_kb_strategies | questions_per_strategy=10 |

### RAG strategies (tests/real_world/test_kb_retrieval.py::test_kb_strategies)

| Strategy | Available | hit@5 | MRR | Answer acc. | Citation acc. | p50 ms | p95 ms | Errors / reason |
|---|---|---|---|---|---|---|---|---|
| adaptive | yes | 0.0 | 0.0 | 0.0 | 0.0 | 940.767 | 1220.124 | 10 |
| agentic | yes | 0.0 | 0.0 | 0.0 | 0.0 | 77.195 | 94.077 | 10 |
| agentic_chunking | yes | 0.0 | 0.0 | 0.0 | 0.0 | 28.541 | 39.684 | 10 |
| code | yes | 0.0 | 0.0 | 0.0 | 0.0 | 334.776 | 444.1 | 10 |
| colbert | yes | 0.0 | 0.0 | 0.0 | 0.0 | 2651.476 | 6735.493 | 10 |
| corrective | yes | 0.0 | 0.0 | 0.0 | 0.0 | 324.117 | 352.344 | 10 |
| flare | yes | 0.3 | 0.3 | 1.0 | 1.0 | 1974.532 | 3380.111 | 7 |
| fusion | yes | 1.0 | 1.0 | 1.0 | 1.0 | 6482.875 | 17125.832 | 0 |
| graph | yes | 0.0 | 0.0 | 0.0 | 0.0 | 52.166 | 2497.993 | 10 |
| hybrid | yes | 1.0 | 1.0 | 1.0 | 1.0 | 4633.187 | 15455.782 | 0 |
| hyde | yes | 0.9 | 0.9 | 1.0 | 1.0 | 7319.841 | 20075.542 | 1 |
| memory_augmented | yes | 0.0 | 0.0 | 0.0 | 0.0 | 342.052 | 392.161 | 10 |
| modular | yes | 0.0 | 0.0 | 0.0 | 0.0 | 95.166 | 157.894 | 10 |
| multi_hop | yes | 0.4 | 0.4 | 1.0 | 1.0 | 8215.272 | 16694.739 | 6 |
| naive | yes | 0.9 | 0.9 | 1.0 | 1.0 | 3319.258 | 7217.193 | 1 |
| raft | no | | | | | | | raft_model_selection_required (probe HTTP 503) |
| raptor | yes | 0.0 | 0.0 | 0.0 | 0.0 | 31.256 | 38.585 | 10 |
| self_rag | yes | 0.5 | 0.5 | 1.0 | 1.0 | 4967.583 | 13017.851 | 5 |
| speculative | yes | 0.0 | 0.0 | 0.0 | 0.0 | 316.674 | 396.197 | 10 |
| web_augmented | yes | 0.3 | 0.3 | 1.0 | 1.0 | 10310.797 | 31614.337 | 7 |

## Tests

| Scenario | Suite | Result | Duration (s) | Key evidence | Failure detail |
|---|---|---|---|---|---|
| EVAL-GOLDEN | backend | **failed** | 371.1 | {"dataset_id": "19a6da3c-d341-407b-9c86-5d2fb30ab15b", "result_id": "44f7e895-d7c1-4f1a-ad36-29ee6d7b99ba", "status": "completed", "gate_passed": false, "avg_score": 0.0, "scoring": "lexical", "per_task": {"t01": {"statu... | E assert not ['only 0% of golden tasks pass their checks (min 70%)', 'the eval run does not record which dataset version it ran'] |
| EVAL-GOLDEN-VERSIONING | backend | **failed** | 0.0 | {"dataset_id": "703c5c96-96db-4f6f-aff0-4c652dad4db5", "edit_attempts": {"PATCH": 404, "PUT": 404}} | E assert None is not None |
| GOAL-MULTISTEP-RAG | backend | **failed** | 115.8 | {"per_goal_budget_usd": 10.0, "goal_id": "913fef37a1184edca4316a7e629247a5", "agent_id": "7d2214b418d64b269431c342d22f229a", "tool_mode": "web_search", "status": "failed", "answer_head": "{\"tool\": null, \"result\": \"I... | E + failed |
| GOAL-STRATEGIES | backend | **failed** | 15.1 | {"submit_http": 202, "goal_id": "1d6ef462d53b476e949a94933e139ee1", "status": "failed", "mode": "supervisor", "answer_head": "No structured result was produced.", "event_types": ["chunking_strategy_selected", "execution_... | E + failed |
| GOAL-STRATEGIES | backend | **failed** | 10.2 | {"submit_http": 202, "goal_id": "9ccbba0b387f4ec683474cbd6b1b65a9", "status": "failed", "mode": "debate", "answer_head": "No structured result was produced.", "event_types": ["chunking_strategy_selected", "execution_stra... | E + failed |
| GOAL-STRATEGIES | backend | **failed** | 482.7 | {"submit_http": 202, "goal_id": "f4643652594847a38b785dd6ec4c8088"} | E AssertionError: timed out after 480s waiting for goal f4643652594847a38b785dd6ec4c8088 to finish; last={"goal_id": "f4643652594847a38b785dd6ec4c8088", "status": "executing", "goal": "Draft a five-item checklist a dispatcher should follow before releasing a cold-chain pharmaceutical consignment (te... |
| GOAL-STRATEGIES-HIGH-RISK | backend | **passed** | 29.5 | {"goal_id": "d313772754c04c7cb5a976afd197f674", "status": "executing", "approvals": 1, "approval_action": "Step 4: Remove all stale staging records from the list and collect their IDs.", "reject_http": 200, "final_status... |  |
| GOAL-STRATEGIES-HIGH-RISK | backend | **passed** | 25.4 | {"goal_id": "e8133dd9958547cc9105dfead0ab68cc", "status": "executing", "approvals": 1, "approval_action": "Step 4: Remove all stale staging records from the list and collect their IDs.", "reject_http": 200, "final_status... |  |
| GOAL-STRATEGIES-HIGH-RISK | backend | **failed** | 487.0 | {"goal_id": "6567eaa602094a1d9b899fe517ca932b", "status": "executing", "approvals": 1, "approval_action": "mixture_of_agents coordination run: Demo list (in-memory sample data, no external systems): rec-201 env=staging l... | E AssertionError: timed out after 480s waiting for goal 6567eaa602094a1d9b899fe517ca932b to finish; last={"goal_id": "6567eaa602094a1d9b899fe517ca932b", "status": "executing", "goal": "Demo list (in-memory sample data, no external systems): rec-201 env=staging last_used=2025-02-01; rec-202 env=produ... |
| GOAL-HIGH-RISK-APPROVE | backend | **failed** | 395.8 | {"goal_id": "8ea5660a34b7495bab11caf716ea7c13", "status_at_gate": "executing", "approval_request_id": "c50825e6da3b4d7294ae75dac929d98f", "approval_action": "Step 1: Create an in\u2011memory list of records with fields I... | E + failed |
| GOAL-HIGH-RISK-DENY | backend | **passed** | 130.6 | {"goal_id": "92b14913938f4afa8ca07c8d73ccadab", "status_at_gate": "executing", "approval_request_id": "7b1ade9f57a14ea2acc3b9e94204d545", "approval_action": "Step 1: Create in-memory list of records with id, env, last_us... |  |
| GOV-PII-GUARDRAIL | backend | **failed** | 25.3 | {"guardrail_id": "596c0ad6-7541-40b1-b91d-1b316c24b28b", "tester": {"http": 200, "body": "{\"allowed\":true,\"risk_score\":0.35,\"action\":\"redacted\",\"violations\":[{\"layer\":\"pii\",\"category\":\"pii_email\",\"seve... | E assert not True |
| GOV-GRANT-DENY | backend | **failed** | 65.7 | {"grant_http": 201, "goal_id": "8efdfefc74184db7a6730f96d641e1b3", "status": "failed", "event_types": ["chunking_strategy_selected", "execution_strategy_resolved", "goal_created", "goal_failed", "goal_started", "guardrai... | E assert 'tool_call_blocked_by_grant' in ['goal_created', 'worker_started', 'goal_started', 'chunking_strategy_selected', 'knowledge_retrieved', 'rag_strategy_selected', ...] |
| GOV-POLICY-APPROVAL | backend | **failed** | 52.8 | {"simulation": {"web_search": "require_approval", "shell_exec": "deny", "parse_document": "allow"}, "goal_id": "6477961c85d24b35b246bcdb06bfe8f5", "status": "executing", "approvals": ["Step 1: Perform a web search for th... | E + where '{"request_id": "b060e914e28e43b8be93a50d38883e9d", "goal_id": "6477961c85d24b35b246bcdb06bfe8f5", "action": "Step 1: ...": "2026-10-05T09:10:02.898270+00:00", "note": "", "approver": null, "required_approvers": 1, "approvals_received": 0}' = mask({'request_id': 'b060e914e28e43b8be93a50d38... |
| GOV-BUDGET-CAP | backend | **failed** | 0.0 | {"budget_before": {"per_goal_usd": 10.0, "per_tenant_daily_usd": 500.0}} | E AttributeError: 'LiveAPI' object has no attribute 'put' |
| KB-COMPLEX-CORPUS | backend | **passed** | 29.4 | {"collection_id": "57dd6fed54bb4db7ac8b459ea0fbfe22", "file": "larkspur-ops-policy-manual.pdf", "bytes": 85227, "upload_http": 201, "chunks": 72, "expected_chunks": [50, 146], "pages_reported": 72, "fact_ranks": {"pdf-co... |  |
| KB-COMPLEX-CORPUS | backend | **passed** | 15.7 | {"collection_id": "57dd6fed54bb4db7ac8b459ea0fbfe22", "file": "bramblewood-vendor-master-agreement.docx", "bytes": 39088, "upload_http": 201, "chunks": 8, "expected_chunks": [5, 30], "fact_ranks": {"docx-termination": 1,... |  |
| KB-COMPLEX-CORPUS | backend | **passed** | 5.0 | {"collection_id": "57dd6fed54bb4db7ac8b459ea0fbfe22", "file": "q3-fy27-operations-review.pptx", "bytes": 43170, "upload_http": 201, "chunks": 1, "expected_chunks": [1, 5], "fact_ranks": {"pptx-notes-hosur": 1, "pptx-cost... |  |
| KB-COMPLEX-CORPUS | backend | **passed** | 4.4 | {"collection_id": "57dd6fed54bb4db7ac8b459ea0fbfe22", "file": "fleet-and-fuel-fy27.xlsx", "bytes": 7335, "upload_http": 201, "chunks": 4, "expected_chunks": [1, 6], "truncated": false, "fact_ranks": {"xlsx-vehicle": 2, "... |  |
| KB-COMPLEX-CORPUS | backend | **passed** | 2.9 | {"collection_id": "57dd6fed54bb4db7ac8b459ea0fbfe22", "file": "shipment-ledger-2026.csv", "bytes": 355913, "upload_http": 201, "chunks": 586, "expected_chunks": [59, 798], "fact_ranks": {"csv-shipment-status": 1}, "table... |  |
| KB-COMPLEX-CORPUS | backend | **passed** | 4.2 | {"collection_id": "57dd6fed54bb4db7ac8b459ea0fbfe22", "file": "it-disaster-recovery-runbook.html", "bytes": 4567, "upload_http": 201, "chunks": 2, "expected_chunks": [1, 11], "fact_ranks": {"html-tier2-rpo": 1, "html-fai... |  |
| KB-COMPLEX-CORPUS | backend | **passed** | 3.4 | {"collection_id": "57dd6fed54bb4db7ac8b459ea0fbfe22", "file": "larkctl-deploy-guide.md", "bytes": 1632, "upload_http": 201, "chunks": 1, "expected_chunks": [1, 5], "fact_ranks": {"md-canary": 1, "md-rollback-logs": 1}, "... |  |
| KB-COMPLEX-CORPUS | backend | **failed** | 0.0 | {"collection_id": "57dd6fed54bb4db7ac8b459ea0fbfe22", "file": "delivery-note-dn-58213-scan.pdf", "bytes": 138785, "upload_http": 422, "chunks": 0, "expected_chunks": [1, 3]} | E assert 422 in (200, 201) |
| KB-COMPLEX-CORPUS | backend | **passed** | 1.3 | {"collection_id": "57dd6fed54bb4db7ac8b459ea0fbfe22", "file": "wh9-whiteboard-photo.png", "bytes": 30624, "upload_http": 201, "chunks": 1, "expected_chunks": [1, 2], "fact_ranks": {"png-fire-drill": 1}} |  |
| KB-COMPLEX-CORPUS | backend | **failed** | 0.0 | {"collection_id": "57dd6fed54bb4db7ac8b459ea0fbfe22", "file": "people-ops-bundle.zip", "bytes": 647, "upload_http": 415, "chunks": 0, "expected_chunks": [3, 9]} | E assert 415 in (200, 201) |
| KB-COMPLEX-EMBEDDINGS | backend | **passed** | 0.1 | {"collection_id": "57dd6fed54bb4db7ac8b459ea0fbfe22", "embedder": {"status": "available", "provider": "dedicated", "model": "nvidia/nemotron-3-embed-1b", "dimension": 2048, "failed_providers": []}, "chunks_uploaded": 675... |  |
| KB-COMPLEX-LIFECYCLE | backend | **failed** | 7.3 | {"collection_id": "9989b44ba8874a02b1366272accf9f46", "v1": {"chunks": 8, "document_id": "f38023e95c5847a3b5b31a0f0fb48bd9"}, "reupload": {"http": 201, "chunks": 0, "deduplicated": true}, "unchanged_chunk_ids_before": {"... | E assert not ['the superseded clause (75 days) is still served after the edit: the re-upload added a second copy instead of replaci...dor-master-agreement.docx after the edit (want 1)', 'chunk count grew from 9 to 17 on an edit that changed one clause'] |
| KB-COMPLEX-CSV-SCALE | backend | **passed** | 1.6 | {"top_sources": ["shipment-ledger-2026.csv", "shipment-ledger-2026.csv", "shipment-ledger-2026.csv", "shipment-ledger-2026.csv", "shipment-ledger-2026.csv"], "csv_chunks": 586} |  |
| KB-REEMBED-MIGRATION | backend | **passed** | 161.9 | {"collection_id": "85df1ff9f3cc45c3923f02db8bb9bcdc", "reembed_http": 202, "reembed": {"status": "completed", "total": 12, "processed": 12, "model": "dedicated/nvidia/nemotron-3-embed-1b", "error": null}, "changed_top1":... |  |
| KB-SCALE-SMOKE | backend | **passed** | 247.8 | {"tenant": "enterprise", "collection_id": "d860b918af2143c8aff991494ab9e890", "first_errors": []} |  |
| KB-RETRIEVAL-HARD | backend | **passed** | 179.7 | {"collection_id": "57dd6fed54bb4db7ac8b459ea0fbfe22", "excluded_not_ingested": ["scan-received-by", "zip-earned-leave", "zip-laptop-desk"], "by_kind": {"numeric": {"n": 6, "top5": 6, "correct": 6}, "negation": {"n": 2, "... |  |
| KB-TENANT-ISOLATION | backend | **passed** | 0.1 | {"foreign_search_http": 404, "foreign_rag_http": 404, "foreign_documents_http": 404, "foreign_health_http": 200, "second_tenant_global_rag_http": 503, "leaks": []} |  |
| KB-STRATEGIES | backend | **failed** | 662.0 | {"catalogue": {"naive": {"available": true, "state": "implemented", "reason": null}, "hybrid": {"available": true, "state": "implemented", "reason": null}, "hyde": {"available": true, "state": "implemented", "reason": nu... | E assert not ['naive: 1 failed queries (pdf-cold-chain: /rag/query(naive) -> 503 {"detail": "Answer synthesis is unavailable"})', '...e: 10 failed queries (pdf-cold-chain: /rag/query(adaptive) -> 503 {"detail": "Answer synthesis is unavailable"})', ...] |
| KB-SOURCES-SYNC-RSS | backend | **skipped** | 0.0 | {} | Skipped: needs RW_FIXTURE_PUBLIC_URL (a tunnel to the local fixture server; the stack's connector egress guard refuses host.docker.internal) or RW_FIXTURE_REACHABLE=1 when the operator allowlisted the fixture host |
| SRC-REDIS-INCREMENTAL | backend | **skipped** | 0.0 | {} | Skipped: needs RW_REDIS_URL: a Redis the stack can reach (egress-allowlisted) |
| SRC-MONGO-INCREMENTAL | backend | **skipped** | 0.0 | {} | Skipped: needs RW_MONGO_URI: a MongoDB the stack can reach (egress-allowlisted) |
| SRC-S3-INCREMENTAL | backend | **skipped** | 0.0 | {} | Skipped: needs RW_S3_ENDPOINT, RW_S3_BUCKET, RW_S3_ACCESS_KEY, RW_S3_SECRET_KEY: an S3/MinIO bucket the stack can reach (egress-allowlisted) |
| KB-REAL-DOCS | backend | **failed** | 24.5 | {"collection_id": "244c9d5e35244c88b3ed9c7a983a5e71", "uploads": {"halcyon-change-notice.pdf": {"http": 201, "chunks": 1}, "zephyrine-contract.docx": {"http": 201, "chunks": 1}, "quokka-pay-launch.pptx": {"http": 201, "c... | E + failed |
| KB-REEMBED | backend | **failed** | 8.4 | {"collection_id": "244c9d5e35244c88b3ed9c7a983a5e71", "health_before": {"total_chunks": 7, "embedding_dim": 2048, "needs_reembed": true, "drift_severity": "high"}, "url_ingest_http": 201, "url_ingest": {"chunks_ingested"... | E assert not ['pep-0020 (url)'] |
| SCHED-CRUD | backend | **passed** | 116.0 | {"schedule_id": "7d8c192fafd0448684f36a8213951f2f", "next_fire": {"reported": "2026-10-06T09:00:00+00:00", "expected": "2026-10-06T09:00:00+00:00"}, "edit_http": 200, "next_fire_after_edit": {"reported": "2026-10-05T10:3... |  |
| SCHED-PLAN-FLOOR | backend | **passed** | 0.1 | {"plan": "enterprise", "floor_s": 60, "at_floor_http": 201, "below_floor_http": 422} |  |
| SCHED-NL | backend | **passed** | 1.9 | {"http": 201, "parsed": [{"type": "cron", "cron": "0 9 * * 1-5"}]} |  |
| SCHED-FIRES-GOAL | backend | **failed** | 240.9 | {"plan": "enterprise", "interval_s": 60, "schedule_id": "5cee448255414a0cad3b60a81ee4356d"} | E AssertionError: timed out after 240s waiting for the schedule to fire a goal; last={"runs": [{"run_id": "ef8d132f-7240-476d-95ae-40036394cab8", "goal_id": "7ae2e60ab1bf412cbb7d9edc4a6a090a", "status": "executing", "skip_reason": null, "started_at": "2026-10-05T09:38:42.486717", "duration_ms": null... |
| SCHED-FIRES-WORKFLOW | backend | **passed** | 60.6 | {"plan": "enterprise", "cron": "* * * * *", "workflow_id": "3ab46acc-afa0-44d9-9c97-55900fc98004", "publish_http": 200, "run_id": "bb0618ef-a9cb-4030-a72e-38fae8760b58", "trigger_type": "schedule"} |  |
| SCHEDULED-WF-HITL | backend | **passed** | 67.5 | {"plan": "enterprise", "plan_floor_s": 60, "workflow_id": "b144fac0-315d-4070-a997-176c3a868060", "cron": "* * * * *", "publish_http": 200, "run_id": "26dad578-2c64-4bba-99f3-9cab4427b9e0", "trigger_type": "schedule", "s... |  |
| WF-SCHEDULE-PLAN-FLOOR | backend | **skipped** | 0.0 | {"plan": "enterprise", "plan_floor_s": 60} | Skipped: plan enterprise allows every-minute schedules (floor 60) |
| KB-RSS | backend | **passed** | 15.0 | {"feed": "https://github.com/python/cpython/releases.atom", "feed_entries": 10, "collection_id": "b571131504aa4cbe88641664939451fd", "source_id": "e10575496f2e42efa707da6248d9d8f9", "health": {"http": 200, "body": "{\"ok... |  |
| KB-RSS-LOCAL | backend | **passed** | 4.1 | {"feed": "http://host.docker.internal:52758/feed.xml", "source_id": "1a19b2a34c7844f097f2050ee9fdd32d", "health": {"http": 200, "body": "{\"ok\":false,\"latency_ms\":0.0,\"error\":\"SSRF guard [rss]: hostname 'host.docke... |  |
| SRC-REDIS | backend | **passed** | 0.0 | {"catalogue_has_redis": true, "source_id": "f4afaa78850c466ba20ac991e53f00f1", "mode": "egress-guard (set RW_REDIS_URL for real ingestion)", "health": {"http": 200, "body": "{\"ok\":false,\"latency_ms\":0.0,\"error\":\"S... |  |
| SRC-MONGO-SYNC | backend | **passed** | 0.0 | {"catalogue_has_mongodb": true, "source_id": "1d1e44078bd54acf92ff16c9f1a9a390", "mode": "egress-guard (set RW_MONGO_URI for real ingestion)", "health": {"http": 200, "body": "{\"ok\":false,\"latency_ms\":0.0,\"error\":\... |  |
| TRIGGER-CHAIN | backend | **failed** | 27.4 | {"consumer_id": "07e5087c-48dd-4ece-a3aa-b3c8b3ccf998", "producer_id": "52f9fb4d-9297-4ae8-8287-5c5fbe72f5ba", "channel": "rw-batch-ready-eeba76e2", "consumer_run_id": "8e716bd5-0202-420b-ae94-3aa63bbdfa34", "consumer_pa... | E + failed |
| TRIGGER-SIGNED-WEBHOOK | backend | **passed** | 5.2 | {"trigger_id": "7feca4a57fc0486a9826b1369c802a29", "bad_signature_http": 401, "delivery_http": 200, "delivery_body": "{\"status\":\"accepted\",\"webhook_type\":\"github\",\"dispatched\":1,\"failed\":0}", "replay_http": 2... |  |
| WF-COMPLEX-PIPELINE | backend | **skipped** | 3.1 | {"workflow_id": "a81e3abc-769f-4ab3-b20a-c8c0c6366318", "run_id": "cd9a931e-109b-4e9d-9799-a9567ed09fd9", "fixture_base": "http://host.docker.internal:65097"} | Skipped: the workflow HTTP step's SSRF guard refused the local fixture server (SSRF guard [workflow]: hostname 'host.docker.internal' resolved to blocked IP '192.168.5.2' (anti-rebinding check)SSRF guard [workflow]: hostname 'host.docker.i); set RW_FIXTURE_PUBLIC_URL to a tunnel to RW_FIXTURE_PORT s... |
| WF-FAILURE-RECOVERY | backend | **skipped** | 3.1 | {"workflow_id": "dea0393d-61e0-43e0-b301-340047bbe09d", "run_id": "413ae8cd-2b3d-4655-b462-0c0f8c9a1344"} | Skipped: the workflow HTTP step's SSRF guard refused the local fixture server (SSRF guard [workflow]: hostname 'host.docker.internal' resolved to blocked IP '192.168.5.2' (anti-rebinding check)SSRF guard [workflow]: hostname 'host.docker.i); set RW_FIXTURE_PUBLIC_URL to a tunnel to RW_FIXTURE_PORT s... |
| WF-CANCEL-AND-APPROVAL | backend | **passed** | 8.1 | {"workflow_id": "eda2ac5c-09c8-4d62-aef4-0a735f5fb225", "run_id": "8fa896df-ce6a-421e-ad7f-6601c8e4d244", "approval_request_id": "03b2ca4723164bcba20332213544cd89", "approval_after_cancel": {"status": "cancelled", "actio... |  |
| WF-HITL-APPROVE | backend | **passed** | 10.2 | {"workflow_id": "4ac17286-d456-488a-bc54-7a0964ab5e95", "run_id": "fd3b1dec-7cbf-46de-a012-0b408e8243c1", "status_at_gate": "waiting_hitl", "steps_at_gate": {"draft_report": "complete", "manager_approval": "waiting_hitl"... |  |
| WF-HITL-REJECT | backend | **passed** | 11.2 | {"workflow_id": "e52dfab1-17ce-4b7d-b845-1ae2d1567444", "run_id": "180d6a40-1f8a-4cc7-b254-e93490ae9d3e", "status_at_gate": "waiting_hitl", "steps_at_gate": {"draft_report": "complete", "manager_approval": "waiting_hitl"... |  |
| WF-HITL-RESTART | backend | **passed** | 51.2 | {"workflow_id": "f67dfa34-ee61-494e-b133-acb16d4a26d2", "run_id": "e735bd72-ac6c-4cb1-8dd4-b7288d78238b", "status_at_gate": "waiting_hitl", "approval_request_id": "61defd960bde41d28f0ced52a6bef06b", "status_after_delay":... |  |
| WF-HITL-CANCEL | backend | **passed** | 6.1 | {"workflow_id": "851d5445-7b6d-47c9-a0ee-1bf7349e8de4", "run_id": "15b8d9d3-faae-47ea-a179-b5a780189781", "status_at_gate": "waiting_hitl", "approval_request_id": "5e8997e9f4bd4edea92f3fe158925b16", "cancel_http": 202, "... |  |
| WF-INPUT-DEFAULTS | backend | **passed** | 6.1 | {"run_id": "2c0c0fed-1b5c-47a6-a1d8-e130dcf82cdf", "resolved_prompt_head": "You are writing the weekly status report for the Payments Platform team for week 2026-W40. Facts: Shipped UPI autopay retries (checkout success ... |  |
| WF-APPROVALS-TENANT-ISOLATION | backend | **passed** | 0.0 | {"listed": 0, "foreign_items": 0, "foreign_tenants": [], "foreign_workflows": []} |  |
| WF-PUBLISH-APPROVAL | backend | **failed** | 0.2 | {"workflow_id": "d51a2f52-1034-4154-b918-47d8a4b1c903", "enable_http": 200, "direct_publish_http": 409, "submit_http": 202, "status_after_submit": "pending_approval", "self_approve_http": 409, "self_approve_detail": "{\"... | E assert 'workflow.created' in ['workflow.publish_approved', 'workflow.publish_submitted', 'workflow.published', 'workflow.updated'] |
| UI-APPROVALS-LIVE | playwright | **passed** | 11.7 | {"attachments": []} |  |
| UI-KB-DOCS | playwright | **passed** | 2.5 | {"attachments": []} |  |

## Failure details

### EVAL-GOLDEN (failed)

`tests/real_world/test_eval_golden.py::test_eval_golden_dataset`

```
tests/real_world/test_eval_golden.py:133: in test_eval_golden_dataset
    assert not soft, "; ".join(soft)
E   AssertionError: only 0% of golden tasks pass their checks (min 70%); the eval run does not record which dataset version it ran
E   assert not ['only 0% of golden tasks pass their checks (min 70%)', 'the eval run does not record which dataset version it ran']
```

Docker log evidence:

```
[worker-1] [2026-10-05 08:38:44,274: INFO/ForkPoolWorker-16] Task app.scaling.tasks.run_ai_ops_dataset[b79daaa2-f4f9-4ce9-ba61-50f1afa53669] succeeded in 362.3411183899989s: {'status': 'completed', 'result_id': '44f7e895-d7c1-4f1a-ad36-29ee6d7b99ba'}
```

### EVAL-GOLDEN-VERSIONING (failed)

`tests/real_world/test_eval_golden.py::test_eval_golden_edit_creates_version`

```
tests/real_world/test_eval_golden.py:154: in test_eval_golden_edit_creates_version
    assert updated is not None, (
E   AssertionError: no API edits a golden task (PATCH/PUT /ai-ops/datasets/{id} -> {'PATCH': 404, 'PUT': 404}); a versioned dataset cannot be maintained
E   assert None is not None
```

### GOAL-MULTISTEP-RAG (failed)

`tests/real_world/test_goal_complex.py::test_goal_multistep_rag`

```
tests/real_world/test_goal_complex.py:87: in test_goal_multistep_rag
    assert final.get("status") == "complete", f"goal ended {final.get('status')}: " \
E   AssertionError: goal ended failed: {"tool": null, "result": "INSUFFICIENT DATA: total H1 diesel cost in INR not provided in context"}
E   assert 'failed' == 'complete'
E     
E     - complete
E     + failed
```

Docker log evidence:

```
[worker-1] [2026-10-05 08:41:15,122: WARNING/ForkPoolWorker-17] 2026-10-05 08:41:15 [info     ] goal_learning_recorded         feedback_count=0 goal_id=913fef37a1184edca4316a7e629247a5 lifecycle_state=active memory_id=2f3d6b363f9452c28420e4cd9ff21d6a outcome=failed
[worker-1] [2026-10-05 08:41:15,131: INFO/ForkPoolWorker-17] Task app.scaling.tasks.run_goal[9f4d6b19-e7f6-4c79-92fd-e1ea5efc3ae4] succeeded in 106.14164369999708s: {'status': 'failed', 'goal_id': '913fef37a1184edca4316a7e629247a5', 'agent_id': '7d2214b418d64b269431c342d22f229a', 'connector_ids': [], 'workflow_mode': 'single_agent', 'priority': 'normal', 'dry_run': False, 'iterations': 0, 'result_scope': 'su
[worker-1] [2026-10-05 08:39:29,410: WARNING/ForkPoolWorker-17] 2026-10-05 08:39:29 [info     ] Running goal 913fef37a1184edca4316a7e629247a5 for tenant 49f1bbc940624e9d8215a6f8b50ce2c0
[worker-1] [2026-10-05 08:39:29,410: WARNING/ForkPoolWorker-17] 2026-10-05 08:39:29 [info     ] run_goal_queue_selected        goal_id=913fef37a1184edca4316a7e629247a5 plan=enterprise queue=goals.enterprise
[worker-1] [2026-10-05 08:39:29,557: WARNING/ForkPoolWorker-17] 2026-10-05 08:39:29 [info     ] worker_agent_config goal=913fef37a1184edca4316a7e629247a5 agent=7d2214b418d64b269431c342d22f229a mode=bounded-autonomous max_iter=8
[worker-1] [2026-10-05 08:39:29,768: WARNING/ForkPoolWorker-17] 2026-10-05 08:39:29 [info     ] Goal 913fef37a1184edca4316a7e629247a5 will run with AgentGraph (full capabilities)
```

### GOAL-STRATEGIES (failed)

`tests/real_world/test_goal_complex.py::test_goal_strategy_low_risk[supervisor]`

```
tests/real_world/test_goal_complex.py:166: in test_goal_strategy_low_risk
    assert final.get("status") == "complete", f"{strategy} goal ended {final.get('status')}: " \
E   AssertionError: supervisor goal ended failed: No structured result was produced.
E   assert 'failed' == 'complete'
E     
E     - complete
E     + failed
```

Docker log evidence:

```
[worker-1] [2026-10-05 08:41:31,535: WARNING/ForkPoolWorker-17] 2026-10-05 08:41:31 [info     ] goal_learning_recorded         feedback_count=0 goal_id=1d6ef462d53b476e949a94933e139ee1 lifecycle_state=active memory_id=16be48149e6b5a5f95de77e6faf2dd5d outcome=failed
[worker-1] [2026-10-05 08:41:31,543: INFO/ForkPoolWorker-17] Task app.scaling.tasks.run_goal[36825f0f-82d2-4480-840e-2f8e8c5d263e] succeeded in 6.965695275001053s: {'status': 'failed', 'goal_id': '1d6ef462d53b476e949a94933e139ee1', 'agent_id': '', 'connector_ids': [], 'workflow_mode': 'supervisor', 'priority': 'normal', 'dry_run': False, 'iterations': 0, 'result_scope': 'submitted_goal', 'submitted_goal_stat
[worker-1] [2026-10-05 08:41:24,684: WARNING/ForkPoolWorker-17] 2026-10-05 08:41:24 [info     ] Running goal 1d6ef462d53b476e949a94933e139ee1 for tenant 49f1bbc940624e9d8215a6f8b50ce2c0
[worker-1] [2026-10-05 08:41:24,684: WARNING/ForkPoolWorker-17] 2026-10-05 08:41:24 [info     ] run_goal_queue_selected        goal_id=1d6ef462d53b476e949a94933e139ee1 plan=enterprise queue=goals.enterprise
[worker-1] [2026-10-05 08:41:24,962: WARNING/ForkPoolWorker-17] 2026-10-05 08:41:24 [info     ] Goal 1d6ef462d53b476e949a94933e139ee1 will run with AgentGraph (full capabilities)
```

### GOAL-STRATEGIES (failed)

`tests/real_world/test_goal_complex.py::test_goal_strategy_low_risk[debate]`

```
tests/real_world/test_goal_complex.py:166: in test_goal_strategy_low_risk
    assert final.get("status") == "complete", f"{strategy} goal ended {final.get('status')}: " \
E   AssertionError: debate goal ended failed: No structured result was produced.
E   assert 'failed' == 'complete'
E     
E     - complete
E     + failed
```

Docker log evidence:

```
[worker-1] [2026-10-05 08:41:43,569: WARNING/ForkPoolWorker-18] 2026-10-05 08:41:43 [info     ] goal_learning_recorded         feedback_count=0 goal_id=9ccbba0b387f4ec683474cbd6b1b65a9 lifecycle_state=active memory_id=86a78fb664e45a6da19ec6cd193477c5 outcome=failed
[worker-1] [2026-10-05 08:41:43,576: INFO/ForkPoolWorker-18] Task app.scaling.tasks.run_goal[cf36bd1f-e5d6-4d7d-b1f2-7828e92d8334] succeeded in 9.379107253000257s: {'status': 'failed', 'goal_id': '9ccbba0b387f4ec683474cbd6b1b65a9', 'agent_id': '', 'connector_ids': [], 'workflow_mode': 'debate', 'priority': 'normal', 'dry_run': False, 'iterations': 0, 'result_scope': 'submitted_goal', 'submitted_goal_status':
[worker-1] [2026-10-05 08:41:34,198: WARNING/ForkPoolWorker-18] 2026-10-05 08:41:34 [info     ] Running goal 9ccbba0b387f4ec683474cbd6b1b65a9 for tenant 49f1bbc940624e9d8215a6f8b50ce2c0
[worker-1] [2026-10-05 08:41:34,198: WARNING/ForkPoolWorker-18] 2026-10-05 08:41:34 [info     ] run_goal_queue_selected        goal_id=9ccbba0b387f4ec683474cbd6b1b65a9 plan=enterprise queue=goals.enterprise
[worker-1] [2026-10-05 08:41:35,899: WARNING/ForkPoolWorker-18] 2026-10-05 08:41:35 [info     ] Goal 9ccbba0b387f4ec683474cbd6b1b65a9 will run with AgentGraph (full capabilities)
```

### GOAL-STRATEGIES (failed)

`tests/real_world/test_goal_complex.py::test_goal_strategy_low_risk[mixture_of_agents]`

```
tests/real_world/test_goal_complex.py:161: in test_goal_strategy_low_risk
    final = goals.wait_terminal(api, gid)
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/real_world/goals.py:41: in wait_terminal
    return dict(wait_until(lambda: get(api, goal_id), timeout=timeout, interval=5,
tests/real_world/helpers.py:163: in wait_until
    raise AssertionError(
E   AssertionError: timed out after 480s waiting for goal f4643652594847a38b785dd6ec4c8088 to finish; last={"goal_id": "f4643652594847a38b785dd6ec4c8088", "status": "executing", "goal": "Draft a five-item checklist a dispatcher should follow before releasing a cold-chain pharmaceutical consignment (temperature logging, seals, paperwork, handover, escalation). Use general operations knowledge only; no tools are needed.", "priority": "normal", "dry_run": false, "agent_id": null, "workflow_mode": "single_agent", "created_at": "2026-10-05T08:41:44.419817+00:00", "event_count": 3, "provider_warning": null, "result_artifact": {"version": 1, "kind": "empty", "title": "Draft a five-item checklist a dispatcher should follow before releasing a cold-chain pharmaceutical consignment (temperature logging, seals, paperwork, handover, escalation). Use general operations knowledge only; no tools are needed.",
```

Docker log evidence:

```
[worker-1] [2026-10-05 08:41:45,393: WARNING/ForkPoolWorker-19] 2026-10-05 08:41:45 [info     ] Running goal f4643652594847a38b785dd6ec4c8088 for tenant 49f1bbc940624e9d8215a6f8b50ce2c0
[worker-1] [2026-10-05 08:41:45,393: WARNING/ForkPoolWorker-19] 2026-10-05 08:41:45 [info     ] run_goal_queue_selected        goal_id=f4643652594847a38b785dd6ec4c8088 plan=enterprise queue=goals.enterprise
[worker-1] [2026-10-05 08:41:47,133: WARNING/ForkPoolWorker-19] 2026-10-05 08:41:47 [info     ] Goal f4643652594847a38b785dd6ec4c8088 will run with AgentGraph (full capabilities)
```

### GOAL-STRATEGIES-HIGH-RISK (failed)

`tests/real_world/test_goal_complex.py::test_goal_strategy_high_risk_needs_approval[mixture_of_agents]`

```
tests/real_world/test_goal_complex.py:221: in test_goal_strategy_high_risk_needs_approval
    final = goals.wait_terminal(api, gid)
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/real_world/goals.py:41: in wait_terminal
    return dict(wait_until(lambda: get(api, goal_id), timeout=timeout, interval=5,
tests/real_world/helpers.py:163: in wait_until
    raise AssertionError(
E   AssertionError: timed out after 480s waiting for goal 6567eaa602094a1d9b899fe517ca932b to finish; last={"goal_id": "6567eaa602094a1d9b899fe517ca932b", "status": "executing", "goal": "Demo list (in-memory sample data, no external systems): rec-201 env=staging last_used=2025-02-01; rec-202 env=production last_used=2026-09-30; rec-203 env=staging last_used=2025-03-09. Delete the stale staging records (last used before 2026) from the demo list and report which record IDs were deleted.", "priority": "normal", "dry_run": false, "agent_id": "ba77f0db18d1413ca8e45239d7600e5d", "workflow_mode": "single_agent", "created_at": "2026-10-05T08:50:42.401592+00:00", "event_count": 7, "provider_warning": null, "result_artifact": {"version": 1, "kind": "empty", "title": "Demo list (in-memory sample data, no external systems): rec-201 env=staging last_used=2025-02-01; rec-202 env=production last_used=2026-09-
```

Docker log evidence:

```
[worker-1] [2026-10-05 08:50:46,649: WARNING/ForkPoolWorker-19] 2026-10-05 08:50:46 [error    ] Goal 6567eaa602094a1d9b899fe517ca932b failed: 'dict' object has no attribute 'status'
[worker-1] [2026-10-05 08:50:46,650: WARNING/ForkPoolWorker-19] 2026-10-05 08:50:46 [warning  ] goal_transient_failure_will_retry goal_id=6567eaa602094a1d9b899fe517ca932b attempt=1/3 error='dict' object has no attribute 'status'
[backend-1] 2026-10-05T08:50:46.530586Z [info     ] hitl_rejection_note_stored     goal_id=6567eaa602094a1d9b899fe517ca932b
[worker-1] [2026-10-05 08:50:42,437: WARNING/ForkPoolWorker-19] 2026-10-05 08:50:42 [info     ] Running goal 6567eaa602094a1d9b899fe517ca932b for tenant 49f1bbc940624e9d8215a6f8b50ce2c0
[worker-1] [2026-10-05 08:50:42,437: WARNING/ForkPoolWorker-19] 2026-10-05 08:50:42 [info     ] run_goal_queue_selected        goal_id=6567eaa602094a1d9b899fe517ca932b plan=enterprise queue=goals.enterprise
[worker-1] [2026-10-05 08:50:42,598: WARNING/ForkPoolWorker-19] 2026-10-05 08:50:42 [info     ] worker_agent_config goal=6567eaa602094a1d9b899fe517ca932b agent=ba77f0db18d1413ca8e45239d7600e5d mode=supervised max_iter=8
[worker-1] [2026-10-05 08:50:42,821: WARNING/ForkPoolWorker-19] 2026-10-05 08:50:42 [info     ] Goal 6567eaa602094a1d9b899fe517ca932b will run with AgentGraph (full capabilities)
[worker-1] [2026-10-05 08:50:48,010: WARNING/ForkPoolWorker-26] 2026-10-05 08:50:48 [info     ] Running goal 6567eaa602094a1d9b899fe517ca932b for tenant 49f1bbc940624e9d8215a6f8b50ce2c0
```

### GOAL-HIGH-RISK-APPROVE (failed)

`tests/real_world/test_goal_high_risk.py::test_goal_high_risk_approve`

```
tests/real_world/test_goal_high_risk.py:112: in test_goal_high_risk_approve
    assert goal.get("status") == "complete", f"goal ended {goal.get('status')}: {answer[:300]}"
E   AssertionError: goal ended failed: The condition for a stale staging record is: env equals 'staging' and last_used date is before 2026 [UNGROUNDED CLAIM — not found in tool outputs]-01 [UNGROUNDED CLAIM — not found in tool outputs]-01 [UNGROUNDED CLAIM — not found in tool outputs].
E   assert 'failed' == 'complete'
E     
E     - complete
E     + failed
```

Docker log evidence:

```
[worker-1] [2026-10-05 09:05:23,904: INFO/ForkPoolWorker-30] Task app.scaling.tasks.run_goal[897933dc-d6ea-4e8e-a744-9c68e738bfc8] succeeded in 396.1089448229977s: {'status': 'failed', 'goal_id': '8ea5660a34b7495bab11caf716ea7c13', 'reason': 'PermissionError: Step \'Step 3: Iterate over the list, collect IDs that meet the condition into a \'removed\' list and those that do not into a \'remaining\' list.\' ap
[worker-1] [2026-10-05 08:58:49,906: WARNING/ForkPoolWorker-30] 2026-10-05 08:58:49 [info     ] Running goal 8ea5660a34b7495bab11caf716ea7c13 for tenant 49f1bbc940624e9d8215a6f8b50ce2c0
[worker-1] [2026-10-05 08:58:49,906: WARNING/ForkPoolWorker-30] 2026-10-05 08:58:49 [info     ] run_goal_queue_selected        goal_id=8ea5660a34b7495bab11caf716ea7c13 plan=enterprise queue=goals.enterprise
[worker-1] [2026-10-05 08:58:50,366: WARNING/ForkPoolWorker-30] 2026-10-05 08:58:50 [info     ] worker_agent_config goal=8ea5660a34b7495bab11caf716ea7c13 agent=736b3f33fa754ecba9449dee65e687b2 mode=supervised max_iter=8
[worker-1] [2026-10-05 08:58:51,735: WARNING/ForkPoolWorker-30] 2026-10-05 08:58:51 [info     ] Goal 8ea5660a34b7495bab11caf716ea7c13 will run with AgentGraph (full capabilities)
[worker-1] [2026-10-05 09:05:23,827: WARNING/ForkPoolWorker-30] 2026-10-05 09:05:23 [info     ] goal_denied_by_governance goal_id=8ea5660a34b7495bab11caf716ea7c13: Step 'Step 3: Iterate over the list, collect IDs that meet the condition into a 'removed' list and those that do not into a 'remaining' list.' approval timed out.
```

### GOV-PII-GUARDRAIL (failed)

`tests/real_world/test_gov_guardrails.py::test_output_pii_guardrail`

```
tests/real_world/test_gov_guardrails.py:69: in test_output_pii_guardrail
    assert not leaked, "the goal's answer returned the personal email/phone unredacted"
E   AssertionError: the goal's answer returned the personal email/phone unredacted
E   assert not True
```

Docker log evidence:

```
[worker-1] [2026-10-05 09:08:00,681: INFO/ForkPoolWorker-31] Task app.scaling.tasks.run_goal[6695758c-ac46-478e-8faf-17929fca99db] succeeded in 13.785166271998605s: {'status': 'failed', 'goal_id': '86f00b0ac5aa4c15aca28d6728c04995', 'agent_id': '', 'connector_ids': [], 'workflow_mode': 'single_agent', 'priority': 'normal', 'dry_run': False, 'iterations': 0, 'result_scope': 'submitted_goal', 'submitted_goal_s
[worker-1] [2026-10-05 09:07:46,896: WARNING/ForkPoolWorker-31] 2026-10-05 09:07:46 [info     ] Running goal 86f00b0ac5aa4c15aca28d6728c04995 for tenant 49f1bbc940624e9d8215a6f8b50ce2c0
[worker-1] [2026-10-05 09:07:46,896: WARNING/ForkPoolWorker-31] 2026-10-05 09:07:46 [info     ] run_goal_queue_selected        goal_id=86f00b0ac5aa4c15aca28d6728c04995 plan=enterprise queue=goals.enterprise
[worker-1] [2026-10-05 09:07:47,227: WARNING/ForkPoolWorker-31] 2026-10-05 09:07:47 [info     ] Goal 86f00b0ac5aa4c15aca28d6728c04995 will run with AgentGraph (full capabilities)
[worker-1] [2026-10-05 09:08:00,670: WARNING/ForkPoolWorker-31] 2026-10-05 09:08:00 [info     ] memory_write_blocked           goal_id=86f00b0ac5aa4c15aca28d6728c04995 reason=guardrail store=canonical tenant_id=49f1bbc940624e9d8215a6f8b50ce2c0
[worker-1] [2026-10-05 09:08:00,670: WARNING/ForkPoolWorker-31] 2026-10-05 09:08:00 [info     ] goal_learning_blocked_by_guardrail goal_id=86f00b0ac5aa4c15aca28d6728c04995
```

### GOV-GRANT-DENY (failed)

`tests/real_world/test_gov_guardrails.py::test_grant_denies_tool`

```
tests/real_world/test_gov_guardrails.py:108: in test_grant_denies_tool
    assert "tool_call_blocked_by_grant" in types, (
E   AssertionError: no tool_call_blocked_by_grant event (events: ['chunking_strategy_selected', 'execution_strategy_resolved', 'goal_created', 'goal_failed', 'goal_started', 'guardrail_profile_selected', 'knowledge_retrieved', 'model_route_selected', 'plan_ready', 'rag_strategy_selected', 'step_complete', 'step_started', 'tool_call_complete', 'verification_done', 'worker_complete', 'worker_started'])
E   assert 'tool_call_blocked_by_grant' in ['goal_created', 'worker_started', 'goal_started', 'chunking_strategy_selected', 'knowledge_retrieved', 'rag_strategy_selected', ...]
```

Docker log evidence:

```
[worker-1] [2026-10-05 09:09:07,476: WARNING/ForkPoolWorker-31] 2026-10-05 09:09:07 [info     ] goal_learning_recorded         feedback_count=0 goal_id=8efdfefc74184db7a6730f96d641e1b3 lifecycle_state=active memory_id=fdcaed007f295438937eb48386b9dfd7 outcome=failed
[worker-1] [2026-10-05 09:09:07,500: INFO/ForkPoolWorker-31] Task app.scaling.tasks.run_goal[e3937b96-f794-4fa3-ba5b-f1ae03ffd351] succeeded in 58.46787488500195s: {'status': 'failed', 'goal_id': '8efdfefc74184db7a6730f96d641e1b3', 'agent_id': '86fde66cf30848c6a8c1b661c4b8280f', 'connector_ids': [], 'workflow_mode': 'single_agent', 'priority': 'normal', 'dry_run': False, 'iterations': 0, 'result_scope': 'sub
[worker-1] [2026-10-05 09:08:09,032: WARNING/ForkPoolWorker-31] 2026-10-05 09:08:09 [info     ] Running goal 8efdfefc74184db7a6730f96d641e1b3 for tenant 49f1bbc940624e9d8215a6f8b50ce2c0
[worker-1] [2026-10-05 09:08:09,032: WARNING/ForkPoolWorker-31] 2026-10-05 09:08:09 [info     ] run_goal_queue_selected        goal_id=8efdfefc74184db7a6730f96d641e1b3 plan=enterprise queue=goals.enterprise
[worker-1] [2026-10-05 09:08:09,174: WARNING/ForkPoolWorker-31] 2026-10-05 09:08:09 [info     ] worker_agent_config goal=8efdfefc74184db7a6730f96d641e1b3 agent=86fde66cf30848c6a8c1b661c4b8280f mode=bounded-autonomous max_iter=8
[worker-1] [2026-10-05 09:08:09,351: WARNING/ForkPoolWorker-31] 2026-10-05 09:08:09 [info     ] Goal 8efdfefc74184db7a6730f96d641e1b3 will run with AgentGraph (full capabilities)
```

### GOV-POLICY-APPROVAL (failed)

`tests/real_world/test_gov_guardrails.py::test_policy_requires_approval`

```
tests/real_world/test_gov_guardrails.py:148: in test_policy_requires_approval
    assert "web_search" in mask(pending[0]), f"the approval is not for web_search: " \
E   AssertionError: the approval is not for web_search: {"request_id": "b060e914e28e43b8be93a50d38883e9d", "goal_id": "6477961c85d24b35b246bcdb06bfe8f5", "action": "Step 1: Perform a web search for the current repo rate set by the Reserve Bank of India.", 
E   assert 'web_search' in '{"request_id": "b060e914e28e43b8be93a50d38883e9d", "goal_id": "6477961c85d24b35b246bcdb06bfe8f5", "action": "Step 1: ...": "2026-10-05T09:10:02.898270+00:00", "note": "", "approver": null, "required_approvers": 1, "approvals_received": 0}'
E    +  where '{"request_id": "b060e914e28e43b8be93a50d38883e9d", "goal_id": "6477961c85d24b35b246bcdb06bfe8f5", "action": "Step 1: ...": "2026-10-05T09:10:02.898270+00:00", "note": "", "approver": null, "required_approvers": 1, "approvals_received": 0}' = mask({'request_id': 'b060e914e28e43b8be93a50d38883e9d', 'goal_id': '6477961c85d24b35b246bcdb06bfe8f5', 'action': 'Step 1: Perform a web search for the current repo rate set by the Reserve Bank of India.', 'risk_level': 'high', ...})
```

Docker log evidence:

```
[worker-1] [2026-10-05 09:09:18,797: WARNING/ForkPoolWorker-30] 2026-10-05 09:09:18 [info     ] Running goal 6477961c85d24b35b246bcdb06bfe8f5 for tenant 49f1bbc940624e9d8215a6f8b50ce2c0
[worker-1] [2026-10-05 09:09:18,797: WARNING/ForkPoolWorker-30] 2026-10-05 09:09:18 [info     ] run_goal_queue_selected        goal_id=6477961c85d24b35b246bcdb06bfe8f5 plan=enterprise queue=goals.enterprise
[worker-1] [2026-10-05 09:09:18,950: WARNING/ForkPoolWorker-30] 2026-10-05 09:09:18 [info     ] worker_agent_config goal=6477961c85d24b35b246bcdb06bfe8f5 agent=9b825ae955304ff282e1d8a708048d48 mode=supervised max_iter=8
[worker-1] [2026-10-05 09:09:19,161: WARNING/ForkPoolWorker-30] 2026-10-05 09:09:19 [info     ] Goal 6477961c85d24b35b246bcdb06bfe8f5 will run with AgentGraph (full capabilities)
```

### GOV-BUDGET-CAP (failed)

`tests/real_world/test_gov_guardrails.py::test_budget_cap_stops_goal`

```
tests/real_world/test_gov_guardrails.py:166: in test_budget_cap_stops_goal
    cap = api.put("/governance/budget", json={
          ^^^^^^^
E   AttributeError: 'LiveAPI' object has no attribute 'put'
```

### KB-COMPLEX-CORPUS (failed)

`tests/real_world/test_kb_complex.py::test_kb_complex_format[scan_pdf]`

```
tests/real_world/test_kb_complex.py:69: in test_kb_complex_format
    assert up["http"] in (200, 201), (
E   AssertionError: scan_pdf upload of delivery-note-dn-58213-scan.pdf refused: HTTP 422 {"detail": "delivery-note-dn-58213-scan.pdf: the PDF has no extractable text (scanned images need OCR)"}
E   assert 422 in (200, 201)
```

Docker log evidence:

```
[worker-1] [2026-10-05 08:59:04,811: WARNING/ForkPoolWorker-30] 2026-10-05 08:59:04 [debug    ] rrf_retrieval_complete         bm25_hits=9 candidates=18 collection_id=57dd6fed54bb4db7ac8b459ea0fbfe22 fts_hits=0 mode=hybrid top_k=3 trgm_hits=0 vector_hits=9
[worker-1] [2026-10-05 08:59:04,821: WARNING/ForkPoolWorker-30] 2026-10-05 08:59:04 [debug    ] rrf_retrieval_complete         bm25_hits=9 candidates=18 collection_id=57dd6fed54bb4db7ac8b459ea0fbfe22 fts_hits=0 mode=hybrid top_k=3 trgm_hits=0 vector_hits=9
[worker-1] [2026-10-05 08:59:04,863: WARNING/ForkPoolWorker-30] 2026-10-05 08:59:04 [debug    ] rrf_retrieval_complete         bm25_hits=9 candidates=18 collection_id=57dd6fed54bb4db7ac8b459ea0fbfe22 fts_hits=0 mode=hybrid top_k=3 trgm_hits=0 vector_hits=9
[worker-1] [2026-10-05 09:06:29,508: WARNING/ForkPoolWorker-31] 2026-10-05 09:06:29 [debug    ] rrf_retrieval_complete         bm25_hits=9 candidates=18 collection_id=57dd6fed54bb4db7ac8b459ea0fbfe22 fts_hits=0 mode=hybrid top_k=3 trgm_hits=0 vector_hits=9
[worker-1] [2026-10-05 09:06:29,519: WARNING/ForkPoolWorker-31] 2026-10-05 09:06:29 [debug    ] rrf_retrieval_complete         bm25_hits=9 candidates=18 collection_id=57dd6fed54bb4db7ac8b459ea0fbfe22 fts_hits=0 mode=hybrid top_k=3 trgm_hits=0 vector_hits=9
[worker-1] [2026-10-05 09:06:29,562: WARNING/ForkPoolWorker-31] 2026-10-05 09:06:29 [debug    ] rrf_retrieval_complete         bm25_hits=9 candidates=18 collection_id=57dd6fed54bb4db7ac8b459ea0fbfe22 fts_hits=0 mode=hybrid top_k=3 trgm_hits=0 vector_hits=9
[worker-1] [2026-10-05 09:09:29,409: WARNING/ForkPoolWorker-30] 2026-10-05 09:09:29 [debug    ] rrf_retrieval_complete         bm25_hits=9 candidates=18 collection_id=57dd6fed54bb4db7ac8b459ea0fbfe22 fts_hits=0 mode=hybrid top_k=3 trgm_hits=0 vector_hits=9
```

### KB-COMPLEX-CORPUS (failed)

`tests/real_world/test_kb_complex.py::test_kb_complex_format[zip]`

```
tests/real_world/test_kb_complex.py:69: in test_kb_complex_format
    assert up["http"] in (200, 201), (
E   AssertionError: zip upload of people-ops-bundle.zip refused: HTTP 415 {"detail": "people-ops-bundle.zip: unsupported binary file; upload PDF, DOCX, XLSX or text"}
E   assert 415 in (200, 201)
```

Docker log evidence:

```
[worker-1] [2026-10-05 08:59:04,811: WARNING/ForkPoolWorker-30] 2026-10-05 08:59:04 [debug    ] rrf_retrieval_complete         bm25_hits=9 candidates=18 collection_id=57dd6fed54bb4db7ac8b459ea0fbfe22 fts_hits=0 mode=hybrid top_k=3 trgm_hits=0 vector_hits=9
[worker-1] [2026-10-05 08:59:04,821: WARNING/ForkPoolWorker-30] 2026-10-05 08:59:04 [debug    ] rrf_retrieval_complete         bm25_hits=9 candidates=18 collection_id=57dd6fed54bb4db7ac8b459ea0fbfe22 fts_hits=0 mode=hybrid top_k=3 trgm_hits=0 vector_hits=9
[worker-1] [2026-10-05 08:59:04,863: WARNING/ForkPoolWorker-30] 2026-10-05 08:59:04 [debug    ] rrf_retrieval_complete         bm25_hits=9 candidates=18 collection_id=57dd6fed54bb4db7ac8b459ea0fbfe22 fts_hits=0 mode=hybrid top_k=3 trgm_hits=0 vector_hits=9
[worker-1] [2026-10-05 09:06:29,508: WARNING/ForkPoolWorker-31] 2026-10-05 09:06:29 [debug    ] rrf_retrieval_complete         bm25_hits=9 candidates=18 collection_id=57dd6fed54bb4db7ac8b459ea0fbfe22 fts_hits=0 mode=hybrid top_k=3 trgm_hits=0 vector_hits=9
[worker-1] [2026-10-05 09:06:29,519: WARNING/ForkPoolWorker-31] 2026-10-05 09:06:29 [debug    ] rrf_retrieval_complete         bm25_hits=9 candidates=18 collection_id=57dd6fed54bb4db7ac8b459ea0fbfe22 fts_hits=0 mode=hybrid top_k=3 trgm_hits=0 vector_hits=9
[worker-1] [2026-10-05 09:06:29,562: WARNING/ForkPoolWorker-31] 2026-10-05 09:06:29 [debug    ] rrf_retrieval_complete         bm25_hits=9 candidates=18 collection_id=57dd6fed54bb4db7ac8b459ea0fbfe22 fts_hits=0 mode=hybrid top_k=3 trgm_hits=0 vector_hits=9
[worker-1] [2026-10-05 09:09:29,409: WARNING/ForkPoolWorker-30] 2026-10-05 09:09:29 [debug    ] rrf_retrieval_complete         bm25_hits=9 candidates=18 collection_id=57dd6fed54bb4db7ac8b459ea0fbfe22 fts_hits=0 mode=hybrid top_k=3 trgm_hits=0 vector_hits=9
```

### KB-COMPLEX-LIFECYCLE (failed)

`tests/real_world/test_kb_complex.py::test_kb_dedup_update_delete`

```
tests/real_world/test_kb_complex.py:268: in test_kb_dedup_update_delete
    assert not soft, "; ".join(soft)
E   AssertionError: the superseded clause (75 days) is still served after the edit: the re-upload added a second copy instead of replacing the document; 2 documents listed for bramblewood-vendor-master-agreement.docx after the edit (want 1); chunk count grew from 9 to 17 on an edit that changed one clause
E   assert not ['the superseded clause (75 days) is still served after the edit: the re-upload added a second copy instead of replaci...dor-master-agreement.docx after the edit (want 1)', 'chunk count grew from 9 to 17 on an edit that changed one clause']
```

### KB-STRATEGIES (failed)

`tests/real_world/test_kb_retrieval.py::test_kb_strategies`

```
10 failed queries (pdf-cold-chain: /rag/query(adaptive) -> 503 {"detail": "Answer synthesis is unavailable"}); modular: 10 failed queries (pdf-cold-chain: /rag/query(modular) -> 503 {"detail": "Retrieval service is unavailable"}); speculative: 10 failed queries (pdf-cold-chain: /rag/query(speculative) -> 503 {"detail": "Retrieval service is unavailable"}); agentic: 10 failed queries (pdf-cold-chain: /rag/query(agentic) -> 503 {"detail": "Retrieval service is unavailable"}); web_augmented: 7 failed queries (pdf-cold-chain: /rag/query(web_augmented) -> 503 {"detail": "Answer synthesis is unavailable"}); self_rag: 5 failed queries (pdf-hazmat: /rag/query(self_rag) -> 503 {"detail": "Retrieval service is unavailable"}); flare: 7 failed queries (pdf-cold-chain: /rag/query(flare) -> 503 {"detail": "Retrieval service is unavailable"}); raptor: 10 failed queries (pdf-cold-chain: /rag/query(raptor) -> 503 {"detail": "RAG strategy is unavailable: raptor (requires RAPTOR indexing)"}); agentic_chunking: 10 failed queries (pdf-cold-chain: /rag/query(agentic_chunking) -> 503 {"detail": "RAG strategy is unavailable: agentic_chunking (requires agentic-chunking indexing)"}); colbert: 10 failed queries (pdf-cold-chain: /rag/query(colbert) -> 503 {"detail": "Answer synthesis is unavailable"}); memory_augmented: 10 failed queries (pdf-cold-chain: /rag/query(memory_augmented) -> 503 {"detail": "Answer synthesis is unavailable"}); code: 10 failed queries (pdf-cold-chain: /rag/query(code) -> 503 {"detail": "Answer synthesis is unavailable"})
E   assert not ['naive: 1 failed queries (pdf-cold-chain: /rag/query(naive) -> 503 {"detail": "Answer synthesis is unavailable"})', '...e: 10 failed queries (pdf-cold-chain: /rag/query(adaptive) -> 503 {"detail": "Answer synthesis is unavailable"})', ...]
```

### KB-REAL-DOCS (failed)

`tests/real_world/test_knowledge.py::test_kb_real_documents`

```
tests/real_world/test_knowledge.py:162: in test_kb_real_documents
    assert final.get("status") == "complete", f"goal ended {final.get('status')}: {answer[:300]}"
E   AssertionError: goal ended failed: No structured result was produced.
E   assert 'failed' == 'complete'
E     
E     - complete
E     + failed
```

Docker log evidence:

```
[worker-1] [2026-10-05 09:32:38,836: INFO/ForkPoolWorker-47] Task app.scaling.tasks.run_goal[a2ffce77-48b0-486a-9b12-d3c6bb047e02] succeeded in 18.401494519999687s: {'status': 'failed', 'goal_id': '28554aa1c17c45a1828248d3f8681c65', 'reason': 'PermissionError: Step \'Step 1: The Project Halcyon database migration window is Saturday 14 November 2026, 02:00-05:00 IST (source: 244c9d5e...\' requires human appro
[worker-1] [2026-10-05 09:32:20,547: WARNING/ForkPoolWorker-47] 2026-10-05 09:32:20 [info     ] Running goal 28554aa1c17c45a1828248d3f8681c65 for tenant 49f1bbc940624e9d8215a6f8b50ce2c0
[worker-1] [2026-10-05 09:32:20,547: WARNING/ForkPoolWorker-47] 2026-10-05 09:32:20 [info     ] run_goal_queue_selected        goal_id=28554aa1c17c45a1828248d3f8681c65 plan=enterprise queue=goals.enterprise
[worker-1] [2026-10-05 09:32:20,899: WARNING/ForkPoolWorker-47] 2026-10-05 09:32:20 [info     ] worker_agent_config goal=28554aa1c17c45a1828248d3f8681c65 agent=3d636a80fe934bc79c0082d2bfec56f0 mode=bounded-autonomous max_iter=6
[worker-1] [2026-10-05 09:32:21,643: WARNING/ForkPoolWorker-47] 2026-10-05 09:32:21 [info     ] Goal 28554aa1c17c45a1828248d3f8681c65 will run with AgentGraph (full capabilities)
[worker-1] [2026-10-05 09:32:22,945: WARNING/ForkPoolWorker-47] 2026-10-05 09:32:22 [debug    ] rrf_retrieval_complete         bm25_hits=6 candidates=7 collection_id=244c9d5e35244c88b3ed9c7a983a5e71 fts_hits=0 mode=hybrid top_k=3 trgm_hits=0 vector_hits=7
[worker-1] [2026-10-05 09:32:34,600: WARNING/ForkPoolWorker-47] 2026-10-05 09:32:34 [debug    ] rrf_retrieval_complete         bm25_hits=5 candidates=7 collection_id=244c9d5e35244c88b3ed9c7a983a5e71 fts_hits=0 mode=hybrid top_k=3 trgm_hits=1 vector_hits=7
[worker-1] [2026-10-05 09:32:38,784: WARNING/ForkPoolWorker-47] 2026-10-05 09:32:38 [info     ] goal_denied_by_governance goal_id=28554aa1c17c45a1828248d3f8681c65: Step 'Step 1: The Project Halcyon database migration window is Saturday 14 November 2026, 02:00-05:00 IST (source: 244c9d5e...' requires human approval (high-risk step: change to a sensitive target (data); step of a high-risk goal (financial actio
```

### KB-REEMBED (failed)

`tests/real_world/test_knowledge.py::test_kb_reembed_keeps_search_correct`

```
tests/real_world/test_knowledge.py:255: in test_kb_reembed_keeps_search_correct
    assert not misses, f"search lost documents after re-embedding: {misses}"
E   AssertionError: search lost documents after re-embedding: ['pep-0020 (url)']
E   assert not ['pep-0020 (url)']
```

Docker log evidence:

```
[worker-1] [2026-10-05 09:32:22,945: WARNING/ForkPoolWorker-47] 2026-10-05 09:32:22 [debug    ] rrf_retrieval_complete         bm25_hits=6 candidates=7 collection_id=244c9d5e35244c88b3ed9c7a983a5e71 fts_hits=0 mode=hybrid top_k=3 trgm_hits=0 vector_hits=7
[worker-1] [2026-10-05 09:32:34,600: WARNING/ForkPoolWorker-47] 2026-10-05 09:32:34 [debug    ] rrf_retrieval_complete         bm25_hits=5 candidates=7 collection_id=244c9d5e35244c88b3ed9c7a983a5e71 fts_hits=0 mode=hybrid top_k=3 trgm_hits=1 vector_hits=7
[worker-1] [2026-10-05 09:32:44,224: INFO/ForkPoolWorker-47] Task app.scaling.tasks.re_embed_collection[99605d34-a9c8-4b6e-93a2-1f7ccd6bf691] succeeded in 1.0047499199972663s: {'collection_id': '244c9d5e35244c88b3ed9c7a983a5e71', 're_embedded': 8, 'model': 'dedicated/nvidia/nemotron-3-embed-1b', 'dimension': 2048, 'previous_dimension': 2048, 'job_id': '46aa710280f1447eb585def3b1c7b944'}
```

### SCHED-FIRES-GOAL (failed)

`tests/real_world/test_sched_realistic.py::test_schedule_fires_goal`

```
tests/real_world/test_sched_realistic.py:189: in test_schedule_fires_goal
    hist = wait_until(
tests/real_world/helpers.py:163: in wait_until
    raise AssertionError(
E   AssertionError: timed out after 240s waiting for the schedule to fire a goal; last={"runs": [{"run_id": "ef8d132f-7240-476d-95ae-40036394cab8", "goal_id": "7ae2e60ab1bf412cbb7d9edc4a6a090a", "status": "executing", "skip_reason": null, "started_at": "2026-10-05T09:38:42.486717", "duration_ms": null, "error": null}, {"run_id": "5197db8f-69e9-4109-84b0-b72cd72cbaff", "goal_id": "fa87de33a5c84387a99b35d09139135d", "status": "success", "skip_reason": null, "started_at": "2026-10-05T09:37:00.604644", "duration_ms": 22853, "error": null}, {"run_id": "2dc56370-90fc-4664-8955-5556bb8dcf25", "goal_id": "c1f45a73c01345afbb974f35f4c889d4", "status": "success", "skip_reason": null, "started_at": "2026-10-05T09:35:45.176183", "duration_ms": 30002, "error": null}], "total": 3, "schedule_id": "5cee448255414a0cad3b60a81ee4356d"}
```

Docker log evidence:

```
[worker-1] [2026-10-05 09:35:42,550: WARNING/ForkPoolWorker-48] 2026-10-05 09:35:42 [info     ] Fired interval schedule schedule:49f1bbc940624e9d8215a6f8b50ce2c0:5cee448255414a0cad3b60a81ee4356d for tenant 49f1bbc940624e9d8215a6f8b50ce2c0
[worker-1] [2026-10-05 09:35:44,164: WARNING/ForkPoolWorker-48] 2026-10-05 09:35:44 [info     ] Firing schedule schedule:49f1bbc940624e9d8215a6f8b50ce2c0:5cee448255414a0cad3b60a81ee4356d for tenant 49f1bbc940624e9d8215a6f8b50ce2c0
[worker-1] [2026-10-05 09:35:45,180: INFO/ForkPoolWorker-48] trigger_fired trigger_id=5cee448255414a0cad3b60a81ee4356d type=interval goal_id=c1f45a73c01345afbb974f35f4c889d4 ms=987
[worker-1] [2026-10-05 09:35:45,181: INFO/ForkPoolWorker-48] Task app.scaling.tasks.run_scheduled_goal[f2d47a7d-b1c0-4923-95cb-fed085ccdf3d] succeeded in 1.0181110490011633s: {'status': 'dispatched', 'schedule_id': 'schedule:49f1bbc940624e9d8215a6f8b50ce2c0:5cee448255414a0cad3b60a81ee4356d', 'goal_id': 'c1f45a73c01345afbb974f35f4c889d4', 'skip_reason': None}
[worker-1] [2026-10-05 09:37:00,526: WARNING/ForkPoolWorker-48] 2026-10-05 09:37:00 [info     ] Fired interval schedule schedule:49f1bbc940624e9d8215a6f8b50ce2c0:5cee448255414a0cad3b60a81ee4356d for tenant 49f1bbc940624e9d8215a6f8b50ce2c0
[worker-1] [2026-10-05 09:37:00,573: WARNING/ForkPoolWorker-48] 2026-10-05 09:37:00 [info     ] Firing schedule schedule:49f1bbc940624e9d8215a6f8b50ce2c0:5cee448255414a0cad3b60a81ee4356d for tenant 49f1bbc940624e9d8215a6f8b50ce2c0
[worker-1] [2026-10-05 09:37:00,607: INFO/ForkPoolWorker-48] trigger_fired trigger_id=5cee448255414a0cad3b60a81ee4356d type=interval goal_id=fa87de33a5c84387a99b35d09139135d ms=18
[worker-1] [2026-10-05 09:37:00,608: INFO/ForkPoolWorker-48] Task app.scaling.tasks.run_scheduled_goal[37902f70-e0ea-4f8a-974c-16486a473cb6] succeeded in 0.03550331999940681s: {'status': 'dispatched', 'schedule_id': 'schedule:49f1bbc940624e9d8215a6f8b50ce2c0:5cee448255414a0cad3b60a81ee4356d', 'goal_id': 'fa87de33a5c84387a99b35d09139135d', 'skip_reason': None}
```

### TRIGGER-CHAIN (failed)

`tests/real_world/test_trigger_chain.py::test_signed_webhook_chains_two_workflows`

```
tests/real_world/test_trigger_chain.py:124: in test_signed_webhook_chains_two_workflows
    assert finished.get("status") == "complete", (
E   AssertionError: the producer's completion event did not resume the consumer: failed code step 'book_dispatch': execution could not be audited
E   assert 'failed' == 'complete'
E     
E     - complete
E     + failed
```

Docker log evidence:

```
[workflow-worker-1] [2026-10-05 09:41:42,427: WARNING/ForkPoolWorker-57] 2026-10-05T09:41:42.427699Z [warning  ] audit_write_retry              attempt=1 error="(sqlalchemy.dialects.postgresql.asyncpg.Error) <class 'asyncpg.exceptions.StringDataRightTruncationError'>: value too long for type character varying(32)\n[SQL: \n    INSERT INTO audit_log (\n        id, tenant_id, goal_id, tool_name, action_level, outcome, s
[workflow-worker-1] [2026-10-05 09:41:42,634: WARNING/ForkPoolWorker-57] 2026-10-05T09:41:42.633948Z [warning  ] audit_write_retry              attempt=2 error="(sqlalchemy.dialects.postgresql.asyncpg.Error) <class 'asyncpg.exceptions.StringDataRightTruncationError'>: value too long for type character varying(32)\n[SQL: \n    INSERT INTO audit_log (\n        id, tenant_id, goal_id, tool_name, action_level, outcome, s
[workflow-worker-1] [2026-10-05 09:41:43,039: WARNING/ForkPoolWorker-57] 2026-10-05T09:41:43.039114Z [warning  ] audit_write_retry              attempt=3 error="(sqlalchemy.dialects.postgresql.asyncpg.Error) <class 'asyncpg.exceptions.StringDataRightTruncationError'>: value too long for type character varying(32)\n[SQL: \n    INSERT INTO audit_log (\n        id, tenant_id, goal_id, tool_name, action_level, outcome, s
[workflow-worker-1] [2026-10-05 09:41:43,047: WARNING/ForkPoolWorker-57] 2026-10-05T09:41:43.043485Z [error    ] workflow_run_failed_worker     error='RuntimeError("code step \'book_dispatch\': execution could not be audited")' run_id=8e716bd5-0202-420b-ae94-3aa63bbdfa34 span_id=966ac596691a436a trace_id=dcf429047b12cc6a5dc34e877ba7c250
[backend-1] 2026-10-05T09:41:20.673976Z [info     ] workflow.published             tenant_id=49f1bbc940624e9d8215a6f8b50ce2c0 workflow_id=52f9fb4d-9297-4ae8-8287-5c5fbe72f5ba
[backend-1] 2026-10-05T09:41:20.700556Z [info     ] workflow_webhook_fired         run_id=e6fb9e83-e20d-4326-999a-7e333c82ad6c workflow_id=52f9fb4d-9297-4ae8-8287-5c5fbe72f5ba
[backend-1] 2026-10-05T09:41:20.716435Z [info     ] workflow_run_idempotent_replay existing_run_id=e6fb9e83-e20d-4326-999a-7e333c82ad6c workflow_id=52f9fb4d-9297-4ae8-8287-5c5fbe72f5ba
[backend-1] 2026-10-05T09:41:20.716511Z [info     ] workflow_webhook_fired         run_id=e6fb9e83-e20d-4326-999a-7e333c82ad6c workflow_id=52f9fb4d-9297-4ae8-8287-5c5fbe72f5ba
```

### WF-PUBLISH-APPROVAL (failed)

`tests/real_world/test_workflow_publish.py::test_workflow_publish_requires_second_approver`

```
tests/real_world/test_workflow_publish.py:78: in test_workflow_publish_requires_second_approver
    assert expected in names, f"missing audit row {expected}: {names}"
E   AssertionError: missing audit row workflow.created: ['workflow.publish_approved', 'workflow.publish_submitted', 'workflow.published', 'workflow.updated']
E   assert 'workflow.created' in ['workflow.publish_approved', 'workflow.publish_submitted', 'workflow.published', 'workflow.updated']
```

Docker log evidence:

```
[backend-1] 2026-10-05T09:43:30.264352Z [info     ] workflow.publish_approval_required_set required=True tenant_id=49f1bbc940624e9d8215a6f8b50ce2c0 workflow_id=d51a2f52-1034-4154-b918-47d8a4b1c903
[backend-1] 2026-10-05T09:43:30.295758Z [info     ] workflow.publish_submitted     submitted_by=3bb23fc307f0455eb79ff490ad33a8d1 tenant_id=49f1bbc940624e9d8215a6f8b50ce2c0 workflow_id=d51a2f52-1034-4154-b918-47d8a4b1c903
[backend-1] 2026-10-05T09:43:30.365736Z [info     ] workflow.published             tenant_id=49f1bbc940624e9d8215a6f8b50ce2c0 workflow_id=d51a2f52-1034-4154-b918-47d8a4b1c903
[backend-1] 2026-10-05T09:43:30.365792Z [info     ] workflow.publish_approved      approver=fe2b724b83cb4b58869a68cb41c120b5 tenant_id=49f1bbc940624e9d8215a6f8b50ce2c0 workflow_id=d51a2f52-1034-4154-b918-47d8a4b1c903
```

