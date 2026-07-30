# AgentVerse Full-Product Live Certification

Run ID: `AVCERT-20260729-001`
Original execution: 2026-07-28/29 (Asia/Kolkata)
Task 12 checkpoint: 2026-07-30
Verdict: **NOT CERTIFIED**

## Executive summary

The original live-certification run remains NOT CERTIFIED. Task 12 now has one typed,
authoritative 18-entry RAG capability catalogue in code, but it does not replace the required
Program 1 broad suite, real-provider, PostgreSQL/Redis, API, or paid RAFT evidence.

The original run established substantial real OpenAI and infrastructure coverage while also
finding release blockers in runtime-role RLS enforcement, Celery Redis checkpointing,
concurrent Fusion retrieval, missing model propagation, swallowed RAG API failures, and broad
Ruff/mypy/regression failures. Those findings are not cleared by this checkpoint.

## Task 12 canonical RAG checkpoint

### Code evidence

The canonical registry now contains exactly these 18 IDs, in public contract order:

`naive`, `hybrid`, `hyde`, `multi_hop`, `graph`, `corrective`, `adaptive`, `modular`,
`speculative`, `agentic`, `web_augmented`, `fusion`, `self_rag`, `flare`, `raptor`,
`agentic_chunking`, `colbert`, and `raft`.

Each catalogue entry owns its canonical strategy, concrete adapter class/factory, typed
runtime dependencies, readiness predicate, and adapter-owned identity/contract probe.
Registry promotion to `IMPLEMENTED` is derived from the adapter class satisfying the keyed
canonical strategy and contract; a missing, abstract, mismatched, or unregistered adapter is
not promoted. Static contract probes invoke `probe_trace()` on the concrete adapter, verify
adapter and trace strategy identity, and return non-empty contract evidence without loading
artifacts or calling external providers. This is identity/contract evidence only; it is not
operational implementation or live-readiness evidence.

`RAG_RUNTIME_CAPABILITIES` is a read-only view derived from the catalogue.
`RAG_RUNTIME_ADAPTERS` is a lazy compatibility alias to that same object, so neither is an
independently maintained map. `core_strategy_capabilities()`, registry dependency metadata,
and `/rag/strategies` ordering and dependency output derive from the same catalogue.

Readiness covers database/schema, embedder, provider, graph, policy-authorized web, local-only
RAGatouille and ColBERT checkpoint artifacts, RAFT lifecycle service, and compatible completed
RAFT model states. Shared facts are computed once per discovery request and interpreted only
by catalogue predicates. ColBERT readiness never downloads artifacts. RAFT model readiness
queries durable tenant-scoped state using local provider/capability selectors before recency
ordering, without submitting paid work or calling a provider. Gateway adapter lifecycle
closure remains close-once; the process-default cross-encoder now has an app-lifespan shutdown
owner, and temporary legacy rerankers close after use.

The obsolete `ALL_RAG_PATTERNS` instance catalogue was removed. It duplicated dispatch
metadata and could drift from the canonical runtime adapter catalogue.

Legacy tests that described RAPTOR, Speculative RAG, and FLARE as silently falling back to
Hybrid were removed. Canonical adapters remain fail-closed and strategy identity is not
relabelled by the registry.

### Added or updated evidence

- `tests/rag/test_registry_complete_rag.py` checks the single immutable catalogue, exact
  18-ID parity, `IMPLEMENTED` state, distinct concrete adapter classes, invocation and exact
  evidence for every adapter probe, every dependency-unavailable state, derived view
  identity, and registry/core/API parity.
- `tests/rag/test_raft_lifecycle.py` checks provider-free completed-model readiness before
  inference.
- `app/rag/catalogue.py` is the sole 18-entry strategy, adapter, dependency, readiness, and
  probe ownership table.
- `tests/rag/test_strategy_dispatch.py` retains dispatch and registry coverage without
  certifying silent fallback behavior.
- `app/orchestration/strategy_registry.py` resolves and probes canonical adapters directly.
- `app/rag/agentic/patterns/__init__.py` exposes the single canonical adapter map.

### Verification status

The post-hardening Task 12 gate additionally covers catalogue-predicate authority,
local-only ColBERT artifact checks, filtered RAFT selection, O(1) discovery probing, lazy
catalogue imports, and executor lifecycle closure. On 2026-07-30, the focused quality gate
completed with **106 tests passed**. Targeted Ruff checks passed, and targeted mypy checks
passed across the 10 changed readiness, gateway, RAFT, reranker, API, registry, and config
source files:

```bash
HF_HUB_OFFLINE=1 uv run pytest tests/rag/test_registry_complete_rag.py \
  tests/rag/test_core_strategy_evidence.py tests/rag/test_colbert_runtime.py \
  tests/rag/test_cross_encoder_runtime.py tests/rag/test_raft_lifecycle.py \
  -q --no-cov
uv run ruff check <changed Task 12 readiness and focused test files>
uv run mypy <10 changed Task 12 readiness source files>
```

The final Program 1 automated gate also passed on 2026-07-30:

- Non-integration RAG/knowledge suite: **1,089 passed, 4 skipped, 66 deselected**. The four
  skips require unavailable local cross-encoder/ColBERT model artifacts.
- PostgreSQL/Testcontainers integration suite: **66 passed, 1,093 deselected** using the
  documented Colima socket and forced-RLS runtime roles.
- Exact Program 1 Ruff scope: **all checks passed**.
- Exact Program 1 mypy scope: **64 source files, no issues**.

This report therefore records a passing Program 1 automated gate, not a full-product
certification result. Real-provider and paid-external-action gates remain below.

The final review also verified that the configured ColBERT checkpoint is used by inference,
RAGatouille is isolated in the pinned `colbert` optional extra, RAFT relationships use
tenant-consistent composite foreign keys, and the regenerated OpenAPI document exposes all
seven `/rag/raft/*` lifecycle routes.

## Live-provider and RAFT status

Real-provider API smoke certification remains pending for every non-paid canonical strategy.
No claim is made that all 17 non-paid journeys currently pass against the running API,
PostgreSQL, Redis, and the configured real provider.

At this checkpoint, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, and
`OLLAMA_BASE_URL` were all unset, so a real-provider smoke run was not possible without user
credentials or a configured local model endpoint.

RAFT lifecycle and adapter code are present, but paid fine-tune submission was not performed.
Paid RAFT certification remains blocked until the user sees the provider cost preview and
confirms that exact external action at execution time. A completed compatible model and live
inference journey must then be recorded before RAFT can be marked live-certified.

## Release decision

Do not promote this revision as fully certified. Task 12 may be accepted only after the
focused pytest, Ruff, and mypy commands above pass. Program 1 additionally requires the broad
RAG/knowledge and integration gates plus real-provider API journeys. Paid RAFT remains
pending explicit action-time cost confirmation. The original full-product blockers remain
subject to their own remediation and retest evidence.
