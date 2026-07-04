# ADR-D15: Cybersecurity Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/15/use-cases.md

## 1. Market & Monetization
- **TAM:** Indian cybersecurity market ₹25,000 crore (2024) → ₹80,000 crore (2030). Structural tailwind: 3.4M unfilled security roles globally (ISC² 2024) and 11,000+ alerts/day/enterprise — the SOC labour gap is the buying trigger.
- **Buyer:** CISO (economic buyer); SOC Manager / GRC lead / Head of Security Engineering (champions). Also **MSSPs** as a channel/multi-tenant reseller. Top-down, security-approval-heavy sale (not PLG).
- **Three tiers (from source):**
  - **Tier 1 — SOC Starter: ₹80,000/month.** SIEM triage + vuln prioritization + phishing analysis; ≤5,000 alerts/day, 2 SIEM integrations.
  - **Tier 2 — Security Platform: ₹2,00,000/month.** Full suite + IR, CSPM, access reviews, dark-web; unlimited alerts/assets; regulatory notification automation; HITL customization.
  - **Tier 3 — Enterprise SOC: ₹4,00,000+/month.** Custom playbooks, ML risk models, multi-tenant for MSSPs, P1 triage <60s SLA, dedicated security architect, SOC2-grade audit trail.
- **Consumption model:** Subscription anchored on **alert/asset/identity volume bands** (alerts/day, assets scanned, identities reviewed, vendors monitored). Add-on metering: sandbox detonations (UC-3), zero-day response events (UC-10 standalone ₹80k/mo). UC-11 vendor risk priced per-vendor-block (₹8k / 25 vendors). One-time setup fees for custom questionnaire/policy templates.
- **WTP: High.** Value is denominated in avoided breach cost (₹4.2 crore avg, IBM India 2024), avoided regulatory penalties (CERT-In 6h mandate), and hard analyst-hour savings (UC-11: ₹1.3 crore/yr; UC-12: ₹1.1 crore/yr). Security budgets are defensible and growing.
- **Time-to-first-revenue:** Moderate. Mostly **Bucket-1** (security tools have real APIs), but sales cycle is long due to security review, data-sensitivity, and the need for HITL trust before autonomy. Wedge = SIEM triage (UC-1) — immediate, measurable MTTI collapse.
- **Monetization note:** This is the **highest-WTP vertical** in the assigned set. Real-time SIEM triage is the flagship (MTTI 84min → 3min). Success-fee models don't fit security (no clean "recovery" metric except UC-11 breach-risk reduction) — pure subscription + volume bands. MSSP multi-tenancy (Tier 3) is a force-multiplier revenue channel.

## 2. Use Cases -> Product Mapping

| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | SIEM Alert Triage & Enrichment | Both | 1 | splunk, sentinel(new-API), qradar(new-API), virustotal(new-API), shodan(new-API), abuseipdb(new-API), active_directory(new-API), slack | Real-time-monitoring | No (auto-triage; escalates) |
| UC-2 | Vulnerability Scan & Prioritization | Agent | 1 | tenable/qualys/rapid7(new-API), cisa_kev(new-API), jira, servicenow(new-API), slack, code-exec | High-volume-repetitive | No (advisory; SLA escalation) |
| UC-3 | Phishing Analysis & Auto-Remediation | Agent | 1 | email/gmail, virustotal(new-API), urlscan(new-API), anyrun-sandbox(new-API), proofpoint/mimecast(new-API), splunk | Real-time-monitoring | Yes (confirm remediation scope) |
| UC-4 | Access Review & Recertification | Both | 1 | active_directory(new-API), azure_ad(new-API), okta(new-API), sailpoint(new-API), slack, email, servicenow(new-API), code-exec | Approval-gated | Yes (manager retain/revoke) |
| UC-5 | Dark Web Monitoring | Agent | 1 | recorded_future(new-API), intel471(new-API), spycloud(new-API), active_directory(new-API), email, slack, audit-trail | Real-time-monitoring | Yes (CISO approves remediation) |
| UC-6 | Incident Response Playbook Execution | Both | 1 | splunk/sentinel, crowdstrike/sentinelone(new-API), active_directory(new-API), servicenow(new-API), slack, email | Approval-gated | Yes (IC approves destructive) |
| UC-7 | SOC2/ISO27001 Compliance Gap Assessment | Both | 1 | splunk(SIEM), iam(new-API), aws, gcp, azure_config(new-API), hr(new-API), jira, document_reader, pdf_generator | Research+doc-gen | No (task creation) |
| UC-8 | Security Awareness Training Content | Template | 1 | email, slack, splunk(user risk), web_search, pdf_generator | High-volume-repetitive | Optional |
| UC-9 | Cloud Security Posture Mgmt (CSPM) | Agent | 1 | aws (Security Hub/Config), gcp (SCC), azure(new-API), jira, slack | Real-time-monitoring | Yes (auto-remediate w/ approval) |
| UC-10 | Zero-Day Patch Prioritization | Agent | 1 | nvd(new-API), cisa_kev(new-API), cmdb(new-API), ansible(new-API), sccm(new-API), jira, pagerduty, slack, email | Approval-gated | Yes (maintenance window approval) |
| UC-11 | Third-Party Vendor Risk Assessment | Both | 1 (+RPA) | email, web_search, shodan(new-API), haveibeenpwned(new-API), virustotal(new-API), securityscorecard/bitsight(new-API), browser-RPA (CERT-In portal), document_reader, pdf_generator, servicenow/jira, slack, audit-trail, code-exec | Research+doc-gen | Yes (GRC reviews Critical/High) |
| UC-12 | Security Policy Generation & Management | Both | 1 (+RPA) | confluence/sharepoint(new-API), vanta/drata/onetrust(new-API), web_search, document_reader, pdf_generator, email, docusign(new-API), jira/servicenow, slack, audit-trail, splunk, code-exec | Research+doc-gen | Yes (multi-tier policy approval) |

## 3. Connectors Required
- **Existing (Bucket-1, reuse):** splunk, datadog, sentry, pagerduty, aws, gcp, slack, email/gmail, web_search, document_reader, pdf_generator, jira, postgresql, elasticsearch, code-exec.
- **New-API (build — most are well-documented enterprise REST/GraphQL APIs, ~1–2 wks each):** Microsoft Sentinel, IBM QRadar, VirusTotal, Shodan, AbuseIPDB, Active Directory / Azure AD / Okta / SailPoint (IAM), Tenable/Qualys/Rapid7, CISA KEV + NVD, ServiceNow, CrowdStrike/SentinelOne (EDR), URLScan.io, Any.run sandbox, Proofpoint/Mimecast, Recorded Future/Intel471/SpyCloud (dark-web TI), HaveIBeenPwned, SecurityScorecard/Bitsight, AWS Config/Security Hub, GCP SCC, Azure Security Center/Policy, Ansible, SCCM, Vanta/Drata/OneTrust (GRC), Confluence/SharePoint, DocuSign. High volume of integrations but low individual risk — security tools expose real APIs.
- **RPA-portal (build, limited):** CERT-In portal (breach notification / advisories lookup), regulatory-source scraping (RBI/SEBI/MeitY circulars for UC-12 continuous monitoring). Dark-web *crawling* is handled via TI-vendor APIs, not raw RPA. **RPA is the exception here, not the rule** — this vertical is API-first.
- **Build-cost verdict:** Breadth of connectors is the main investment, not brittleness. Prioritize the SIEM + TI + IAM cluster (unlocks UC-1/3/4/5/6). CERT-In RPA is the only true portal build.

## 4. Knowledge Collections (seed slugs + ingestion recipe)
- `mitre-attack` — full ATT&CK tactics/techniques for UC-1 mapping (static import, versioned refresh).
- `incident-playbooks` — SOAR-style playbooks per incident type (ransomware/ATO/exfil/DDoS) for UC-6.
- `asset-criticality-map` + `network-topology` — CMDB export: internet-facing flag, data classification, owner (nightly sync).
- `threat-intel-iocs` — rolling IOC blocklists with 30-day TTL (UC-3), TI-feed ingestion.
- `compliance-frameworks` — SOC2 CC, ISO27001:2022 Annex A (93 controls), NIST CSF 2.0, PCI-DSS v4, DPDP Act, RBI Cyber Framework — control text + evidence-mapping (UC-7, UC-12).
- `policy-templates` — SANS/NIST SP800/ISO27002 authoritative templates for UC-12 generation.
- `vendor-questionnaire-templates` — tiered 80/40/15-question sets aligned to DPDP/RBI TPCRM (UC-11).
- `past-incident-database` — closed incidents with RCA for pattern reuse.
- **Ingestion recipe:** framework/standard docs chunked by control-ID; CMDB/IAM via connector nightly sync; IOC feeds streamed with TTL expiry; all reasoning written to **immutable audit-trail** (governance/audit.py) — non-negotiable for this vertical.

## 5. Guardrails & Compliance
- **Destructive-action policy (deny/approve):** `never-delete-forensic-evidence` (deny `*.delete|*.purge`); asset isolation, account deletion/reset, patch deployment, SIM/session revocation all `require_approval` (Incident Commander / CISO / asset-owner). Bounded-autonomous for enrichment, scoring, triage, ticketing, reporting.
- **Regulatory automation, human-gated:** CERT-In 6-hour incident notification (UC-6), DPDP Act 72h breach-processor notification (UC-12) — agent drafts + tracks deadline; human approves submission.
- **Auditability:** every triage decision, revocation, and policy approval → immutable, 7-year-retention audit trail with reviewer identity + timestamp + rationale (SOC2/ISO evidence requirement).
- **Data sensitivity:** credentials/IOCs/PII hashed in logs; least-privilege service accounts per connector (vault.py encrypted store); RLS tenant isolation critical for MSSP multi-tenancy.
- **Anti-automation-abuse:** rate-limit RPA on CERT-In/regulatory portals; verifier confidence threshold high (≥0.92) before auto-close of alerts to avoid missing true positives.

## 6. Scale Pattern & Cost Drivers
- **Dominant patterns:** Real-time-monitoring (UC-1, UC-3, UC-5, UC-9 — the always-on SOC core) and Research+doc-gen (UC-7, UC-11, UC-12 — compliance/GRC). Approval-gated for IR/access/patch (UC-4, UC-6, UC-10).
- **Cost drivers:** (1) **Event throughput** — 25,000 alerts/day (UC-1) demands cheap-model triage + heavy noise filtering before any LLM touches an alert; model_router to Haiku-class for enrichment, escalate to stronger model only on suspicious. (2) **Sandbox detonations** (UC-3) — metered external cost. (3) **Continuous scans** — CSPM daily, vendor weekly, dark-web continuous. (4) **LLM reasoning** on RCA/policy drafting.
- **Efficiency levers:** SemanticCache + LLM response cache on repeated enrichment (same IOC seen many times); circuit breakers on TI-vendor APIs; per-plan Celery queues so MSSP enterprise tenants aren't starved; deduplication of correlated alerts (reliability/dedup) before LLM.

## 7. Decision & Phasing
- **Phase 1 (flagship wedge):** UC-1 SIEM Triage + UC-2 Vuln Prioritization + UC-3 Phishing — the "SOC Starter" tier. Proves MTTI collapse; builds the SIEM+TI+email connector cluster.
- **Phase 2 (security platform):** UC-6 Incident Response, UC-9 CSPM, UC-4 Access Review, UC-5 Dark Web. Adds EDR/IAM/cloud-posture connectors; introduces destructive-action HITL playbooks.
- **Phase 3 (GRC + enterprise/MSSP):** UC-7 Compliance Gap, UC-11 Vendor Risk, UC-12 Policy Management, UC-10 Zero-Day. GRC-platform connectors, CERT-In RPA, multi-tenant MSSP mode, custom playbooks/ML risk models.
- **Rationale:** Real-time SOC value first (fastest trust + ROI), then autonomous response (needs earned trust for destructive actions), then the high-margin recurring GRC layer that locks in compliance-driven retention.

## 8. KPIs
- **Product:** MTTI (84min → <3min), MTTC (73 days → <4h contained types), false-positive rate (45%→<8%), critical-vuln dwell time (47→5 days), phishing analysis (20min→90s), access review cycle (6–8wk→5–7d), CSPM compliance score (40%→85% in 60d), CERT-In deadline compliance (100%).
- **Business:** MRR by tier + volume band, vendor-blocks metered, MSSP tenant count, net retention (compliance stickiness).
- **Trust/quality:** auto-close accuracy (missed-true-positive rate → 0), HITL override rate on destructive actions, audit-trail completeness, eval-suite IR quality score.
- **Risk:** breaches attributable to agent miss (target 0), zero-day affected-system identification time (2–3d → 2–4h).
