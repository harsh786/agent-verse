# ADR-0001: AgentVerse as a Multi-Vertical Product Platform

- **Status:** Accepted (foundational)
- **Date:** 2026-07-04
- **Deciders:** Platform architecture
- **Related:** `docs/superpowers/plans/2026-07-04-agentverse-world-class-master-plan.md`, `docs/superpowers/plans/2026-07-04-domain-solutions-templates-marketplace.md`, `docs/domains/` (37 domains, 418 use cases), per-domain ADRs in `docs/adr/domains/`

---

## Context

AgentVerse is a horizontal, vendor-agnostic autonomous-agent platform. The business goal is to sell it as **37 vertical products** (one per domain in `docs/domains/`) to millions of end-organizations, India-first and global, while keeping **one codebase and one operations team**. Two full audits established that the platform core is broadly built but carries ~89 verified defects (7 CRITICAL) and lacks the productization, connector, and scale substrate to run millions of domain workloads profitably.

The central tension: **breadth (37 verticals) vs. maintainability + unit economics at scale.** This ADR fixes the decisions that resolve it. Each domain then gets its own ADR (`docs/adr/domains/NN-*.md`) applying these decisions to that vertical's use cases.

---

## Decision 1 — Domains are DATA, not code

A vertical is **never** a fork, branch, or domain-specific service. The execution core (`app/agent/`, providers, RAG, guardrails, cost, tenancy) stays **domain-blind**. Everything vertical-specific lives in **four data planes**:

1. **Content** — agents + goal templates as validated YAML (`app/content/`), one folder per domain.
2. **Knowledge** — per-domain seeded collections + ingestion recipes (clause libraries, HSN masters, drug guidelines).
3. **Connectors** — the integration substrate (Decision 3).
4. **Policies** — per-domain guardrail/HITL/compliance bundles.

**Invariant (the acceptance test for productization):** adding a new vertical requires **zero changes to `app/agent/`**. If a domain forces a core change, domain logic has leaked and must be pushed back into the four data planes.

**Consequence:** one team maintains 37 products; domain #38 is one YAML folder + one authoring subagent + (maybe) one connector.

## Decision 2 — The product unit is a Domain Solution installed into a Tenant

- Hierarchy: **Platform → Tenant (a firm/hospital/bank/retailer) → Users (roles) → installed Solutions → running agents.** (Requires the master-plan Phase 1 `users`/`tenant_memberships` model — today 1 SSO login wrongly = 1 tenant.)
- A **Solution** = bundle of {agents, knowledge collections + ingestion recipes, workflows, schedules, guardrail/compliance policies, eval suite, sample data, onboarding checklist, required connectors}, installed atomically, trialable in **simulation mode** on sample data before real systems are connected.
- Solutions are the sellable SKU. Tenants self-serve install from the marketplace or buy a curated vertical package.

## Decision 3 — Connector substrate: API where it exists, RPA-farm where it doesn't

- **Bucket 1 (~60–70% of agents): existing catalog connectors** (227 today) — email, Slack, WhatsApp, GitHub, Jira, Salesforce, Shopify, DocuSign, Sheets, Razorpay, Zoho. **Zero new integration** — author-only.
- **Bucket 2 (~30%): no-public-API portals** (GST/GSTN, MCA21, EPFO/ESIC, Vahan/RTO, RERA, DigiLocker, PM-JAY/IDSP, aggregator dashboards, Naukri) → **Browser-RPA connectors** on the existing `app/rpa/` Playwright stack, encoded once as thin deterministic tools (not raw LLM click-by-click), credential-injected from the vault, HITL-gated for filings, screenshot-audited.
- **Bucket 3 (few): semi-API** (Tally XML gateway) → thin wrapper.
- **RPA is not a fallback — it is the moat.** Competitors on APIs cannot touch portals that have none. RPA + credential injection + HITL is the only general path to India's regulatory verticals.
- **Scale caveat (accepted):** browser automation is heavy (1 Chromium/session). RPA runs as its own **autoscaled worker pool / headless browser farm** Celery queue, with session reuse, per-tenant login caching, and batching; prefer bulk-upload/semi-API endpoints over click flows where a portal offers one.

## Decision 4 — Cost is COGS; the platform must be cheap-per-goal before it scales

At millions of goals (bulk GST filings, catalog enrichment, invoice matching), **LLM token cost defines unit economics.** Non-negotiable before volume (master-plan Phase 2/5): per-goal tool retrieval (stop injecting all tool schemas every call), provider prompt caching, semantic + response caching wired into the **worker** path (a current P0 gap), and model routing that sends bulk/simple steps to cheap or **local (Ollama)** models. Every domain ADR states its dominant workload shape and cost drivers.

## Decision 5 — P0 before scale (hard gate)

The audit's defects change severity under load and **block** multi-vertical scale: the `cancel_goal` concurrency double-decrement corrupts every tenant's plan accounting; unbounded SSE queues and the per-goal engine leak exhaust memory/connections; RLS-blocked maintenance means GDPR/retention deletes silently never run (compliance time bomb); in-memory tenant authority breaks multi-replica. **No vertical ships to production on top of unfixed Phase 0A/0B/0C.**

## Decision 6 — Scale architecture is keyed to 4 workload shapes

Every domain maps to one dominant pattern (from `docs/domains/README.md`), each scaling differently:
1. **High-volume repetitive** (GST/CA, ecommerce catalog, invoicing) → scheduled fan-out into per-plan Celery queues + per-tenant bulkhead; cost-optimization critical.
2. **Approval-gated** (legal, banking, filings) → cross-replica HITL (must fix C6) + notification inbox + SLA escalation.
3. **Real-time monitoring** (DevOps, cyber, telecom, energy) → beat monitors + dedup + circuit breakers + backpressure.
4. **Research + doc-gen** (due diligence, audit, dossiers) → multi-agent supervisor with **aggregate budget caps** (a current gap) + long-context RAG.

---

## Decision 7 — Monetization priority: which vertical first, and why

Scored on 6 weighted criteria (score 1–5 × weight, normalized to 100):

| Criterion | Weight |
|---|:-:|
| Market size / TAM | 20 |
| Revenue recurrence & stickiness (mandatory compliance) | 20 |
| Time-to-first-revenue (speed) | 15 |
| Build cost / connector readiness (inverse) | 15 |
| Willingness to pay / price realization | 15 |
| Distribution leverage / low CAC (channel) | 15 |

| Vertical | TAM | Recur | Speed | Build | WTP | Distrib | **Score** |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| **07/27 GST & Tax + CA Firm** | 5 | 5 | 3 | 3 | 4 | 5 | **85** |
| 04 Sales & CRM | 4 | 4 | 5 | 5 | 3 | 3 | 80 |
| 06 Legal | 4 | 3 | 5 | 5 | 5 | 2 | 79 |
| 18 Customer Support | 3 | 4 | 5 | 5 | 3 | 3 | 76 |
| 10 E-commerce | 4 | 4 | 4 | 4 | 3 | 3 | 74 |
| 15 Cybersecurity | 4 | 4 | 3 | 4 | 4 | 2 | 71 |
| 12 Healthcare | 5 | 4 | 2 | 2 | 4 | 2 | 66 |
| 20 Banking/FinTech | 5 | 4 | 1 | 1 | 5 | 1 | 60 |

### The pick: **GST & Tax + CA Firm (domains 07 + 27) — the profit engine.**

Rationale — it maximizes *most-monetary* **and** *profit-in-less-time* simultaneously:
- **Mandatory, recurring, high-frequency:** GST returns file every month for every one of ~1.5 crore filers. Compliance is non-optional → the goal volume is structural and recurring, not campaign-driven. Recurring revenue compounds fastest.
- **Distribution leverage (the decisive factor):** ~3.5 lakh CAs. One CA-firm tenant carries 50–500 client sub-accounts. Selling to the *firm* lands hundreds of paying end-orgs per deal → the lowest CAC of any vertical. This is why GST/CA beats faster-to-build domains on total profit velocity.
- **Proven pricing:** ₹5K–1.5L/month + per-filing consumption already validated in the domain doc.
- **The one gate — GST portal RPA (Bucket 2):** a ~2–3 week build that unlocks a ₹2,500 cr/yr recurring market. Worth it.

### The parallel fast-start (revenue in weeks, while GST RPA is built):
**Sales/CRM + Legal** — both Bucket-1 (zero new connectors), highest speed/build scores, high WTP. Ship these self-serve (PLG) to generate first revenue and reference logos immediately, funding the GST/CA build. This is the land-and-expand entry.

**Rollout order:** (1) Sales/CRM + Legal fast-cash beachhead → (2) GST & CA Firm profit engine (build GST/ITR/MCA21 RPA) → (3) fan out remaining 34 via the content pipeline, TAM-ordered (healthcare, ecommerce, cyber, banking…).

---

## Decision 8 — Packaging, pricing & GTM

- **SKU = Domain Solution** in three tiers mirroring the platform plans: Starter (₹5–20K/mo, 1–3 workflows), Professional (₹25K–1L/mo, full suite), Enterprise (₹1L–10L+/mo, white-label + BYO models/endpoints).
- **Consumption add-ons** where volume dominates: ₹/filing, ₹/document, ₹/goal, ₹/unit-managed.
- **Three motions:** (1) **PLG self-serve** via marketplace + simulation trial; (2) **Channel resale** — CAs/brokers/consultants resell vertical Solutions to their clients (tenant-of-tenants); (3) **White-label** for vertical-SaaS ISVs.

---

## Per-Domain ADR Template (the standard for `docs/adr/domains/NN-*.md`)

Every domain ADR MUST follow this structure so the 37 are comparable:

```
# ADR-D<NN>: <Domain> Vertical on AgentVerse
Status · Date · Source: docs/domains/NN-*/use-cases.md · Monetization rank

## 1. Market & Monetization
TAM, buyer, pricing tiers (₹), consumption model, WTP, time-to-first-revenue, monetization score

## 2. Use Cases → Product Mapping   (table)
| UC-N | Title | Ships as | Bucket (1/2/3) | Connectors | Scale pattern | HITL? |
(every UC in the domain doc mapped to an Agent, a Goal Template, or both)

## 3. Connectors Required
Existing (catalog) | New API | RPA-portal (with the specific portal) — flag build cost

## 4. Knowledge Collections
Seed collections + ingestion recipe (docs/portals to crawl; no copyrighted text shipped)

## 5. Guardrails & Compliance
Regulated? (which regime); mandatory HITL gates; fail-closed policy; audit/evidence needs

## 6. Scale Pattern & Cost Drivers
Dominant workload shape (1–4); expected goal volume; token/RPA cost drivers; caching/routing levers

## 7. Decision & Phasing
What we build first (flagship agent), what's fast-follow, what's gated on connectors

## 8. KPIs
Adoption, reliability (eval pass %, RPA success %), cost/goal, revenue/tenant
```

---

## Consequences

**Positive:** one engine → 37 products; new verticals are cheap; the RPA-portal moat is defensible; channel distribution (GST/CA) gives the lowest CAC; simulation trial de-risks sales.

**Negative / risks (accepted, with mitigation):**
- **RPA brittleness** (portal DOM changes) → mocked-DOM CI tests + monitoring + fast content patching (RPA scripts are data too).
- **RPA/gov-portal legal & ToS** → per-tenant consent, act-as-the-real-user credential injection, rate limiting, legal review before shipping each gov connector.
- **CAPTCHA/OTP/2FA** → `rpa_detect_captcha` + `rpa_request_human_help` HITL.
- **LLM cost blowout at volume** → Phase 2/5 cost controls are prerequisites, not options.
- **India DPDP / data-residency** → flagged for the re-audit; needs region pinning + residency controls (likely a new master-plan item).

## Alternatives considered & rejected

1. **Per-domain microservices/forks** — rejected: 37 codebases, unmaintainable, kills the invariant.
2. **API-only (skip RPA)** — rejected: abandons the entire India regulatory market (no APIs exist), which is the highest-monetization segment.
3. **Content in-code (current `_BUILTIN_TEMPLATES` lists)** — rejected: doesn't scale past ~30 records, unmaintainable at 200+, and the security reviewer rejects normal connector names.
4. **Boil-the-ocean 37-at-once launch** — rejected: proves nothing about unit economics/reliability; land-and-expand on 2–3 domains first.
