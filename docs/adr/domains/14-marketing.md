# ADR-D14: Marketing & Growth Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/14/use-cases.md

## 1. Market & Monetization
- **TAM:** ₹3,200 crore/year of automatable Indian marketing operations spend (source doc); global MarTech orchestration is the larger prize. B2B firms spend ₹1.5–5 crore/year on martech but realize ~30% of its value due to tool fragmentation — AgentVerse is the orchestration layer.
- **Buyer:** CMO / VP Growth / Head of Performance Marketing (economic buyer); Marketing Ops / RevOps lead (champion). Fast bottom-up PLG entry via a single high-ROI agent (SEO factory or ads optimizer), then land-and-expand to a suite.
- **Three tiers (from source, refined):**
  - **Tier 1 — Growth: ₹30,000–60,000/month.** 3–5 agent workflows; Bucket-1 connectors (Google Ads, Meta, HubSpot, email, GA4); 5 seats; HITL on all outbound.
  - **Tier 2 — Scale: ₹1,00,000–2,00,000/month.** Unlimited workflows + full connector library; ML attribution dashboard; 20 seats; autonomous execution for low-risk actions.
  - **Tier 3 — Enterprise: ₹3,50,000+/month.** White-label reporting portal, custom LLM on brand assets, Oracle Eloqua/Adobe integration.
- **Consumption model:** Hybrid. Base seat/workflow subscription + usage overages: per-article (₹600), per-campaign (₹50k influencer), per-report (₹1.2L market-entry), and **% of managed ad spend** (5%, min ₹30k/mo) for UC-9. Consumption meters: LLM tokens (content generation heavy), RPA portal minutes, connector API calls.
- **WTP:** High on measurable-ROI use cases (ads optimization pays for itself at ~₹2.7L/mo saved on ₹10L spend; attribution replaces 4 analyst-days/mo). Moderate on content (commoditized by generic LLM tools — differentiation is publish-loop + ranking monitoring, not raw generation).
- **Time-to-first-revenue:** Fast (weeks). Largely **Bucket-1 connectors** → PLG self-serve motion. SEO Content Factory (UC-2) and Ads Optimization (UC-9) are the wedge: single-connector, obvious ROI, low compliance friction.
- **Monetization note:** UC-9 (ads) supports a performance/% -of-spend model with strong alignment; UC-12 (market-entry intel) is a high-margin one-time report play (₹1.2L delivered in 48h vs ₹5–15L consultant). Content commoditization risk is the main pricing pressure — anchor value on the closed loop (rank monitoring, attribution, auto-refresh), not word count.

## 2. Use Cases -> Product Mapping

| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Multi-Channel Campaign Orchestration | Both | 1 | google_ads, meta_ads, linkedin, mailchimp, hubspot, slack, google_analytics, cms(RPA-new WordPress/Webflow) | Approval-gated | Yes (creative + spend) |
| UC-2 | SEO Content Factory | Both | 1 (+RPA) | web_search, document_reader, cms(RPA-new WordPress/Webflow), ahrefs/semrush(RPA-new), google_search_console(RPA-new) | High-volume-repetitive | Optional (auto-approve >85% conf) |
| UC-3 | Competitor Intelligence & Battle Cards | Both | 1 (+RPA) | web_search, browser-RPA, salesforce/hubspot, slack, g2(RPA-new), linkedin, confluence(RPA-new) | Real-time-monitoring | No (advisory alerts) |
| UC-4 | Social Media Scheduling & Analysis | Both | 1 (+RPA) | linkedin, web_search, slack, buffer/hootsuite(RPA-new), twitter/instagram/youtube(RPA-new) | High-volume-repetitive | Yes (calendar approval, paid boost) |
| UC-5 | A/B Test Setup & Statistical Analysis | Agent | 1 (+RPA) | google_analytics, mixpanel(RPA-new), slack, code-exec, optimizely/vwo(RPA-new), cms(RPA-new) | Research+doc-gen | Yes (SRM/anomaly alerts) |
| UC-6 | Influencer Discovery & Campaign Mgmt | Both | 1 (+RPA) | instagram/youtube/linkedin(RPA-new), email, google_sheets, pdf_generator, web_search | Research+doc-gen | Yes (creator shortlist + outreach) |
| UC-7 | Email Sequence Personalization | Agent | 1 | mailchimp, hubspot, klaviyo(RPA-new), mixpanel(RPA-new), slack, code-exec | High-volume-repetitive | No (autonomous, hot-reply routing) |
| UC-8 | PR Outreach & Media Monitoring | Both | 1 (+RPA) | email, web_search, slack, document_reader, muckrack(RPA-new), google_news_rss(RPA-new), twitter(RPA-new) | Real-time-monitoring | Yes (pitch approval) |
| UC-9 | Google/Meta Ads Optimization | Agent | 1 | google_ads, meta_ads, google_analytics, slack, code-exec, audit-trail | Real-time-monitoring | Yes (creative activation) |
| UC-10 | Marketing Attribution & ROI Reporting | Both | 1 (+RPA) | google_ads, meta_ads, hubspot, salesforce, google_analytics, mailchimp, slack, snowflake/bigquery(RPA-new) | Research+doc-gen | No (reporting) |
| UC-11 | Customer Lifecycle Value Maximization | Both | 1 (+RPA) | hubspot/salesforce, mixpanel(RPA-new), linkedin, email, slack, pdf_generator, code-exec, stripe/razorpay/chargebee(RPA-new), jira | Approval-gated | Yes (CSM reviews retention brief) |
| UC-12 | Local Market Expansion Intelligence | Template | 1 (+RPA) | web_search, browser-RPA (app stores, Google Maps, marketplaces, review sites), meta_ads(library), linkedin, pdf_generator, google_sheets, code-exec, slack | Research+doc-gen | Yes (CMO reviews report) |

## 3. Connectors Required
- **Existing (Bucket-1, reuse as-is):** google_ads*, meta_ads*, linkedin, mailchimp, hubspot, salesforce, slack, google_analytics, web_search (SearXNG), document_reader, pdf_generator, email/gmail, google_sheets, code-exec. (*google_ads / meta_ads: RPA-new fallback if official API connector absent — Meta Ads Library scraping needed for UC-12 regardless.)
- **New-API (build, moderate cost):** klaviyo, mixpanel/amplitude, stripe/razorpay/chargebee (billing for UC-11 LTV), snowflake/bigquery (warehouse for UC-10). Each ~1–2 dev-weeks; well-documented REST APIs.
- **RPA-portal (build, higher cost — no/limited public API or gated):** WordPress/Webflow CMS publish, Google Search Console, Ahrefs/SEMrush, Optimizely/VWO, Buffer/Hootsuite, Twitter/X + Instagram + YouTube posting, G2/Capterra, Muck Rack, app-store rank trackers (App Annie/Sensor Tower), Google Maps business listings, quick-commerce/marketplace scrapers (Zomato/Swiggy/Amazon/Blinkit for UC-12). Each ~2–4 dev-weeks + ongoing selector maintenance; brittle, rate-limited, ToS risk.
- **Build-cost verdict:** ~80% of marketing value is reachable Bucket-1 (fastest PLG). CMS-publish RPA is the one near-universal build needed to close the loop on UC-1/2/4.

## 4. Knowledge Collections (seed slugs + ingestion recipe)
- `brand-guidelines` — ingest brand voice doc, tone rules, banned phrases, logo/asset usage (PDF/Docs → chunk by section).
- `audience-segments` — CRM segment exports + ICP definitions (CSV → structured records).
- `historical-campaign-data` — past campaign briefs, results, learnings (recurring nightly sync from GA4/ads exports).
- `competitor-profiles` — per-competitor positioning, pricing, battle-card baselines (semantic-diff source for UC-3).
- `seo-keyword-corpus` — keyword clusters, SERP snapshots, internal-link graph (weekly refresh for UC-2).
- `content-library` — approved email/social message variants tagged by cohort/funnel-stage (UC-7).
- **Ingestion recipe:** connector-pull → PII scrub (sanitization.py) → chunk (section for docs, row for tabular) → embed (voyage/gemini) → hybrid pgvector+trigram index in KnowledgeStore; nightly Celery refresh for time-sensitive collections (keywords, competitor pages, campaign metrics).

## 5. Guardrails & Compliance
- **HITL is mandatory on all outbound + spend:** content publish (`cms.publish|email.send_blast`), ad spend changes (`google-ads.*|meta-ads.*` above threshold), paid boosts, influencer outreach send. Bounded-autonomous only for low-risk internal actions (reporting, scoring, draft generation).
- **Consent & anti-spam:** email blasts must respect opt-in / unsubscribe; India DPDP Act consent for CRM PII in UC-7/UC-11; no scraping of PII beyond ToS.
- **Brand safety:** all generated creative checked against `brand-guidelines` banned-phrase list; influencer brand-safety scoring (UC-6).
- **Ad-platform ToS:** RPA against ad portals risks account suspension — prefer official APIs; rate-limit RPA; audit-trail every spend change with data rationale (UC-9 requirement).
- **Cost budgets:** per-goal LLM budget caps (content generation is token-heavy); governance/cost.py enforces per-tenant ceilings. Attribution/PII data residency = India for regulated clients.

## 6. Scale Pattern & Cost Drivers
- **Dominant patterns:** High-volume-repetitive (UC-2 content, UC-7 email, UC-4 social) and Real-time-monitoring (UC-3, UC-8, UC-9). Research+doc-gen for the high-margin report plays (UC-10, UC-12).
- **Cost drivers:** (1) **LLM tokens** — content generation (UC-2 at 100 articles/mo) and personalization dominate spend; use prompt_compressor + SemanticCache aggressively. (2) **RPA portal minutes** — CMS publish, social posting, marketplace scraping; brittle and the main opex/maintenance sink. (3) **Connector API calls** — ads/analytics polling every 6h (UC-1) and hourly monitoring (UC-8).
- **Efficiency levers:** LLM response cache for repeated SEO briefs; per-plan Celery queue routing so enterprise attribution jobs don't starve free-tier content jobs; batch ad-metric pulls.

## 7. Decision & Phasing
- **Phase 1 (wedge, all Bucket-1, PLG):** UC-9 Ads Optimization + UC-2 SEO Content Factory + UC-7 Email Personalization. Highest ROI clarity, lowest connector build, self-serve onboarding. Requires only CMS-publish RPA build.
- **Phase 2 (suite / land-expand):** UC-1 Campaign Orchestration, UC-3 Competitor Intel, UC-4 Social, UC-10 Attribution. Adds monitoring + warehouse connectors.
- **Phase 3 (high-margin + enterprise):** UC-11 Lifecycle/LTV (needs billing connectors), UC-12 Market-Entry Intel (report product), UC-5 A/B, UC-6 Influencer. White-label + custom-LLM tier.
- **Rationale:** Ship the ROI-obvious, single-connector agents first to prove value and fund the RPA/warehouse build-out for the suite.

## 8. KPIs
- **Product:** campaign launch time (3 weeks → 4 days), CPL reduction (−28%), ROAS improvement (+40–65% in 90d), SEO article volume (10 → 100/mo), email CTR (2.8% → 9.4%), attribution refresh latency (2–4 weeks → daily).
- **Business:** MRR by tier, % managed ad spend, articles/reports metered, seat expansion, gross retention.
- **Trust/quality:** HITL approval rate & override rate on generated creative, brand-guideline violation catches, ad-account suspension incidents (target 0), eval-suite score on content quality.
- **Efficiency:** LLM cost per article/campaign, RPA success rate & selector-break MTTR, SemanticCache hit rate.
