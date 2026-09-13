# Hallucination Hardening Plan

Status: PLAN · Branch: `feat/agent-memory-governance` · TDD, no stubs.

## Goal
Drive agent hallucination toward zero on grounded tasks by (a) tightening the
existing grounding stack, (b) adding per-claim evidence scoring and calibrated
abstention, and (c) making citations a hard gate on the final answer — without
increasing latency on the common (already-grounded) path.

## Current state (verified)
- `app/agent/grounding.py::check_grounding` — extracts concrete claims
  (IDs/numbers/dates/URLs) and checks **literal substring** presence in tool
  evidence. Fail-closed (claims + no evidence → ungrounded; the P0-4 fix).
- `app/agent/prompts.py` — executor `INSUFFICIENT DATA` escape hatch; `GROUNDING_SYSTEM`
  LLM check; `JUDGE_RUBRIC_SYSTEM` independent judge; `SYNTHESIS_SYSTEM` requires `[Step N]`.
- State: `ungrounded_claims`, `consecutive_ungrounded` (2 → replan) in `AgentState`.
- Gap: matching is substring-literal (misses paraphrase + numeric-format drift, and
  false-positives on incidental substrings); no per-claim confidence; abstention is
  binary; citations are prompted but not enforced.

## Design
1. **Claim typing + normalization** — extend `extract_claims` to type claims
   (numeric, id, date, url, entity, quantitative-relation) and normalize
   (e.g. `1,024`≡`1024`, `2026-09-14`≡`Sep 14 2026`, currency, case/whitespace).
2. **Per-claim evidence scoring** — replace pure substring with a 3-tier match:
   exact-normalized → token-set/regex (numbers with units) → embedding-similarity
   (≥ threshold) against evidence spans. Emit `GroundingResult.claim_scores`.
3. **Calibrated abstention** — a `GroundingPolicy` with per-tenant/plan thresholds:
   `min_grounded_ratio`, `numeric_claims_require_exact=True`. Below threshold →
   the executor must emit `INSUFFICIENT DATA` for that claim rather than the value.
4. **Citation gate on synthesis** — post-process the `SYNTHESIS_SYSTEM` output:
   every sentence carrying a typed claim must carry a `[Step N]` whose step output
   actually contains the matched evidence; uncited claims are stripped or the
   answer is rejected → replan. (`app/context/citation_manager.py` already exists.)
5. **Semantic-entropy signal (optional, cost-gated)** — for high-stakes goals,
   sample the executor/synthesis N times at T>0, cluster by meaning, and treat high
   entropy as an ungrounded signal (Kuhn et al. semantic entropy). Off by default;
   gated by runtime profile + cost tier.
6. **Provenance completeness** — ensure every `provenance` entry links claim → step →
   tool_call → source_url so the UI/audit can show the evidence chain.

## TDD task list
- T1 `tests/agent/test_grounding_normalization.py` — numeric/date/currency
  equivalence; incidental-substring false-positive is NOT grounded. → extend `grounding.py`.
- T2 `tests/agent/test_grounding_evidence_scoring.py` — 3-tier match + `claim_scores`;
  paraphrase grounded via embedding tier; wrong number ungrounded. → `grounding.py` + embedder dep.
- T3 `tests/agent/test_grounding_policy.py` — `GroundingPolicy` thresholds per plan;
  numeric-exact rule; abstention path returns INSUFFICIENT DATA. → new `app/agent/grounding_policy.py`.
- T4 `tests/agent/test_citation_gate.py` — synthesis with an uncited claim is
  rejected/stripped; every kept claim's `[Step N]` truly contains evidence. → wire `citation_manager`.
- T5 `tests/agent/test_semantic_entropy.py` (marked slow) — high-entropy sample set
  flagged ungrounded; low-entropy passes. → new `app/agent/semantic_entropy.py`, cost-gated.
- T6 `tests/agent/test_grounding_routing.py` — grounding policy failure feeds
  `consecutive_ungrounded` and routes to `rag_remediate`/replan.

## Acceptance
- No regression on the grounded happy path latency (embedding/entropy tiers only
  fire on a claim that fails the cheap tiers, or under high-stakes profiles).
- Wrong-number / paraphrased-but-unsupported claims are caught (T2/T4).
- Final answers carry only evidence-backed, cited claims (T4).
- All new tests green under `-m "not slow"`; entropy test under `-m slow`.
