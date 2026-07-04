# ADR-D03: DevOps & Platform Engineering Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/03-devops/use-cases.md

## 1. Market & Monetization
- **TAM:** Global DevOps ₹1.07 lakh-crore (2024) → ₹4.84 lakh-crore (2032), CAGR 20.7%. Problem cost: outages ₹4,70,000/min (Gartner), cloud waste ₹2.23 lakh-crore/yr, on-call burnout drives 35% of SRE attrition (₹15–25L/replacement).
- **Buyer persona:** Head of Platform / SRE Lead / VP Infra at cloud-native and production-SLA companies; economic buyer is the eng leader carrying the on-call pager and the cloud bill.
- **Pricing tiers (₹):**
  - **DevOps Starter — ₹42,000/mo:** incident triage, CI/CD failure analysis, post-deploy verification; ≤5 services; 3,000 goals/mo; Slack.
  - **Platform Pro — ₹1,25,000/mo:** + cloud cost optimization, vuln scanning, runbook execution; ≤25 services; 15,000 goals/mo; custom runbook library.
  - **Enterprise SRE — ₹4,20,000+/mo:** + capacity planning, SLA prediction, secret rotation; unlimited services; custom alert routing & HITL; P1 triaged <60s; SOC2 audit trail.
- **Consumption add-on model:** per-service metering (₹25,000/mo per 10 services vuln scanning; ₹59,000/mo per cluster log analysis; ₹2,50,000/mo multi-cluster) + **performance-based cloud-cost tier (10% of monthly savings achieved)** — a uniquely alignable pricing lever. Module add-ons: ₹84,000/mo SLA monitoring, ₹50,000/mo runbook automation, ₹42,000/mo drift/capacity, ₹33,000/mo secrets.
- **Willingness-to-pay:** Very high — one prevented P1-hour (~₹1.7 crore) or a 15–25% cloud-bill cut dwarfs the subscription; the 10%-of-savings model sells itself.
- **Time-to-first-revenue:** ~1 week — PagerDuty + Slack + CloudWatch triage live in week 1 (Bucket-1).
- **Monetization note:** **Fast-cash Bucket-1 core with New-API depth-plays.** Ingestion/action on aws, gcp, datadog, pagerduty, github/gitlab, slack is all catalog. Deeper autonomy (runbook exec, drift, post-deploy, secrets) needs **Kubernetes and infra New-API connectors** — high-value, connector-gated expansion, not a landing blocker.

## 2. Use Cases -> Product Mapping
| UC-N | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|------|-------|----------|--------|------------|---------------|-------|
| UC-1 | Automated Incident Response & Triage | Marketplace Agent | 1 | pagerduty, datadog, github, aws, slack; **New-API:** kubernetes | Real-time-monitoring | Yes (remediation approval) |
| UC-2 | CI/CD Pipeline Failure Analysis | Marketplace Agent | 1 | github/gitlab, jira, slack + code-sandbox; **New-API:** Jenkins | High-volume-repetitive | No |
| UC-3 | Cloud Cost Optimization Analysis | Marketplace Agent | 1 | aws (Cost Explorer, CloudWatch), jira, slack | Research+doc-gen | Yes (resource deletes gated) |
| UC-4 | Security Vulnerability Scanning & Remediation | Marketplace Agent | 1 | github, web_search (NVD), slack, jira + code-sandbox | High-volume-repetitive | No (opens patch PRs) |
| UC-5 | Automated Runbook Execution | Marketplace Agent | 1 | aws, pagerduty, confluence, slack + code-sandbox; **New-API:** kubernetes | Approval-gated | Yes (each destructive step) |
| UC-6 | Capacity Planning & Auto-Scaling Analysis | Both | 1 | aws (CloudWatch), jira, slack; **New-API:** Prometheus/Grafana | Research+doc-gen | Yes (pre-scaling approval) |
| UC-7 | SLA Breach Prediction & Prevention | Marketplace Agent | 1 | datadog, pagerduty, slack, aws (Route53/ALB); **New-API:** Prometheus | Real-time-monitoring | Yes (traffic diversion) |
| UC-8 | Log Analysis & Anomaly Detection | Marketplace Agent | 1 | aws (CloudWatch Logs), pagerduty, slack; **New-API:** Elasticsearch, Splunk, Grafana | Real-time-monitoring | No |
| UC-9 | Infrastructure Drift Detection & Remediation | Marketplace Agent | 1 | aws, gcp, github (Terraform), jira, slack (CloudTrail); **New-API:** kubernetes, Azure | Real-time-monitoring | Yes (security-critical drift) |
| UC-10 | On-Call Handoff Automation | Both | 1 | pagerduty, datadog, github, slack | Research+doc-gen | No |
| UC-11 | Post-Deployment Verification | Marketplace Agent | 1 | github, datadog, slack + Playwright (smoke); **New-API:** ArgoCD, kubernetes | Real-time-monitoring | Yes (rollback approval) |
| UC-12 | Certificate & Secret Rotation Automation | Marketplace Agent | 1 | aws (ACM), slack; **New-API:** HashiCorp Vault, kubernetes, Let's Encrypt (HTTP) | Approval-gated | Yes (rotation is state-changing) |

## 3. Connectors Required
- **Existing (catalog · Bucket 1 · no build):** pagerduty, datadog, aws (Cost Explorer, CloudWatch, ACM, Route53/ALB, CloudTrail, EC2/RDS), gcp, github, gitlab, jira, confluence, slack, web_search. Plus platform-native **Docker code-execution sandbox** (kubectl/CLI/diagnostic scripts) and **Playwright** (smoke tests) — native, not connectors.
- **New-API (build):** **Kubernetes** (high value, appears in UC-1/5/9/11/12 — build first), Prometheus/Grafana, Elasticsearch, Splunk, HashiCorp Vault, ArgoCD, Jenkins, Azure, Let's Encrypt/certbot (via HTTP tool). Medium build cost each; Kubernetes is the highest-leverage single build.
- **RPA-portal (Bucket 2):** none.

## 4. Knowledge Collections
- `runbooks` — operational runbooks ingested from Confluence/runbook store, parsed step-by-step with read-only vs state-changing classification (UC-5).
- `architecture-diagrams` + `service-dependencies` — service maps for blast-radius assessment (UC-1).
- `past-incidents` — resolved incident timelines & post-mortems for correlation RAG.
- `rate-cards` / cloud pricing references — for cost attribution (UC-3).
- **Ingestion recipe:** crawl the customer's Confluence/wiki and incident history; ingest customer-owned runbooks and diagrams only; fetch CVE/NVD and cloud-pricing data live via web_search.

## 5. Guardrails & Compliance
- **Regulated:** SOC2 / ISO27001 / CIS Benchmarks evidence generation is a core buying trigger (drift reports, patch SLAs, audit trail); financial-services and healthcare-IT tenants add sector controls.
- **Mandatory HITL gates:** all production changes (`kubernetes.*|aws.ec2.*|aws.rds.*` → require_approval), every destructive runbook step, resource deletion (cost cleanup), rollback triggers, traffic diversion, pre-scaling, and secret/cert rotation.
- **Fail-closed policy:** `*.delete|*.drop` denied outright (no database deletes); auto-patches must pass sandbox tests before PR; the agent triages and diagnoses autonomously but pauses at every state-changing boundary. On verification failure after a step (UC-5), pause and alert rather than continue.
- **Audit needs:** timestamped log of every executed command and its output written to the incident ticket; SOC2 audit trail bundled at Enterprise tier; P1 triage-time SLA (<60s) tracked.

## 6. Scale Pattern & Cost Drivers
- **Dominant shape:** Real-time-monitoring (continuous polling/streaming: incidents, SLA, logs, drift, post-deploy) with Approval-gated remediation and scheduled Research+doc-gen (weekly cost/capacity, daily scans).
- **Expected goal volume:** high and spiky — log/anomaly polling every 15 min and 5-min SLA polling generate steady background goals; incident storms spike sharply.
- **Cost drivers:** high-frequency polling goals; large log-window ingestion (500GB–50TB/day systems); multi-service parallel diagnosis; sandbox compute for diagnostics.
- **Caching/routing levers:** dedup + circuit-breakers on repeated alerts (platform reliability layer); baseline/anomaly pre-filtering so the LLM only sees deviations, not raw logs; cheap model for classification/triage, strong model for root-cause synthesis and remediation planning; cache diagnosis for recurring incident signatures via `past-incidents` RAG.

## 7. Decision & Phasing
- **Flagship first:** **UC-1 Automated Incident Response & Triage** — the headline "tireless first responder," live week 1 on pagerduty + datadog + aws + slack (read-only diagnosis before Kubernetes remediation lands).
- **Fast-follows (Bucket-1):** UC-2 CI/CD failure analysis, UC-11 post-deploy verification, UC-3 cloud cost optimization (sell on 10%-of-savings), UC-10 on-call handoff.
- **Connector-gated (build Kubernetes first):** UC-5 runbook execution, UC-9 drift remediation, UC-12 secret rotation (need k8s + Vault), UC-7/UC-8 (Prometheus/Splunk/Elasticsearch), UC-6 capacity (Prometheus).

## 8. KPIs
- **Adoption:** services under monitoring per tenant; % of P1 incidents auto-triaged; runbooks migrated & executed via agent; cloud accounts connected.
- **Reliability:** `incident-resolution-eval` pass %; MTTD/MTTR reduction (target 197→8 min MTTD); RPA/runbook step success %; post-deploy false-rollback rate; drift-detection precision.
- **Cost/goal:** tokens per incident diagnosis; polling-goal cost/service/day; sandbox minutes per runbook.
- **Revenue/tenant:** tier MRR + per-service/per-cluster add-ons + realized cloud-savings share (10%); Starter→Pro→Enterprise expansion tracking connector adoption (k8s = expansion unlock).
