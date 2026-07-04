# ADR-D02: Software Engineering Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/02-software-engineering/use-cases.md

## 1. Market & Monetization
- **TAM:** Developer-productivity/SDLC tooling — anchored by the cost of the problem: developers spend 42% of time on non-coding tasks, code review costs ₹6.7 lakh-crore/yr globally, technical debt $1.52T/yr. Developer time costs ₹8,000–20,000/hour, so every saved hour compounds.
- **Buyer persona:** Eng Manager / VP Engineering / CTO at 5–500-dev orgs; bottom-up champion is a tech-lead who installs the review bot on one repo. PLG-native.
- **Pricing tiers (₹):**
  - **Developer Tools Starter — ₹7,999/mo (≤5 devs):** code review bot, PR descriptions, test generation; 1 repo; 2,000 goals/mo.
  - **Engineering Team Pro — ₹42,000/mo (≤20 devs):** + vuln scanning, tech-debt analysis, codebase Q&A; unlimited repos; 20,000 goals/mo; Slack.
  - **Enterprise Engineering — ₹2,50,000+/mo:** full suite + fine-tuning on company codebase; SLA-backed <5 min review; custom guardrails; SSO + audit.
- **Consumption add-on model:** per-seat is primary (₹8,000/dev/mo review, ₹3,200/dev/mo PR-desc standalone, ₹17,000/dev/mo test-gen); per-repo add-ons (₹12,500/repo/mo vuln scan, ₹17,000/mo bug triage per repo). Goal-cap overages billed.
- **Willingness-to-wtp:** Very high and self-justifying (one senior-hour saved/week/dev covers the seat many times over); frictionless credit-card PLG entry at ₹7,999.
- **Time-to-first-revenue:** Days — code-review bot goes live on one pilot repo in week 1 on pure GitHub/Slack (Bucket-1).
- **Monetization note:** **Fast-cash Bucket-1 beachhead.** Almost every UC runs on catalog connectors (github/gitlab, jira, linear, confluence, slack, web_search, pagerduty, aws) plus the platform's own Docker code-execution sandbox. No connector build gates the core product — this is the strongest PLG land-and-expand of the five domains.

## 2. Use Cases -> Product Mapping
| UC-N | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|------|-------|----------|--------|------------|---------------|-------|
| UC-1 | Automated Code Review with Contextual Feedback | Marketplace Agent | 1 | github, slack, web_search + code-sandbox | High-volume-repetitive | Yes (merge gated per manifest) |
| UC-2 | Intelligent Bug Triage & Reproduction | Marketplace Agent | 1 | jira/linear, github, slack + code-sandbox | High-volume-repetitive | No |
| UC-3 | PR Description & Changelog Generation | Both | 1 | github, jira, web_search | High-volume-repetitive | No (author accepts draft) |
| UC-4 | Technical Debt Analysis & Prioritization | Marketplace Agent | 1 | github, jira, pagerduty, slack + code-sandbox | Research+doc-gen | No |
| UC-5 | Automated Documentation Generation from Code | Both | 1 | github, confluence + code-sandbox; **New-API:** Notion (optional) | Research+doc-gen | No (opens PRs) |
| UC-6 | Dependency Vulnerability Scanning & Patch Management | Marketplace Agent | 1 | github, web_search (NVD), slack, pagerduty + code-sandbox | Real-time-monitoring | No (opens patch PRs) |
| UC-7 | Sprint Velocity Analysis & Planning Assistant | Both | 1 | jira/linear, slack, google_calendar | Research+doc-gen | No |
| UC-8 | Automated Test Case Generation | Both | 1 | github + code-sandbox | High-volume-repetitive | No |
| UC-9 | Incident Post-Mortem Generation | Both | 1 | pagerduty, datadog, github, slack, jira/confluence | Research+doc-gen | No |
| UC-10 | Codebase Q&A (Instant Architecture Expert) | Both | 1 | github, slack + knowledge base | Research+doc-gen | No |
| UC-11 | Release Notes Generation | Both | 1 | github, jira, slack | Research+doc-gen | Yes (PM approval before publish) |
| UC-12 | API Contract Testing & Breaking Change Detection | Both | 1 | github, slack, aws (API Gateway) + code-sandbox | High-volume-repetitive | Yes (block merge on unacked break) |

## 3. Connectors Required
- **Existing (catalog · Bucket 1 · no build):** github, gitlab, jira, linear, confluence, slack, web_search, pagerduty, datadog, aws (API Gateway, CloudWatch). Plus platform-native **Docker code-execution sandbox** (pylint/eslint/mypy/bandit, `npm/pip audit`, test runs) — not an external connector.
- **New-API (build; low–medium cost, optional):** Notion (Confluence covers the primary path); Jenkins/GitLab-CI for CI-log fetch beyond GitHub Actions (shared with DevOps domain).
- **RPA-portal (Bucket 2):** none required.
- **Build-cost flag:** effectively zero gating build — ship the full suite on existing connectors.

## 4. Knowledge Collections
- `codebase-conventions` — team style guide, naming, lint config, PR checklist.
- `security-policies` — approved-dependency rules, secret-handling standards, OWASP checklist.
- `architecture-decisions` — existing ADRs and design docs.
- `resolved-bugs` — historical issues + resolutions for triage RAG (UC-2).
- `codebase-index` — full source ingested by language/module for Codebase Q&A (UC-10), re-ingested on merge via webhook.
- **Ingestion recipe:** crawl the customer's repos and wiki (Confluence/Notion); ingest customer-owned code and docs only; pull CVE/NVD data live via web_search rather than storing.

## 5. Guardrails & Compliance
- **Regulated:** No statutory regime, but SOC2/ISO27001 evidence generation is a buying trigger (test coverage, vuln remediation SLAs, audit trail).
- **Mandatory HITL gates:** PR merge (`github.merge_pull_request` → require_approval), blocking merges on unacknowledged breaking API changes (UC-12), publishing customer-facing release notes (UC-11).
- **Fail-closed policy:** `github.push_commit` denied — the agent proposes via PR, never commits directly to protected branches. Auto-generated patches (UC-6) must pass the sandbox test suite before a PR is opened; if tests fail, no PR.
- **Audit needs:** full trail of every review verdict, patch, and merge-gate decision for SOC2/SSO-backed enterprise tenants.

## 6. Scale Pattern & Cost Drivers
- **Dominant shape:** High-volume-repetitive event-driven (per-PR review, per-bug triage, per-commit test-gen) with scheduled Research+doc-gen (weekly debt/vuln scans, post-mortems).
- **Expected goal volume:** highest per-tenant of the five domains — a 20-dev team at 20+ PRs/day easily consumes the 20,000-goal Pro cap; enterprise is uncapped.
- **Cost drivers:** large-diff and full-file context tokens (code review, codebase Q&A over 50K+ LOC); repeated re-ingestion on merge; sandbox compute minutes for test/patch runs.
- **Caching/routing levers:** prompt compression on large diffs; LLM-response cache for unchanged files; route mechanical checks (style/lint) to static tools in-sandbox (no LLM) and reserve the LLM for logic/security reasoning; tiered model routing — cheap model for triage/labels, strong model for security and architecture verdicts; incremental (diff-aware) re-ingestion.

## 7. Decision & Phasing
- **Flagship first:** **UC-1 Automated Code Review** — the wedge product, live on one repo in week 1, self-serve at ₹7,999. Drives seat expansion.
- **Fast-follows (all Bucket-1):** UC-3 PR descriptions and UC-8 test generation (bundle into Starter), then UC-6 vuln scanning + UC-4 tech-debt + UC-10 codebase Q&A (Pro tier).
- **Later:** UC-9 post-mortems and UC-12 breaking-change detection (overlap with DevOps suite); enterprise codebase fine-tuning.
- **Connector-gated items:** none — Notion and Jenkins are optional niceties, not blockers.

## 8. KPIs
- **Adoption:** repos with review bot enabled; % of PRs reviewed by agent within SLA (<5 min); seats activated per tenant; codebase-Q&A queries/dev/week.
- **Reliability:** `code-review-quality-eval` pass %; false-positive rate on review comments (drives trust/churn); auto-patch PR merge rate; generated-test pass-on-first-run %.
- **Cost/goal:** avg tokens per PR review; sandbox minutes per test/patch run; re-ingestion cost/merge.
- **Revenue/tenant:** seats × tier MRR + per-repo add-ons; Starter→Pro conversion (unlocked by vuln/debt/Q&A value); enterprise fine-tuning uplift.
