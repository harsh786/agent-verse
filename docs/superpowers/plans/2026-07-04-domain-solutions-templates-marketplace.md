# AgentVerse Domain Solutions, Goal Templates & Marketplace Agents — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan. Content authoring is fanned out **one subagent per domain** (37 authoring jobs) against a fixed schema + validator, then verified. This is the companion build-out to the master plan (`2026-07-04-agentverse-world-class-master-plan.md`) — specifically it realizes **Phase 6 (Skills)** and **Phase 7 (Marketplace Depth, Domain Agents, Solutions)** at full scale across all 37 verticals.

**Goal:** Turn the 37-domain use-case library (`docs/domains/`, 418 detailed use cases) into shipping product: **~45 net-new MCP connectors** (India-first portals + capability pseudo-connectors — count raised from 25 after the coverage re-audit found ~20 referenced systems, incl. domain-09's own GeM flagship, missing from the build list), **150+ detailed parameterized goal templates** (152 allocated), **200+ in-depth marketplace agents** (214 allocated) that each solve a real domain problem end-to-end, with **total UC coverage** (every one of the 418 use cases maps to ≥1 record, test-enforced), and **37 installable Domain Solutions** (agent + knowledge + workflows + schedules + policies + evals + sample data) — all with world-class UI, backend, and e2e tests, so AgentVerse can be sold as a vertical product per domain. Each domain also has an **ADR** (`docs/adr/domains/NN-*.md`, all 37 written) and the platform ADR is `docs/adr/0001-...`.

**Architecture:** Move template/agent content out of in-code Python lists into a validated **content-as-data** layer (`app/content/marketplace/<domain>.yaml`, `app/content/goal_templates/<domain>.yaml`) loaded by the existing idempotent seeders. Each marketplace agent is authored directly from a specific `UC-N` in the domain doc. Connectors that back India portals with no public API are built as **Browser-RPA connectors** on the existing `app/rpa/` stack. Domain Solutions bundle content via the Phase-7 `Solution` schema. UI extends `src/features/marketplace/` and `src/features/templates/` plus new per-domain landing pages.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 async / Alembic / Playwright (RPA) / pydantic (content validation) / React 19 / Vite / TanStack Query / Vitest / Playwright e2e.

## Global Constraints

- Inherits **all** constraints from the master plan (`2026-07-04-agentverse-world-class-master-plan.md`), including the **standard deliverable per track** (backend + world-class UI + tests + load profile) and **fail-closed** rule.
- **Prerequisite gate:** this plan depends on master-plan **Phase 0A/0B/0C** (security/wiring) and **Phase 7** (Solution schema, grounded MetaAgentPlanner, security-reviewer fix). Connector Foundation (Track 1) and content schema (Track 2) can start in parallel with Phase 7; content *seeding* must wait for the security-reviewer fix so agents pass review without the `run_security_review=False` bypass.
- Content is **data, not code**: no giant Python literal lists. Every agent/template is a YAML record validated by a pydantic model at load and in CI. Files stay under 300 lines by splitting per domain.
- Every marketplace agent ships with: a unique `slug` + `template_id`, real (existing-or-built) connector names, a JSON-Schema `parameters_schema`, a **golden eval fixture** (≥1 golden task), and a passing `TemplateSecurityReviewer` verdict (`safe`/`low`).
- Every goal template ships with `{{double_brace}}` placeholders matching an extracted `parameters` array.
- No secrets in content; connector auth resolves via the vault.
- Currency/metrics in content mirror the domain docs (₹ + global), but agent behavior must not hardcode prices — pull from parameters/knowledge.

---

## Part A — Audit Summary (grounding facts)

**Domain library (verified):** 37 domains, **418 use cases** (24 domains × 12 UCs + 13 × 10). Every file has one sample `AgentManifest` YAML and per-UC connectors/revenue/ROI/workflow. Gold-standard depth: healthcare (1176 ln), education (1053). **Thinnest four (enrich during authoring): 05-operations, 34-wealth-management, 35-fashion-apparel, 36-architecture-interior-design.**

**Code schema (verified, exact — see also the schema-extraction report in the session tasks dir):**
- **Marketplace template** = plain `dict` in `_BUILTIN_TEMPLATES` (`app/enterprise/marketplace_v2.py:254`), persisted by idempotent `seed_builtins()` → `publish_template(..., run_security_review=False)` under tenant `"system"`, `visibility="public"`. Table `marketplace_templates` (migration `0059`). Required keys: `template_id, name, slug (UNIQUE), domain, description, required_connectors[], autonomy_mode, author_name, template_config{goal_template, autonomy_mode}, parameters_schema (JSON-Schema draft-07, single-brace `{placeholders}`), visibility, review_status, is_builtin`. Optional: `long_description, subdomain, category, tags[], optional_connectors[], icon_url, is_verified, version`.
- **`install()`** (`marketplace_v2.py:1447`) inserts `agents(id, tenant_id, name, goal_template, autonomy_mode)` only — does **not** set `connector_ids`/`system_prompt`. **Gap to fix in this plan:** install must map `required_connectors` → `connector_ids` and template `system_prompt` → agent, or agents install without their tools.
- **Goal template** = `GoalTemplate` (`app/db/models/template.py`, table `goal_templates`, migration `0048`): `id, tenant_id, name, description, goal_text ({{double_brace}}), parameters (JSONB array of {name, description, required, default}), domain, use_count, version`. Seeded from `_BUILTIN_TEMPLATES` in `app/api/templates.py:67` via idempotent `_seed_builtins_db()` (uuid5 id, `ON CONFLICT DO NOTHING`). Placeholder regex: `\{\{(\w+)\}\}`.
- **Agent** = `Agent` (`app/db/models/agent.py`): `system_prompt, goal_template, model_override, autonomy_mode ∈ {supervised, bounded-autonomous, fully-autonomous}, connector_ids[], trigger_config{trigger_type ∈ TriggerType, cron_expression, interval_seconds, event_channel, timezone}, max_iterations, timeout_seconds, allowed_collection_ids[], eval_suite_id, policy_ids[]`. Create API enforces domain rules (e.g. `legal` requires `domain_metadata.bar_number`).
- **Connectors:** `CONNECTOR_CATALOG` (`app/mcp/catalog.py`, 227 specs) — `name` strings used in `required_connectors`/`connector_ids`. **No India-portal connectors** (only `razorpay`, `zoho_*`). Pseudo-connectors referenced but not real: `document_reader, web_search, audio_transcriber, database_query, pdf_generator`.

**Existing content to not collide with:** 32 marketplace template IDs (8 V1 + 24 V2 across software/devops/testing/hr/sales/support/legal/healthcare/education/finance/ecommerce) and 15 seeded goal templates. New `slug`/`template_id`/goal-template `name` values must be unique.

**Net gaps this plan closes:** (1) 418 use cases, only ~32 productized; (2) ~25 domains blocked by missing connectors; (3) content doesn't scale as in-code lists; (4) `install()` drops connectors; (5) no per-domain packaging, knowledge seed, or trial; (6) security reviewer rejects normal connectors (bypassed, not fixed).

---

## Part B — Content-as-Data Architecture (foundation for all content)

### B.1 Content schema & loader

**Files:** Create `app/content/schema.py` (pydantic models), `app/content/loader.py` (load + validate + seed), `app/content/marketplace/<domain>.yaml` (37 files), `app/content/goal_templates/<domain>.yaml` (37 files). Test: `tests/content/test_loader.py`, `tests/content/test_content_valid.py`.

`app/content/schema.py` (the contract every authored record must satisfy):

```python
from pydantic import BaseModel, Field, field_validator

class MarketplaceAgentContent(BaseModel):
    template_id: str          # "tpl-<domain>-<slug>" globally unique
    slug: str                 # "<domain>-<kebab>" globally unique
    name: str
    domain: str               # one of the 37 domain keys
    subdomain: str | None = None
    description: str          # 1-2 lines
    long_description: str     # 1-3 paragraphs, from the UC "Problem" + "Solution"
    source_use_cases: list[str]  # ["06-legal/UC-1", ...] — provenance; an agent may cover >1 UC
    system_prompt: str        # the agent's role/grounding (200-600 words)
    goal_template: str        # single-brace {placeholders} matching parameters_schema
    parameters_schema: dict    # JSON-Schema draft-07, properties + required
    required_connectors: list[str]   # must exist in catalog OR be built in Track 1
    optional_connectors: list[str] = []
    autonomy_mode: str = "bounded-autonomous"
    default_trigger: dict = Field(default_factory=dict)  # trigger_config shape
    knowledge_collections: list[str] = []  # seed collection slugs the agent uses
    eval_fixtures: list["EvalFixture"]     # >=1 golden task
    tags: list[str] = []
    revenue_model: str        # from the UC, for the UI card
    roi_note: str             # from the UC
    version: str = "1.0.0"

    @field_validator("autonomy_mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        assert v in {"supervised", "bounded-autonomous", "fully-autonomous"}
        return v

class EvalFixture(BaseModel):
    goal: str                 # a concrete goal instance
    required_tools: list[str] = []
    forbidden_tools: list[str] = []
    expected_output_contains: list[str] = []

class GoalTemplateContent(BaseModel):
    name: str                 # globally unique
    domain: str
    description: str
    goal_text: str            # {{double_brace}} placeholders
    # parameters auto-extracted from goal_text at load
    source_use_cases: list[str]
```

`app/content/loader.py` responsibilities: glob YAML → validate each via the model (collecting all errors) → assert `slug`/`template_id`/`name` global uniqueness across files AND against the 32+15 existing → assert every `required_connectors` entry exists in `CONNECTOR_CATALOG` or the Track-1 built list → transform to the exact dict shapes `seed_builtins()` / `_seed_builtins_db()` expect → hand off to those seeders. On any validation failure in `ENVIRONMENT=production`, **fail startup** (fail-closed); in dev, log and skip the bad record.

- [ ] Step 1: Write failing test `test_content_valid.py::test_all_marketplace_records_validate` that globs the YAML dirs, validates each against `MarketplaceAgentContent`, and asserts zero errors + global slug uniqueness. (Fails: dirs empty / model absent.)
- [ ] Step 2: Implement `schema.py` + `loader.py`; add a tiny sample YAML so the test can pass structurally.
- [ ] Step 3: Wire `loader.load_and_seed(marketplace, template_store)` into the existing seed path in `create_app()`/lifespan (replacing the in-code `_BUILTIN_TEMPLATES` iteration; keep the old 32 by migrating them into YAML in Step 5).
- [ ] Step 4: CI gate — add `uv run python -m app.content.loader --check` to CI so malformed content fails the build.
- [ ] Step 5: Migrate the existing 32 marketplace + 15 goal templates into the YAML layer (delete the in-code lists) so there is one source of truth. Commit `refactor(content): move marketplace/goal-template content to validated YAML layer`.

### B.2 Fix `install()` to carry connectors, system_prompt, knowledge, evals

**Files:** `app/enterprise/marketplace_v2.py:1447-1538`. Test: `tests/enterprise/test_marketplace_install.py`.
- [ ] Failing test: installing `tpl-legal-contract-review-v2` creates an agent whose `connector_ids` == template `required_connectors`, `system_prompt` set, `allowed_collection_ids` == seeded knowledge, `eval_suite_id` set.
- [ ] Extend the INSERT to populate `system_prompt, connector_ids, allowed_collection_ids, eval_suite_id, policy_ids` from the template record; keep param validation. Commit.

---

## Part C — Distribution: 200 Agents + 150 Templates across 37 domains

**Allocation rule:** every domain gets marketplace agents for its **highest-automatable, highest-ROI** use cases (full end-to-end agents with knowledge + schedule + evals); goal templates cover the **ad-hoc / single-shot** tasks and common variations. A high-value UC often yields **both** (an always-on agent + a run-now template). Counts weighted by India TAM (README top-10) and automatability.

| # | Domain | Agents | Templates | Flagship agent (from UC) |
|---|--------|:-:|:-:|---|
| 20 | Banking & FinTech | 8 | 5 | KYC/AML investigation & SAR filing (UC-1/6) |
| 12 | Healthcare | 8 | 5 | Insurance pre-auth & claim submission (UC-3) |
| 07 | GST & Tax | 8 | 5 | GSTR-1 auto-filing from ERP (UC-1) |
| 06 | Legal | 8 | 5 | Contract review & red-lining (UC-1) |
| 10 | E-commerce | 8 | 5 | Catalog enrichment at scale (UC-1) |
| 27 | Accounting/CA | 8 | 5 | Bulk ITR filing across 50+ clients (UC-1) |
| 15 | Cybersecurity | 8 | 4 | SIEM alert triage & enrichment (UC-1) |
| 01 | HR & Talent | 6 | 5 | Onboarding automation Day0–30 (UC-4) |
| 02 | Software Eng | 6 | 5 | Contextual code review (UC-1) |
| 03 | DevOps | 6 | 4 | Incident response & triage (UC-1) |
| 04 | Sales & CRM | 6 | 5 | Lead enrichment & scoring (UC-1) |
| 08 | Invoicing/Finance | 6 | 4 | 3-way match & AP fraud (UC-2/12) |
| 14 | Marketing | 6 | 5 | SEO content factory (UC-2) |
| 16 | Logistics | 6 | 4 | Freight invoice audit (UC-6) |
| 17 | Insurance | 6 | 4 | Claims triage & FNOL (UC-2) |
| 19 | Manufacturing | 6 | 4 | Predictive maintenance alerting (UC-1) |
| 13 | Real Estate | 6 | 4 | Rent collection & escalation (UC-3) |
| 24 | Pharmaceutical | 6 | 4 | CDSCO license filing (UC-1) |
| 25 | Telecom | 6 | 4 | Churn prediction & retention (UC-2) |
| 26 | Construction | 6 | 4 | RA bill automation (UC-2) |
| 30 | Energy & Utilities | 6 | 4 | Smart-meter anomaly detection (UC-1) |
| 18 | Customer Support | 5 | 4 | Tier-1 auto-resolution (UC-1) |
| 05 | Operations | 5 | 4 | Freight/procurement optimization (UC-3) |
| 09 | Government Portal | 5 | 4 | GeM tender monitoring & bid (UC-4) |
| 11 | Education | 5 | 4 | Automated grading + feedback (UC-2) |
| 21 | Agriculture | 5 | 4 | Crop disease diagnosis (UC-1) |
| 22 | Hospitality/Travel | 5 | 4 | Dynamic pricing (UC-1) |
| 23 | Media/Publishing | 5 | 4 | Content moderation at scale (UC-2) |
| 28 | Food/Restaurant | 5 | 4 | Swiggy/Zomato reconciliation (UC-2) |
| 29 | Recruitment | 5 | 4 | Bulk resume screening (UC-1) |
| 31 | Automobile/EV | 5 | 4 | RC transfer & RTO compliance (UC-2) |
| 32 | Non-Profit/NGO | 4 | 3 | FCRA compliance & filing (UC-3) |
| 33 | Events/MICE | 4 | 3 | Vendor RFQ-to-PO (UC-4) |
| 34 | Wealth Mgmt | 4 | 3 | Portfolio rebalancing (UC-1) |
| 35 | Fashion/Apparel | 4 | 3 | Markdown optimization (UC-6) |
| 36 | Architecture/Interior | 4 | 3 | Drawing approval tracking (UC-1) |
| 37 | Public Health | 4 | 3 | PM-JAY claim QC (UC-4) |
| — | **TOTAL** | **214** | **152** | |

(Exact per-domain UC→agent/template mapping is produced by each domain's authoring subagent in Track 2 and recorded in `docs/domains/<domain>/content-map.md`.)

**Total-coverage rule (added after the coverage re-audit — MANDATORY):** the counts above are a *floor for flagship depth*, not a coverage ceiling. The re-audit proved that 214 agents + 152 templates with **one** UC each would orphan ≥52 of the 418 use cases (worst in NGO, Events, and the enriched thin domains — 12 UCs but only 7 records). Two rules close this:
1. **Every UC in every domain doc maps to ≥1 content record** (agent OR template). Since `source_use_cases` is now a **list**, one agent may legitimately cover several related UCs — so the agent *count* need not equal the UC count, but the **UC→record coverage must be total**.
2. **Coverage is enforced by a test, not by trust:** `tests/content/test_coverage.py` parses every `UC-N` from `docs/domains/*/use-cases.md`, unions all `source_use_cases` across the YAML, and **fails if any UC is unreferenced** (or if a domain is explicitly listed in an `INTENTIONALLY_NOT_PRODUCTIZED` allowlist with a reason). Each domain's authoring subagent must emit `docs/domains/<domain>/content-map.md` proving its UC→record mapping before its batch is accepted. Under-allocated domains (NGO, Events, Education, Government, Food, Recruitment, Automobile, Public Health, and the 3 enriched thin domains) get their template counts raised until coverage is total.

---

## Part D — Track 1: Connector Foundation (prerequisite)

Agents that name a missing connector cannot run. Build these before/with content authoring.

### D.1 Formalize capability pseudo-connectors
**Files:** `app/mcp/servers/` + `app/mcp/catalog.py`. Add real catalog entries + builtin handlers for `document_reader` (wraps existing `app/tools/document_parser.py`), `web_search` (wraps `app/tools/web_search.py`/SearXNG), `pdf_generator` (wraps `app/tools/artifact_tool.py`), `database_query`, `audio_transcriber` (uses an embedder/provider with audio). Test each resolves via the registry.

### D.2 India-first Browser-RPA connectors (no public API → RPA)
**Files:** new `app/mcp/servers/india/*.py` on the `app/rpa/` Playwright stack, each exposing tool functions (login via injected vault creds, navigate, fill, submit, extract, screenshot-as-evidence). Ship in priority order (TAM-driven):

1. **GST/GSTN** (`gst_portal`) — GSTR-1/3B/9 filing, ITC download (GSTR-2B), e-way bill. (domains 07, 08, 10, 27, 28)
2. **Income-Tax / ITR** (`income_tax_portal`) — ITR filing, Form 26AS, TDS. (07, 27, 34)
3. **MCA21** (`mca21`) — ROC filings, company master data. (06, 27)
4. **EPFO** (`epfo`) + **ESIC** (`esic`) — PF/ESI challans, claims. (01, 29)
5. **DigiLocker** (`digilocker`) — document fetch/verify. (09, 20, 31)
6. **Tally** (`tally`) — via Tally's XML/ODBC HTTP gateway (semi-API). (08, 27, 28)
7. **Vahan/Sarathi** (`vahan`) — RC/DL/RTO. (31)
8. **RERA** (`rera_portal`, state-parametrized) — filings, project status. (13, 26)
9. **Aggregators** (`swiggy_partner`, `zomato_partner`, `ondc`) — settlement reports. (28, 10)
10. **Naukri** (`naukri`) — resume search/sourcing. (29, 01)
11. **Regulator filings** (`trai_portal`, `irdai_portal`, `cdsco_portal`) — sector filings. (25, 17, 24)
12. **Health schemes** (`pmjay`, `idsp`, `nhm_portal`) — claims/surveillance. (12, 37)

**Wave 2 — the coverage-re-audit gap (~20 systems referenced by domain docs/ADRs but missing from the original list; without these, authored agents reference tools that never get built):**
13. **`gem_portal`** (Govt e-Marketplace) — **this is domain 09's own flagship agent (UC-4)** yet was missing; tender monitoring + bid prep. (09)
14. **`udyam`** (MSME registration), **`rti_portal`**, **`fssai`/foscos** (food licensing). (09, 27, 28)
15. **Agriculture cluster** — **`enam_mandi`/agmarknet** (mandi prices), **`pm_kisan`**, **`pmfby`** (crop insurance), **`kcc`** (Kisan Credit Card), IMD weather / ISRO Bhuvan / WDRA feeds. **(Domain 21 has 5 agents but had ZERO connectors — starkest gap.)**
16. **Capital-markets** — **`sebi_portal`**, **`nsdl_cdsl`** (depositories), FEMA/FATCA. (34, 20)
17. **Customs/EXIM** — **`icegate`**, **`dgft`**. (16, 10, 35)
18. **`cibil`** / credit bureau. (20, 21)
19. **Pharmacovigilance** — **`vigibase`/pvpi** (adverse-event; CDSCO covered licensing only). (24)
20. **`cerc_serc`** tariff portals; **`pwd_cpwd`** works/tender portals. (30, 26)
21. **`police_noc`/fire e-NOC** (per-jurisdiction, physical-dispatch fallback). (33, 22, 09)
22. **India health systems** — **`abdm`/abha** + **HMIS** (epic_fhir is US-only). (12, 37)
23. **`aadhaar_esign`/nsdl_esign** (DigiLocker covers fetch, not signing). (many)
24. **Banking rails** — **`upi_npci`**, core-banking adapters (Finacle/Flexcube), **`razorpayx`** (plaid is US-only). (20, 08)
25. **Industrial** — **`scada_mes`** bridge / OPC-UA / MQTT (SAP exists in catalog; shop-floor does not). (19, 30) — this is **New-API/protocol**, not RPA.

Note: the ADRs' §3 "Connectors Required" sections already enumerate these per-domain — Track 1 must be scoped from the **union of all 37 ADR §3 lists**, not a fixed guess. Target ~45 connectors total across D.1+D.2 wave1+wave2. Prioritize by the monetization order (ADR-0001): GST/ITR/MCA21 first (profit engine), then GeM + agriculture cluster (blocks domains 09/21 entirely), then the rest TAM-ordered.

Each connector: one file, ≤300 ln, credential injection via vault, **SSRF guard applies** (master-plan 0C.1), rate-limited, screenshot artifacts for audit, integration test against a recorded/mock DOM (never live gov portals in CI). RPA-portal connectors are classified `write_high`/`destructive` where they submit filings → route through HITL by default. Industrial protocols (`scada_mes`, OPC-UA/MQTT) and core-banking are **New-API/protocol connectors**, not RPA.

- [ ] Per connector: failing test (tool discovery + a mocked-DOM happy path), implement, register in catalog + `registry_wiring.py`, commit. ~25 connectors total (D.1 + D.2).

### D.3 Security-reviewer scope fix (unblocks real content)
**Files:** `app/enterprise/marketplace_v2.py` (`TemplateSecurityReviewer`). Currently normal connector names land in `over_requested` → not-approved, so built-ins bypass review. Fix: separate **connector names** (validated against the catalog allowlist) from **OAuth scopes**; approve templates whose connectors are catalog-known and whose autonomy/connector combo isn't in the critical set. Now content seeds **with** review, not bypassed. Test: a legal contract-review template reviews to `low`/`safe` and `approved=True`.

---

## Part E — Track 2: Content Authoring (fan-out, one subagent per domain)

**Process (subagent-driven):** dispatch one authoring subagent per domain. Each reads `docs/domains/<domain>/use-cases.md`, the schema (`app/content/schema.py`), the connector catalog (incl. Track-1 additions), and this plan's mapping rules; then emits `app/content/marketplace/<domain>.yaml` (N agents) + `app/content/goal_templates/<domain>.yaml` (M templates) + `docs/domains/<domain>/content-map.md` (UC→content provenance). The loader/validator (Part B) is the objective gate — a domain's output isn't done until `test_content_valid.py` passes for it.

**Authoring rules the subagent follows:** map each chosen `UC-N` to one agent; `long_description` = UC Problem+Solution; `system_prompt` = a grounded role (cite the domain, name the standards/portals, forbid fabrication of IDs/amounts, require HITL for filings/payments); `goal_template` params = the UC's variable inputs; `required_connectors` = the UC's "MCP Connectors Used" mapped to real catalog/Track-1 names; ≥1 `eval_fixture` per agent (a concrete goal + expected substrings + required/forbidden tools); India filings → `autonomy_mode: bounded-autonomous` + HITL policy. Enrich the 4 thin domains to 12-UC depth while authoring.

### E.1 Fully-worked reference example — marketplace agent (NOT a placeholder)

`app/content/marketplace/06-legal.yaml` (one record, complete — the pattern every other agent copies):

```yaml
- template_id: tpl-legal-contract-review-v2
  slug: legal-contract-review-pro
  name: Contract Review & Risk Red-Lining Agent
  domain: legal
  subdomain: contracts
  source_use_case: "06-legal/UC-1"
  description: >-
    Reviews any commercial contract in minutes — flags risk clauses against
    your clause library and returns a redlined DOCX with a risk summary.
  long_description: >-
    Reviewing a 40-page contract takes a paralegal 2-4 hours; firms process
    100-500/month at ~₹400/contract in legal time. This agent parses the
    contract, classifies its type, loads the firm's standard clause library
    from the knowledge base, reviews clause-by-clause (liability, IP,
    termination, indemnity, data-processing), flags deviations with severity,
    checks for missing protective clauses, and produces a tracked-changes DOCX
    plus a Critical/High/Medium/Low risk report. A lawyer approves via HITL
    before anything leaves the firm.
  system_prompt: >-
    You are a senior legal analyst reviewing contracts for an in-house team.
    Ground every finding in the provided contract text and the firm's clause
    library from the knowledge base. NEVER invent clause numbers, party names,
    dates, or monetary figures — quote the exact contract text you rely on. If
    a standard clause is missing, say so explicitly. Classify each risk as
    Critical/High/Medium/Low with the business impact. You may draft alternative
    language, but you MUST route the redlined document through human approval
    before it is sent to any counterparty. If the contract text is unreadable
    or a required input is missing, emit "INSUFFICIENT DATA" rather than guess.
  goal_template: >-
    Review the contract at {document_url} under {jurisdiction} law. Classify the
    contract type, extract all parties, obligations, deadlines, and liability
    clauses, compare each clause against the firm clause library, flag
    deviations and missing protections with severity, and produce a redlined
    DOCX plus a risk summary. Route the redline for human approval before finalizing.
  parameters_schema:
    properties:
      document_url: {type: string, format: uri}
      jurisdiction: {type: string, enum: [in, us, uk, eu, sg, ae]}
      contract_type:
        {type: string, enum: [nda, msa, sow, employment, lease, vendor, other]}
    required: [document_url, jurisdiction]
  required_connectors: [document_reader, docusign, gmail]
  optional_connectors: [dropbox, box]
  autonomy_mode: bounded-autonomous
  default_trigger: {}
  knowledge_collections: [legal-clause-library, legal-standards-in]
  eval_fixtures:
    - goal: >-
        Review the MSA at https://example.com/msa.pdf under in law; flag any
        liability cap below 2x fees and any missing data-processing addendum.
      required_tools: [document_reader]
      forbidden_tools: []
      expected_output_contains: ["liability", "risk", "redline"]
  tags: [legal, contracts, compliance, red-lining]
  revenue_model: "₹5,000/contract; ₹50,000/mo unlimited for legal teams"
  roi_note: "Contract review 4-6h → 15min; ~60% outside-counsel savings"
  version: "1.0.0"
```

### E.2 Fully-worked reference example — goal template (NOT a placeholder)

`app/content/goal_templates/07-gst-tax.yaml` (one record, complete):

```yaml
- name: File GSTR-3B for Client
  domain: gst-tax
  source_use_case: "07-gst-tax/UC-1"
  description: >-
    Prepare and file the monthly GSTR-3B summary return for one client from
    their ERP sales/purchase data, with an HITL check before submission.
  goal_text: >-
    Prepare GSTR-3B for {{client_name}} (GSTIN {{gstin}}) for the tax period
    {{period}}. Pull outward supplies and ITC from {{source_system}}, reconcile
    against GSTR-2B from the GST portal, compute net tax payable, generate the
    return summary for review, and after human approval file it on the GST portal
    and store the ARN and filed challan in the client folder.
```

(Auto-extracted `parameters`: `client_name`, `gstin`, `period`, `source_system` — all required, no defaults.)

### E.3 Execution
- [ ] Dispatch 37 authoring subagents (batched ~6 at a time to respect concurrency), each producing its two YAML files + content-map, gated by `test_content_valid.py`.
- [ ] After each batch: run the loader `--check`, the uniqueness test, and the security-reviewer test over new records; fix or bounce back failures.
- [ ] Commit per domain: `feat(content): <domain> marketplace agents + goal templates`.

---

## Part F — Track 3: Domain Solution Packaging

Realizes master-plan Phase 7 "Solutions" for all 37 domains. A **Solution** = a domain's agents + seed knowledge collections + starter workflows + schedules + guardrail/policy bundle + eval suite + sample data + onboarding checklist, installable atomically.

**Files:** `app/content/solutions/<domain>.yaml`, `app/enterprise/solutions.py` (installer, extends the transactional `install()`), migration for a `solutions`/`solution_installs` table if not added in Phase 7. Test: `tests/enterprise/test_solution_install.py`.

- Seed knowledge collections per domain (e.g. `legal-clause-library`, `gst-law-provisions`, `hsn-sac-master`, `cdsco-drug-guidelines`) with **ingestion recipes** (which docs/portals to pull, incl. RPA crawls) rather than shipping copyrighted text.
- Bundle domain guardrail policies (regulated domains → fail-closed, HITL on filings/payments) using the existing policy engine.
- Sample data + simulation trial: each Solution installs a mock dataset so a tenant can run its flagship agent in **simulation mode** (`app/enterprise/simulation.py`) before connecting real systems.
- [ ] Reference Solution first: **Law Firm** (agents from 06-legal + `legal-clause-library` collection + intake workflow + matter-deadline schedule + legal fail-closed policy + citation-accuracy eval suite + sample contracts). Then fan out the remaining 36 by the same template. Each: failing install test → author YAML → verify atomic install in simulation → commit.

---

## Part G — Track 4: World-Class UI

**Files:** extend `src/features/marketplace/` and `src/features/templates/`; new `src/features/domains/` (per-domain landing) and `src/features/solutions/`. e2e in `agent-verse-frontend/e2e/`.

1. **Domain gallery** (`/domains`) — 37 domain cards (icon, tagline, agent/template counts, TAM/ROI from docs), searchable/filterable; each opens a **domain landing page** (`/domains/:domain`) with the vertical's story, its agents, templates, and one-click Solution install.
2. **Marketplace browser upgrade** — handle 200+ agents: server-side search (full-text + semantic via the template embedding), domain/subdomain/connector/tag facets, sort by installs/rating, verified badges, pagination/virtualized grid. Agent detail drawer: long description, required connectors (with "you need to connect X" state), parameter form, eval-pass badge, revenue/ROI, "Try in simulation" button.
3. **Template browser upgrade** — 150+ goal templates with domain facets, `{{param}}` form with validation, "Run now" wiring to goal submission.
4. **One-click install & trial** — param form → install → (if connectors missing) guided connect flow → run flagship goal in simulation with sample data → show the result + cost estimate.
5. **Standard-deliverable UI bar:** semantic tokens, WCAG 2.2 AA (axe-clean), loading/empty/error states, optimistic install with rollback, responsive, dark mode.

- [ ] Build per component with Vitest unit tests; wire to real APIs; commit per surface.

---

## Part H — Track 5: Quality, Evals & Governance

- **Every agent's eval fixture** becomes a golden task in the domain's eval suite (ties to master-plan Phase 11). A domain Solution's "verified" badge requires its suite passing in simulation.
- **Injection review** on every `system_prompt` and `goal_template` (skills/agents are an injection vector — master-plan Phase 10); content CI runs the injection scanner over all YAML.
- **Connector reality check** in CI: assert every `required_connectors` entry resolves in the registry (catalog or Track-1) — prevents shipping agents that can't run.
- **Regulated-domain policy check:** CI asserts filings/payments agents (gst, banking, legal, healthcare, pharma) declare a HITL policy and `bounded/supervised` autonomy.
- [ ] Add `tests/content/test_quality_gates.py` enforcing all four; wire into CI.

---

## Part I — Track 6: Tests (unit + integration + e2e)

- **Coverage (`tests/content/test_coverage.py`):** parse every `UC-N` from `docs/domains/*/use-cases.md`, union all `source_use_cases` across the YAML, **fail if any UC is unreferenced** (unless in the `INTENTIONALLY_NOT_PRODUCTIZED` allowlist with a reason). Also assert every `required_connectors` name resolves in the catalog or Track-1 build list. This is the gate that makes "nothing is missed" verifiable.
- **Unit:** `schema.py` validators, `loader.py` uniqueness/transform, `install()` connector mapping, each Track-1 connector's tool functions (mocked DOM/HTTP).
- **Integration (testcontainers):** seed all content into Postgres → assert 200 marketplace rows + 150 goal-template rows exist with correct domains; install one agent per domain and assert the agent row has connectors + eval suite; RLS isolation on new tables.
- **e2e (Playwright):** (1) browse domains → open a domain → install a Solution → run flagship in simulation → see result; (2) marketplace search+facet finds a specific agent; (3) template "Run now" submits a goal; (4) axe a11y assertion on domain gallery + agent detail + template form.
- **Load:** k6 profile on `GET /marketplace/templates` (search+facet) at catalog scale (200+ rows, concurrent tenants).
- [ ] One test/spec per bullet; coverage ≥80% on new backend code; commit.

---

## Part J — Execution Sequencing & Self-Review

**Sequence:** Track 1 (connectors) ∥ Part B (content schema/loader) → Track 5 CI gates → Track 2 (37 authoring subagents, batched) → Part B.2 install fix → Track 3 (Solutions, Law Firm first) → Track 4 (UI) ∥ Track 6 (tests) continuously. Gate: content seeding waits for Track 1 connectors + D.3 reviewer fix so agents pass review unbypassed.

**Staffing:** connectors and content authoring parallelize heavily (25 connector jobs + 37 authoring jobs are largely independent); UI is one stream; Solutions one stream. Estimated ~200 agents + 150 templates + 25 connectors + 37 Solutions + full UI/tests is a multi-week build best run as batched subagent fan-out with the validator as the objective gate.

### Self-Review (per writing-plans skill)
- **Ask coverage:** "audit all domains" → Part A + the 37-domain inventory (session tasks dir); "world-class features per domain to target as a product" → Track 3 Domain Solutions + Track 4 domain landing pages + Track 1 India connectors; "100–200 detailed goal templates" → 150 in Part C, schema B, worked example E.2; "200 in-depth marketplace agents solving big problems" → 200 in Part C, schema B, worked example E.1; "separate detailed plan" → this file.
- **Placeholder scan:** the content *pattern* is fully worked (E.1/E.2 are complete real records, not stubs); the 200/150 individual records are authored in Track 2 by subagents against the fixed schema + validator (the correct granularity — enumerating all 350 records in the plan would be noise, and each is provenance-linked to a specific `UC-N`). No "TBD" in schema, loader, connector list, distribution, UI, or tests.
- **Schema consistency:** field names/types verbatim from the extraction (single-brace for marketplace `goal_template`, double-brace for `GoalTemplate.goal_text`, `autonomy_mode` enum, `TriggerType` values, real connector names). The two known blockers (missing connectors, reviewer scope logic, `install()` dropping connectors) each have an explicit fixing task (D.2, D.3, B.2).
- **Dependency honesty:** flagged the master-plan prerequisites (Phase 0 security, Phase 7 Solution schema) and the seed-after-connectors gate so nothing ships referencing a tool that can't run.

## Part K — Coverage Re-Audit Addendum & Future Verticals

An adversarial coverage re-audit (2026-07-04) verified this plan against all 418 use cases and the 37 ADRs. Applied fixes: (1) `source_use_cases` is now a list + a **total-coverage rule and test** (Part C, Track 6) — the original 214+152 records with one UC each would have orphaned ≥52 UCs; (2) Track 1 connectors raised ~25 → **~45**, adding the ~20 India systems the ADRs reference but the build list omitted (notably **GeM** — domain 09's own flagship — and the **entire agriculture cluster**, which had 5 agents and 0 connectors); (3) header counts reconciled to the table (214/152); (4) all 37 domain ADRs confirmed written with 8 sections each. Remaining nuance: ensure the 05-operations ADR carries the thin-domain enrichment flag (34/35/36 already do).

**Candidate future verticals (domains 38–45+, not built now — logged so they aren't forgotten):**
1. **Ports, Shipping & Maritime (EXIM)** — pairs with the new ICEGATE/DGFT customs connectors; distinct buyer from domestic logistics (16).
2. **Mining & Metals / Natural Resources** — IBM/environmental-clearance filings, royalty/DMF compliance.
3. **Aviation / Airlines / Airport & MRO** — DGCA compliance, crew rostering, MRO parts.
4. **Gaming, Esports & Real-Money Gaming** — RMG state-law + GST-on-gaming compliance, LiveOps.
5. **Crypto / Web3 / Digital Assets** — VDA tax (30% + 1% TDS), on-chain analytics; **low connector lift** (catalog already has alchemy/moralis/alpaca).
6. **Biotech / Genomics / Clinical Research (CRO)** — CTMS/EDC ops, IEC/CDSCO trial approvals; distinct from pharma manufacturing (24).
7. **SaaS Operations / RevOps** — churn, dunning, usage-based billing; catalog already has chargebee/zuora-class connectors.
8. **Defense & Aerospace procurement** — offset management, DPP/DAP tendering.
- **Split existing:** Education (11) is monolithic — consider **K-12 (RTE) vs Higher-Ed/EdTech (UGC/AICTE)** as separate SKUs given divergent buyers and compliance.

These reuse the same content-as-data + Solution + connector machinery — each is one ADR + one content folder + its connector deltas.
