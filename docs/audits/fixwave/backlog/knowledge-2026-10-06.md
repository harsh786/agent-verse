# Backlog: area `knowledge` (2026-10-06)

Source: `docs/audits/fixwave/pending-all-2026-10-05.json`, the 13 items with `"area": "knowledge"`.
Worktree `.claude/worktrees/bl-knowledge`, branch `backlog/bl-knowledge`, base `5ed25d973`.
Each item was re-checked against the current code (the branch already has the 2026-10-06 fixes:
registry embedder and reranker chains, short pages indexed, S3 source-scoped ids, FTS code tokens,
single-URL DLQ, concurrent connector syncs). None of the 13 was fixed by a later commit.

**Counts:** 10 fixed (were OPEN), 1 obsolete, 2 still open and waiting on an owner decision.
0 were already fixed.

| Item | Status | Reason | Commit / tests |
|---|---|---|---|
| a04-F066-03 | FIXED | A worker that died mid-clone kept its lease. The acks_late redelivery could not claim the job (only `queued` was claimable), so it dead-lettered a duplicate and the job stayed `running` until it was failed as interrupted. Now the claim takes over an **expired** lease. Other deliveries no longer fail or DLQ the job: a live lease gets a retry after one lease period, and a finished job is left alone. A job reconciled as interrupted is dead-lettered so the DLQ retry runs it again (terminal rows stay immutable, per migration 0092). | `97bb38fe3`. Tests: `tests/api/test_repo_ingest_redelivery.py`; integration in `tests/rag/test_persisted_rag_store.py` (`test_redelivery_takes_over_a_dead_workers_expired_lease_and_fences_it`, `test_a_live_lease_is_never_taken_over`, `test_terminal_jobs_are_never_reclaimed`, `test_repository_job_is_finished_by_the_redelivery_end_to_end`) |
| a04-F067-01 | FIXED | github/confluence/jira/slack used to fetch, screen and embed inside the request. They now create a durable leased job and return 202 with a `job_id`. The job runs on the ingestion queue. The token goes over the broker vault-encrypted and no Source row is persisted. | `d97ada1c3`. Tests: `tests/api/test_legacy_source_ingest_jobs.py`; integration `test_legacy_source_job_lifecycle_is_typed_fenced_and_reconciled`, `test_legacy_source_worker_indexes_through_the_pipeline_on_postgres` |
| a04-F067-04 | FIXED | Drive downloads now fetch 1 MiB ranges (cap+1 bytes when the cap is smaller), so at most one range past the cap is read. A file whose listed `size` is over the cap is refused before any download. The Drive Source connector also enforces `max_doc_size_bytes`; before this its downloads were unbounded. | `877c0068b`. Tests: `tests/ingestion/test_gdrive_connector.py::TestDownloadCap`, `tests/api/test_knowledge_gdrive_folder_honest.py::test_listed_size_over_the_budget_truncates_without_downloading` |
| a04-F067-06 | FIXED | Per-file `failed`/`errors` entries now hold a client-safe reason plus a `correlation_id`. The raw `str(exc)` goes only to the server log. | `877c0068b`. Tests: `tests/api/test_knowledge_gdrive_folder_honest.py` (sanitized + correlation id + known kinds) |
| a04-F068-01 | OBSOLETE | `IndexingStrategy = {raptor, agentic_chunking}` is a scope limit, not missing behaviour. These are the only catalogue strategies that need a `PRECOMPUTED_INDEX`. Every other strategy runs at query time on the base index. A test now keeps the API literal, the indexing pipeline and the catalogue in step. | `537e4df4e`. Test: `tests/api/test_indexing_strategy_vocabulary.py` |
| a04-F068-02 | FIXED | `paragraph` and `dom` used to alias SemanticChunker, so DOCX/HTML (one block per line) became a single paragraph cut at sentence boundaries. They are now real `ParagraphChunker` and `DomChunker` (section-aware, heading in every chunk, raw HTML read with the shared extractor). | `39655bb9c`. Test: `tests/ingestion/test_paragraph_dom_chunkers.py` |
| a04-F070-03 | FIXED | The legacy job runs the **registered connector** through the shared IngestionPipeline. The separate Confluence and Jira ingestors are deleted. The Jira connector gained `jql_extra`/`newest_first`; Slack gained channel names. GitHub and Slack connectors still use their ingestors, but only as HTTP clients, so there is one stack. Also fixed the GitHub connector bug: windows shared one doc id, so only each file's last window stayed indexed. | `d97ada1c3`. Tests: `tests/api/test_legacy_source_ingest_jobs.py`, `tests/ingestion/test_github_connector.py::test_every_file_is_one_document_with_a_distinct_id` |
| a04-F071-02 | FIXED | Same fix as F068-02. | `39655bb9c` |
| a04-F073-01 | FIXED | `llm` is removed from `RerankStrategy`. Settings refuses `RAG_DEFAULT_RERANK_STRATEGY=llm`, and any unknown name, with the list of supported values. Unknown names used to fall back to `auto` without saying so. `tfidf` is now an explicit strategy. | `f8a0c34bb`. Tests: `tests/rag/test_rerank_degradation_visible.py` |
| a04-F073-03 | FIXED | A cross-encoder inference error, or a hosted/registry rerank that failed, was unconfigured or came back incomplete, now records `last_strategy_used=tfidf` and `last_degraded_reason`. It increments `agentverse_rerank_degraded_total{reason}`. The default-path results are labelled `rerank_strategy=tfidf`, `rerank_degraded=<reason>`. | `f8a0c34bb`. Test: `tests/rag/test_rerank_fallback_honest.py` |
| a04-F074-05 | FIXED | Shared embedding-usage counting is now turned on at `worker_process_init` (prefork) and `worker_init` (solo/threads). Re-embeds and agent retrieval in a worker are counted. | `e112cc746`. Test: `tests/scaling/test_worker_embedding_usage_init.py` |
| a04-F077-01 | OPEN (owner decision) | RAFT fine-tuning covers OpenAI plus any OpenAI-compatible fine-tune vendor. Bedrock (CreateModelCustomizationJob + S3 staging + provisioned throughput) and Vertex tuning would each be a new provider. Neither can be verified here, and both carry cloud cost. Anthropic has no public fine-tuning API, so "Anthropic" only makes sense as Claude Haiku on Bedrock. | none |
| a04-F077-03 | OPEN (blocked, owner) | `tests/real_e2e/test_raft_real_fine_tune.py` (`RAFT_REAL_FINE_TUNE=1`) needs a paid OpenAI fine-tune run. Not run: it spends the owner's money and needs their go-ahead. | none |

## Behaviour changes callers should know

- `POST /knowledge/ingest/{github,confluence,jira,slack}` now return **202** `{status: "ingestion_started", job_id, source, source_type, collection_id}` instead of a synchronous 200 `{chunks_ingested}`. Poll `GET /knowledge/ingest/jobs/{job_id}`. Durable jobs need the database: an in-memory (no-DB) app now answers 503, the same as `/ingest/repo`. The frontend does not call these routes.
- `RAG_DEFAULT_RERANK_STRATEGY=llm` or an unknown value now fails settings validation. No deployment manifest sets it.
- The GitHub Source connector now yields one document per file instead of one per 1,500-character window. Doc ids are unchanged, so the next sync replaces the leftover windows.

## Verification

- ruff and `mypy app` are clean (1965 files).
- Focused suites: ingestion/knowledge/connector/celery/store tests, 2653 passed; chunker/pipeline tests, 827 passed; reranker tests, 313 passed; `tests/rag/test_persisted_rag_store.py -m integration` (testcontainers Postgres), 54 passed.
- One failure was already there before this work and is unrelated: `tests/scaling/test_worker_memory_budget.py::test_helm_worker_pools_fit_their_memory_limit[legacy]` (a YAML parse error in a helm template; no helm file was touched).
- `openapi.json` was regenerated. It also picks up unrelated drift that was already on the branch (`timeout_seconds` default).
- No alembic migration was added.
