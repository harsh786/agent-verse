# ADR-D23: Media & Publishing Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/23-media-publishing/use-cases.md

## 1. Market & Monetization
- **TAM:** India M&E sector ₹2.3 lakh crore; digital advertising ₹55,000 crore (FY25, 28% CAGR); 80M+ OTT/news subscriptions. Addressable AgentVerse slice = editorial automation + moderation + monetization tooling.
- **Buyer:** Digital editor-in-chief / head of audience development (content + subscription), and ad-ops / revenue head (monetization). Compliance officer for SSMI IT Rules obligations.
- **3 tiers (₹):** Creator ₹9,999/mo (independent journalists, newsletters, podcasters — SEO + social + podcast show notes). Publisher ₹79,999/mo (mid-size digital publishers — all 10 UCs, 1M moderation items, 50k newsletter subs). Enterprise ₹3,49,999/mo (large media houses/OTT — unlimited, 50M+ moderation items, 11 languages, IT Rules 2021 module).
- **Consumption model:** Hybrid. Per-item for moderation (₹0.04/item) and newsletter sends (₹2/send); success-fee for ad yield (5% of incremental revenue); flat SaaS for automation modules.
- **WTP:** High on revenue-linked UCs (ad yield, subscriber retention directly touch P&L). Moderation is a compliance must-buy for SSMIs (₹50L–₹20cr penalty exposure). Content generation is cost-substitution — moderate WTP.
- **Time-to-first-revenue:** Fast (2–4 weeks) for SEO/social/podcast — Bucket-1 connectors, no regulated integration. Moderation and ad-yield require data-pipe integration (4–8 weeks).
- **Monetization note:** Best land-and-expand is SEO+social (Creator tier), upsell to moderation/personalization. Ad-yield success-fee aligns incentives and unlocks enterprise budgets but needs Google Ad Manager access.

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Automated News Article Generation from Data Feeds | Both | 2 | bse_nse_rss (RPA-new), sebi_portal (RPA-new), sports API (New-API), election API (New-API), web_search, code_sandbox, postgres, wordpress/CMS, social APIs, slack | Real-time-monitoring + Research+doc-gen | Yes (always, sub-editor) |
| UC-2 | Content Moderation at Scale | Agent | 1+2 | PhotoDNA/hash-DB (New-API), code_sandbox (NLP 12-lang), NCMEC CyberTipline (New-API), postgres, whatsapp, slack | High-volume-repetitive (stream, <200ms SLA) | Yes (borderline queue) |
| UC-3 | Subscriber Personalization & Engagement | Agent | 1 | event stream (New-API), code_sandbox, postgres, app_push (New-API), smtp/email, CMS | High-volume-repetitive (event stream) | No |
| UC-4 | Podcast Show Notes & Transcripts | Both | 1 | file upload, audio_transcriber (New — Whisper/ASR), code_sandbox, linkedin, web_search, pdf_generator, email, recording-platform webhooks (New-API) | Research+doc-gen | No |
| UC-5 | SEO Content Optimization | Both | 1+2 | Google Search Console (New-API), web_search, CMS, code_sandbox, slack, email, postgres | Research+doc-gen + Approval-gated | Yes (editor for content adds) |
| UC-6 | Audience Analytics & Insights | Agent | 1+2 | GA4 (New-API), Firebase (New-API), mailchimp, Facebook Insights (New-API), Twitter Analytics (New-API), code_sandbox, postgres, slack, email | Real-time-monitoring + Research+doc-gen | No |
| UC-7 | Advertising Yield Optimization | Agent | 2 | Google Ad Manager (New-API), code_sandbox, web_search, slack, postgres, email | Real-time-monitoring | Yes (floor >25%, demand deactivation) |
| UC-8 | Rights & Licensing Management | Agent | 1+2 | CMS/DAM (New-API), web_search, reverse-image-search (New-API), code_sandbox, postgres, pdf_generator, email | Real-time-monitoring + Research+doc-gen | Yes (major legal action) |
| UC-9 | Social Media Content Distribution | Both | 1 | CMS webhook, code_sandbox, web_search, Buffer/Hootsuite (New-API), twitter, linkedin, Instagram Graph (New-API), facebook (New-API), whatsapp, postgres | High-volume-repetitive | Yes (breaking/sensitive only) |
| UC-10 | Newsletter Personalization | Both | 1 | CMS, code_sandbox, postgres, mailchimp, sendgrid (New-API), slack | High-volume-repetitive | Yes (major editions) |

## 3. Connectors Required
- **Existing (Bucket 1, reuse):** web_search (SearXNG), email/smtp, whatsapp, linkedin, mailchimp, twitter, slack, pdf_generator, postgres, code_sandbox. Build cost: ~0 (config only).
- **New-API (moderate build, standard REST/OAuth):** audio_transcriber (Whisper/commercial ASR — reusable across domains), Google Search Console, GA4, Firebase, Facebook/Instagram Graph, Twitter Analytics, Buffer/Hootsuite, sendgrid, app_push, Google Ad Manager, reverse-image-search, CMS/DAM (WordPress exists; Drupal/custom = New-API), recording-platform webhooks (Riverside/Squadcast), PhotoDNA/hash-DB, NCMEC CyberTipline, sports data API, Election Commission API. Build cost: 2–5 days each; Ad Manager and GA4 are heavier (~1 week, OAuth + report schemas).
- **RPA-portal (New):** bse_nse filing RSS/scrape, sebi_portal (SEBI EDGAR/XBRL). Build cost: ~1 week each, brittle — monitor for portal changes.
- **Multimodal note:** UC-2 (image/video moderation), UC-4 (audio) require the embedder/model to support multimodal + a dedicated audio_transcriber. Prioritize audio_transcriber as a shared platform capability.

## 4. Knowledge Collections
Seed slugs: `media-house-style-guides`, `it-rules-2021-ssmi-obligations`, `csam-hash-policies`, `seo-schema-templates`, `subscriber-interest-taxonomy` (200+ topic categories), `programmatic-yield-playbooks`, `rights-license-registry`, `social-platform-format-specs`.
Ingestion recipe: (1) ingest publication's existing style guide + past 12 months of published articles into pgvector for brand-voice grounding; (2) ingest IT Rules 2021 text + NCMEC/CyberTipline procedures as compliance ground truth; (3) seed SEO schema.org templates + "People Also Ask" corpora; (4) build subscriber topic taxonomy from CMS category tree; refresh trending topics on 5-min decay per UC-3.

## 5. Guardrails & Compliance
- **IT Rules 2021 (SSMI):** grievance acknowledgement <24h, resolution <15 days, quarterly transparency report (Rule 4d), Grievance Officer notification on critical content. CSAM → mandatory NCMEC report <24h.
- **Fact-check gate (UC-1):** every number in generated article verified against source data extract; discrepancy blocks publish. Always-on HITL sub-editor before publish.
- **Editorial HITL:** breaking/politically-sensitive social posts (UC-9), content additions (UC-5), major legal action on rights (UC-8), ad floor changes >25% (UC-7).
- **Data residency:** india; AES-256; append-only audit trail; moderation decisions preserved for law enforcement.
- **Hallucination risk:** highest in UC-1 automated journalism — mandatory source-grounding + fact-check + human review. Attribution/defamation risk requires named-entity verification.

## 6. Scale Pattern & Cost Drivers
- **High-volume-repetitive (dominant):** moderation (millions/day, <200ms), personalization + newsletter (per-subscriber fan-out), social distribution. Cost driver = LLM/classifier calls per item — mitigate with layered detection (hash → rules → ML → LLM only for borderline) and semantic caching. UC-2's economics (₹0.04 vs ₹1.50) depend on keeping >95% of items out of the LLM path.
- **Real-time-monitoring:** UC-1 (60s feed polling), UC-6/UC-7 (hourly ingestion). Celery-scheduled.
- **Research+doc-gen:** UC-4 podcast (12-min turnaround), UC-5 SEO, UC-8 rights. Batch, cost-tolerant.
- Primary cost lever: moderation classifier throughput and per-subscriber personalization compute. Ad-yield UC-7 is self-funding (success fee).

## 7. Decision & Phasing
- **Phase 1 (land):** UC-5 SEO, UC-9 social, UC-4 podcast — pure Bucket-1, fast revenue, low compliance surface. Requires shared audio_transcriber build.
- **Phase 2 (expand):** UC-1 news generation, UC-3 personalization, UC-10 newsletter — needs CMS + feed integrations + brand-voice knowledge.
- **Phase 3 (enterprise):** UC-2 moderation (multimodal + IT Rules module), UC-7 ad-yield (Ad Manager), UC-6 analytics, UC-8 rights — highest integration and compliance cost, highest ACV.
- Not among the thinnest domains (10 UCs but rich); **no enrichment flag**. Multimodal (image/video/audio) is the key platform dependency to fund early.

## 8. KPIs
- Article generation time 90min → 8min (UC-1); fact-check discrepancy rate < 0.5%.
- Moderation cost/item ₹1.50 → ₹0.04; clear-violation precision ≥ 99%; IT Rules SLA adherence 100%.
- Subscriber churn −38%; LTV ₹1,400 → ₹2,100; push CTR ×3.2 (UC-3).
- Podcast post-production 4h → 12min (UC-4). SEO organic traffic +28–45% (UC-5).
- Ad CPM +35–70%; fill rate +8–15% (UC-7). Newsletter open 20% → 43% (UC-10).
- Rights recovery ₹30–80L/yr (UC-8). Platform: HITL approval latency, LLM cost per moderated/personalized item.
