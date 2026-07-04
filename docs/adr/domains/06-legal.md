# ADR-D06: Legal & Compliance Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/06-legal/use-cases.md

## 1. Market & Monetization

**TAM.** Global legal-technology market **$35.5B (2024) → $148B (2035)**. The wedge is the mechanical majority of legal work: in-house teams spend ~60% of time on routine review/research/compliance; average contract review is 92 minutes; SMEs spend $8,000–25,000/year on outside counsel for automatable work; missed deadlines drive ~40% of malpractice claims. AgentVerse positions as a **tier-1 legal analyst / paralegal-that-never-sleeps** — augmenting lawyers, not replacing judgment.

**Buyer.** Three segments: (1) **in-house legal / GC office** (contract review, compliance, invoice audit, IP); (2) **law firms** (research, DD, intake, court drafting); (3) **procurement / compliance officers** in regulated industries (BFSI, pharma, food). Highest WTP is the in-house GC buying down malpractice/regulatory-fine risk and outside-counsel spend.

**Three pricing tiers (₹):**
| Tier | Price | Scope |
|------|-------|-------|
| Legal Starter | ₹20,000/mo | Contract review, NDA generation, basic legal research; ≤50 contracts/mo; India-jurisdiction focus |
| Legal Professional | ₹75,000/mo | + compliance monitoring, IP tracking, litigation-deadline mgmt; unlimited contracts; multi-jurisdiction; dedicated template library |
| Legal Enterprise | ₹3,00,000+/mo | + DD support, regulatory monitoring; white-label for law firms; iManage/NetDocuments API; 99.9% SLA; legal audit trail; custom clause-library maintenance |

**Consumption model.** Subscription + per-artifact overage: ₹5,000/contract reviewed, ₹10,000/research memo, ₹2,000/NDA generated, ₹8,000/court document, ₹3,000/matter intake, ₹5,000/mo per 50 IP assets, ₹10,000/mo per litigation portfolio (≤20 cases). Two performance-based models: **20% of recovered over-billing** (UC-8 invoice audit) and **fixed-fee/discount arrangements** on DD (UC-3) — both align price to realized savings.

**WTP.** High but more discretionary than tax/GST — legal spend is not government-mandated monthly. Anchored to outside-counsel displacement (60% savings on contract review; $500K→$150K on DD) and catastrophic-risk avoidance ($50M+ per significant error; GDPR fines up to 4% global revenue).

**Time-to-first-revenue.** Fastest of the four assigned domains: the flagship UCs (contract review, NDA generation, research, compliance monitoring, IP, litigation calendar, invoice audit, intake) are almost entirely **Bucket-1** — document_reader + knowledge base + docusign + web_search. No government-portal RPA is required for the core product. Sellable in weeks.

**Monetization note.** Unlike GST/CA, legal is **not** a mandatory-recurring-compliance profit engine — demand is discretionary and volume-lumpy. But it carries the *highest per-unit price* (₹/contract, ₹/memo) and premium enterprise ACV, and it composes naturally with the CA-firm channel (boutique legal+accounting practices). Treat it as a high-ACV attach, not the volume engine.

## 2. Use Cases → Product Mapping

| UC-N | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|------|-------|----------|--------|------------|---------------|-------|
| UC-1 | Contract Review & Risk Red-Lining | Flagship review agent (redline + risk summary) | 1 | document_reader, KB (clause-library), email, docusign | High-volume-repetitive (100–500 contracts/mo) | **Yes** — lawyer reviews redlines before send |
| UC-2 | Legal Research Automation | Research-memo agent | 1 | web_search (Indian Kanoon/case DBs), document_reader, KB | Batch per brief | Lawyer reviews memo |
| UC-3 | Due Diligence Report Generation | Multi-agent DD supervisor | 1 | document_reader, KB, multi-agent supervisor, SharePoint/Dropbox | Low-volume-complex, parallel sub-agents | **Yes** — lead attorney adds strategy layer |
| UC-4 | GDPR/SOC2/HIPAA Compliance Monitoring | Continuous compliance-posture agent | 1 | HRIS/cloud (AWS/GCP via HTTP), email, slack, KB (policies) | Continuous scheduled scan | Alerts; action items to team |
| UC-5 | NDA & Standard Contract Generation | Contract-generation agent (3 variants) | 1 | pdf_generator/document_generation, docusign, KB, email | High-volume-repetitive | Optional review; send gated |
| UC-6 | IP Portfolio Monitoring & Management | IP-calendar + registry-watch agent | 1 (+API) | USPTO/IPO/EUIPO (HTTP API), web_search, pdf_generator, email | Continuous monitoring + deadline fan-out | Alerts; attorney review on conflicts |
| UC-7 | Litigation Timeline & Deadline Management | Litigation-calendar agent | 1 | google_calendar/calendar, pdf_generator, email, KB (court rules) | Continuous, per-matter deadline calc | Alerts; no auto-filing |
| UC-8 | Legal Invoice Auditing | Invoice-audit + dispute-letter agent | 1 | document_reader, email, KB (billing guidelines, engagement letters) | Batch per invoice | Review before dispute sent |
| UC-9 | Client Intake Automation (Law Firms) | Intake + conflict-check agent | 1 | email, pdf_generator, docusign, KB (conflict DB) | Event-driven per matter | **Yes** — conflict hit → attorney decision |
| UC-10 | Employment Law Compliance Monitoring | Employment-law watch agent | 1 | web_search (gazette/labour notifications), KB (HR policies), email, HRIS (HTTP) | Continuous scheduled scan | Alerts; policy-change review |
| UC-11 | Regulatory Change Alert & Impact Analysis | Regulatory-impact agent | 1 | web_search (SEBI/RBI/IRDAI/FSSAI/MCA), KB, jira, email | Continuous 3×/day + impact fan-out | Alerts; Jira tasks |
| UC-12 | Court Filing & Document Preparation | Court-drafting agent | 1 (+2 e-file) | web_search (legal DBs), pdf_generator, court e-filing portal (RPA), KB (court-format rules) | Low-volume-complex per filing | **Yes** — attorney reviews before e-file |

## 3. Connectors Required

**Existing (Bucket 1) — the bulk of this vertical, low incremental cost:** `document_reader` (contract/invoice parsing — the workhorse), `pdf_generator`/document generation, `docusign` (+ `pandadoc`), `email`, `slack`, `web_search` (legal research, regulatory scraping), `google_sheets`, `postgresql`, `linkedin` (optional conflict/KYC context). Multi-agent supervisor (`app/agent/supervisor.py`) for parallel DD review (UC-3). Knowledge base + clause library are the core IP.

**New-API (HTTP, not RPA):**
- **IP registries** — USPTO, IPO (India), EUIPO for UC-6. These expose public/semi-public search APIs → HTTP connector, not RPA. Build ~2–3 wks.
- **Cloud/HRIS posture connectors** — AWS/GCP config + HRIS reads for UC-4/UC-10 compliance evidence. HTTP/SDK. Build ~2–4 wks depending on breadth.
- **DMS integrations** — iManage / NetDocuments / SharePoint / Dropbox for DD data rooms (UC-3) and executed-contract filing. API. Build ~2–3 wks each; iManage/NetDocuments are Enterprise-tier gated.
- **Jira** — compliance task creation (UC-11). API, trivial.

**RPA-portal (Bucket 2) — only one, and it is optional:**
- **Court e-filing portals** (UC-12) — Indian court e-filing (e-Courts / High Court portals) have no uniform API → RPA where e-filing is available. Highly fragmented by court; build lazily per demand. Estimate **3–5 weeks** for the first court, incremental thereafter.

**Legal research note:** Indian Kanoon and case-law databases (UC-2) are accessed via `web_search`/scraping today; a dedicated **`indian_kanoon` connector (RPA/new)** can be built if higher fidelity/citation extraction is needed — treat as an optimization, not a blocker.

## 4. Knowledge Collections

Seed slugs + ingestion recipe:
- **`legal-clause-library`** — *per-tenant* company-standard clauses + acceptable thresholds (liability caps, IP, termination, indemnity, DPA). Recipe: seed with company standards; grow from HITL-approved redlines (UC-1) and generated contracts (UC-5). The core defensible asset.
- **`jurisdiction-rules-india`** — Indian Contract Act, Specific Relief Act, Consumer Protection Act, court-format rules (High Court / District / NCLT / Consumer Forum), court-fee schedules. Recipe: ingest bare Acts + court procedural rules; drives UC-1 clause checks and UC-12 formatting/fees.
- **`case-law-precedents`** — Supreme Court / High Court rulings, indexed by legal principle. Recipe: crawl Indian Kanoon/case DBs via `web_search`, parse, tag by issue + citation, flag overruled positions; powers UC-2, UC-12.
- **`past-contracts-precedents`** — *per-tenant* executed-contract corpus for consistency + conflict reference. Recipe: ingest DMS contracts with searchable metadata (UC-5 step 10).
- **`compliance-policies`** — GDPR/SOC2/HIPAA/PCI-DSS/DPDPA control catalogs + the tenant's own policies. Recipe: ingest framework control lists + company policy docs; used by UC-4, UC-10.
- **`billing-guidelines`** — outside-counsel billing rules + engagement-letter rate cards. Recipe: ingest per-tenant guidelines for UC-8 invoice-audit checks.
- **`regulatory-notifications`** — SEBI/RBI/IRDAI/FSSAI/MCA/labour-ministry circulars, indexed by effective date + sector. Recipe: 3×/day scrape (UC-11/UC-10), classify significance, retain with impact notes.
- **`conflict-database`** — *per-tenant* clients/matters/opposing-parties for UC-9 conflict checks. Recipe: maintained from intake + matter records.

## 5. Guardrails & Compliance

**Regulated by professional-responsibility rules and, for UC-12, by court procedure — but note this vertical is *advisory/document* work, not government filings-with-money. HITL is about legal judgment and irreversibility, not tax liability.**
- **No irreversible external action without lawyer approval.** The reference manifest sets `autonomy_mode: supervised`, gates `docusign.*|email.send` behind `require_approval`, and **hard-denies** `docusign.complete_signing` — the agent may draft and stage but never *execute* a contract. Replicate for court e-filing (`court_portal.efile` = require_approval) and dispute-letter sending (UC-8).
- **Fail-closed on legal ambiguity:** UC-2/UC-12 must flag areas where law is unsettled or a position may be overruled rather than assert a conclusion; conflict hits (UC-9) *block* intake pending attorney decision.
- **Citation integrity:** research/drafting outputs must carry verifiable citations; the LangGraph verifier cross-checks that cited cases exist and are not overruled before a memo/brief reaches HITL — the primary hallucination guard in a domain where a fabricated citation is malpractice.
- **Audit & evidence:** append-only audit trail (`app/governance/audit.py`) — Enterprise tier explicitly requires a **legal audit trail**. Record document versions, who approved each redline, and every external send. UC-4 compliance evidence packages (policy docs, access logs, training records) are themselves the audit deliverable.
- **Privilege & confidentiality:** matter data is privileged; RLS (`app/db/rls.py`) enforces per-tenant (and ideally per-matter) isolation; DMS/data-room contents (UC-3) are `sensitive`; never log document contents; conflict DB must be tenant-private.
- **Data-residency / DPA:** UC-4 handles EU personal data — respect GDPR data-flow mapping; ensure the compliance-monitoring agent does not itself export PII across regions.

## 6. Scale Pattern & Cost Drivers

**Mixed, but *not* the millions-of-filings fan-out of GST/CA.** Two sub-patterns:
- **High-volume-repetitive** — contract review (UC-1) and NDA generation (UC-5) at 100–500 docs/month per legal team; and continuous-monitoring loops (UC-4, UC-6, UC-7, UC-10, UC-11) that poll and fan-out alerts. These scale like the tax verticals but at lower absolute volume.
- **Low-volume-complex** — DD (UC-3, multi-agent parallel review of thousands of data-room docs) and court drafting (UC-12). Few engagements, heavy per-engagement compute + long context.

**Architecture:**
- Per-plan Celery queues + per-tenant bulkhead as elsewhere; the binding constraint here is usually **LLM context/token cost on large documents** (a 40-page contract, a 12,000–50,000-document data room), not portal rate limits.
- UC-3 uses the **multi-agent supervisor** to fan out 5 category sub-agents in parallel — cost scales with data-room size; meter per DD engagement.
- Continuous monitors run on the scheduler/triggers subsystem; dedupe (`app/services/dedup.py`) prevents re-alerting on the same notification.

**Cost drivers:**
- **Token cost dominates** (unlike GST/CA where RPA-time dominates). Large-document review and DD are the expensive operations. Controlled by: prompt compression (`app/agent/prompt_compressor.py`) for long contracts, semantic + LLM-response caching (`app/rag/`) for repeated clause patterns and boilerplate NDA generation, and per-tenant/per-goal budgets (`app/governance/cost.py`).
- **Web-search / research volume** for UC-2/11 — cache aggressively; the same case law and circulars are queried repeatedly across tenants.
- Model routing (`app/agent/model_router.py`) — route mechanical extraction/classification to cheaper models, reserve the strongest model for redline reasoning and research synthesis.

## 7. Decision & Phasing

**Flagship first: UC-1 Contract Review & Risk Red-Lining** — highest frequency, clearest ROI (4–6h→15min, 60% outside-counsel savings), and it is the reference manifest. It requires only Bucket-1 connectors, so it ships fast and seeds the per-tenant clause library that makes UC-5 and UC-3 better over time (data compounding).

**Phasing — this vertical is largely un-gated, so sequence by value, not by connector:**
- **Phase A (weeks 1–4) — Bucket-1 core, immediate revenue:** UC-1 (contract review), UC-5 (NDA/contract generation), UC-2 (research), UC-9 (intake). All document_reader + KB + docusign; no portal dependency.
- **Phase B (weeks 3–8) — continuous-monitoring suite:** UC-7 (litigation calendar), UC-4 (GDPR/SOC2 monitoring), UC-6 (IP monitoring, needs IP-registry HTTP connector), UC-10/UC-11 (employment + regulatory watch). These drive the recurring Professional-tier subscription.
- **Phase C — high-ACV enterprise engagements:** UC-3 (DD, needs DMS connectors + multi-agent hardening), UC-8 (invoice audit, performance-based revenue), UC-12 (court drafting).
- **Phase D — connector-gated tail:** court e-filing RPA (UC-12 submission) and `indian_kanoon` dedicated connector — build lazily per demand; drafting delivers value even without auto-filing.

**Sequencing rule:** keep `complete_signing` / e-file hard-denied until citation-integrity and clause-detection eval suites (`contract-risk-detection-eval`) pass; the agent stays a *drafting/analysis* tool that a lawyer executes.

## 8. KPIs

- **Contract throughput & accuracy:** review time 92min/4–6h → 15min; risk-clause detection precision/recall (eval-suite tracked); % redlines accepted by lawyer without change.
- **Research quality:** memo turnaround 8–16h → 45min; citation-validity rate (target ~100%, zero fabricated citations).
- **DD economics:** DD cycle 6wk → 2wk; document-review cost $500K → $150K per deal.
- **Deadline reliability:** zero missed litigation/IP deadlines (vs 12% IP-renewal miss, 40% of malpractice claims from missed deadlines).
- **Invoice-audit recovery:** ₹ recovered / % of over-billing caught (drives the 20%-of-recovery revenue line).
- **Compliance posture:** audit-prep time 200h → 20h; open compliance-gap count and mean-time-to-remediate.
- **Cost efficiency:** avg token cost per contract reviewed / per DD engagement; cache hit rate on clause + research queries.
- **Commercial:** enterprise ACV, contracts-per-tenant/month, attach rate to CA-firm channel (boutique legal+accounting practices).
