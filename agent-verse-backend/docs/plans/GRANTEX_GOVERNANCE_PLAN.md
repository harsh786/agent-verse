# Grantex-style Governance / Trust / Audit Plan

Status: PLAN · Branch: `feat/agent-memory-governance` · TDD, no stubs.

## Goal
Make governance a **first-class, platform-level** concern by adding the primitives
Grantex defines (delegated authorization for AI agents) on top of what AgentVerse
already has: **verifiable agent identity, scoped/time-limited/revocable grants,
tamper-evident audit, multi-agent delegation chains, and offline (JWKS) verification.**

## Current state (verified) — already ~60% there
- HITL gateway (`app/governance/hitl.py`) — N-of-M approvals, cross-replica, durable,
  boot rehydration.
- Policy engine (`app/governance/policies.py`), tool-risk classification
  (`app/agent/tool_risk.py`), cost governance (`app/governance/cost.py`), append-only
  audit trail, tenancy + RLS, compliance module (GDPR/SOC2).
- **Gaps vs Grantex:** no per-agent cryptographic identity; authority is static
  per-connector `auto_approve` (not scoped/TTL/revocable grants); audit is append-only
  but not hash-chained/tamper-evident; sub-agent spawn inherits context but carries no
  delegation proof; no offline token verification (JWKS).

## Design — new package `app/governance/grants/`
1. **Agent identity** (`identity.py`) — each agent gets a keypair; issue a signed
   agent identity token (JWT, `sub=agent_id`, `tenant`, `created_by`) distinct from the
   creating user. Private keys in the existing vault (`app/providers/vault.py`).
2. **Grants** (`grant.py`, `store.py`) — a `Grant` = {grantor (human/org), grantee
   (agent_id), scopes (tool/resource patterns), constraints (max cost, row limits),
   `not_before`/`expires_at`, revocation status}. Signed → a **grant token**. Persisted
   in a new `grants` table (RLS, FORCE). Revocation = status flip + short-TTL cache.
3. **Enforcement** (`enforcer.py`) — checked at the SAME three points the HITL gateway
   already gates in `executor_mixin._execute_step`: resolve the effective grant for
   (agent, tool, args), verify scope+constraints+expiry+revocation before the tool
   runs; deny → same INSUFFICIENT/blocked path. Composes with tool-risk + policy.
4. **Delegation chains** (`delegation.py`) — when the supervisor spawns a sub-agent,
   mint a **delegation token** that references the parent grant and can only **narrow**
   scope (never widen). Verified up the chain; audit records the full authority chain.
5. **Tamper-evident audit** (`audit_chain.py`) — extend the append-only trail with a
   per-tenant **hash chain** (each record includes `prev_hash`; optional signature).
   `verify_chain()` detects tampering; export produces a compliance evidence pack.
6. **Offline verification** (`jwks.py` + endpoint) — publish public keys at
   `/.well-known/jwks.json`; services verify agent/grant/delegation tokens offline
   (no per-request call to AgentVerse), matching Grantex's JWKS model.
7. **Consent + revocation UX** (API first, UI later) — endpoints to grant, list, and
   revoke authority; the HITL inbox already provides the human approval surface.

## TDD task list
- G1 `tests/governance/test_agent_identity.py` — issue/verify a signed agent identity;
  wrong key rejected; tenant-scoped. → `identity.py`.
- G2 `tests/governance/test_grants.py` — create scoped/TTL grant; expired denied;
  out-of-scope tool denied; in-scope allowed; revocation takes effect. → `grant.py`+`store.py`+migration.
- G3 `tests/governance/test_grant_enforcement.py` (integration) — executor tool call
  denied when no covering grant; allowed when covered; deny reason surfaced. → `enforcer.py` wired in `executor_mixin`.
- G4 `tests/governance/test_delegation_chain.py` — sub-agent delegation can only narrow
  scope; widening rejected; chain verified end-to-end. → `delegation.py` + supervisor spawn.
- G5 `tests/governance/test_audit_hash_chain.py` — hash-chained records; tamper in the
  middle fails `verify_chain`; evidence-pack export. → `audit_chain.py`.
- G6 `tests/governance/test_jwks_offline_verify.py` — JWKS endpoint serves keys; a token
  verifies offline against them; rotated key still validates old tokens during overlap. → `jwks.py`+router.

## Acceptance
- A tool call with no covering, unexpired, unrevoked grant is denied at the executor gate.
- Sub-agents cannot exceed their parent's authority (delegation narrowing enforced).
- Audit chain is tamper-evident and exportable; tokens verify offline via JWKS.
- Composes with existing HITL/policy/cost gates (no bypass); RLS-enforced; mypy/ruff clean.

## Status (implementation)
- **G1 agent identity — ALREADY EXISTS** (`app/auth/agent_identity.py`): RS256 JWT
  `issue_agent_token`/`verify_agent_token`, RSA-2048 `generate_agent_keypair`,
  `agent_credentials` table, `AgentIdentityService`. No rebuild needed.
- **G6 JWKS offline verify — ALREADY EXISTS**: `_build_jwks` → `/.well-known/jwks.json`
  (RFC 7517, Redis-cached). No rebuild needed.
- **G2 grants + G3 enforcement decision — DONE** (`app/governance/grants/`):
  `Grant`/`scope_matches`/`InMemoryGrantStore`/`check_grant`, fail-closed. Tested.
- **G4 delegation — DONE** (`delegation.py`): narrow-only `mint_delegation`. Tested.
- **G5 tamper-evident audit — DONE** (`app/governance/audit_chain.py`): hash chain +
  evidence pack. Tested.
- **Remaining integration** (follow-up, needs migration/executor surgery — not stubbed):
  (a) Postgres `grants` table + repo + issuance API so grants persist and can be
  administered; (b) wire `check_grant` into `executor_mixin._execute_step` at the
  same opt-in gate points as HITL (default OFF via a setting → zero regression until
  a tenant issues grants); (c) mint delegation tokens in the supervisor sub-agent
  spawn path; (d) feed grant/tool-call decisions into `AuditChain`.

## Sequencing
G1→G2→G3 (identity → grants → enforcement) is the MVP that makes governance
first-class at the tool-execution boundary. G4 (delegation), G5 (tamper-evident audit),
G6 (offline JWKS) layer on top. UI/consent dashboards follow the API.
