# ADR-D35: Fashion & Apparel Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/35-fashion-apparel/use-cases.md

## 1. Market & Monetization
- **TAM.** ₹7.2 lakh crore apparel market; 45M jobs (2nd-largest employer); apparel/textile exports ₹2.07 lakh crore (FY23-24). D2C growing 32% CAGR to ₹1.2 lakh crore by 2027. Serviceable: D2C fashion brands, e-commerce private labels, ethnic-wear brands, and 8,000+ MSME garment clusters + AEPC exporters. Brands now launch 18–24 micro-collections/yr (up from 2–4).
- **Buyer.** Founder / Head of Merchandising / Brand Manager for D2C; Sourcing/Production Manager for manufacturing; Export/Compliance Head for exporters; Sustainability lead for ESG-gated brands. Two distinct sub-buyers: **D2C growth** (trend/launch/influencer/personalisation) and **supply-chain/export** (sourcing/sample/export/sustainability).
- **3 tiers (₹).**
  - **Startup (₹10–50cr rev):** ₹15,000/month — trend forecasting + personalised styling + influencer (≤50); 3 active UCs.
  - **Growth (₹50–500cr rev):** ₹55,000/month — all UCs, sample tracking, export suite, sustainability (≤20 supply partners), Shopify + 3 marketplaces, unlimited HITL.
  - **Enterprise (conglomerate/exporter):** ₹2.0 lakh/month — multi-brand, full ICEGATE/DGFT export, Higg/GRI, white-label B2B buyer portal, ERP API, 99.5% SLA.
- **Consumption model.** Monthly subscription + per-event/usage fees (₹30,000/launch, ₹3,500/sourcing cycle, ₹0.80/personalised WhatsApp message) + success fees (0.5% of recovered markdown revenue). Usage-based messaging aligns with D2C send volume.
- **WTP.** High and margin-anchored: markdown optimisation recovers ₹6–11cr on ₹80cr inventory; fabric sourcing cuts 12–19% of COGS (fabric = 45–55% of garment cost); export compliance replaces ₹12–20L/yr consultant spend and unlocks ESG-gated orders worth ₹2–15cr.
- **Time-to-first-revenue.** Fast for D2C wedge (trend + styling + influencer land the Startup tier in weeks on Shopify). Export/sustainability tier is slower (multi-tier supply-chain onboarding).
- **Monetization note.** The D2C-growth cluster (UC-1/5/7/10) is the low-friction, high-velocity wedge on Shopify. The supply-chain/export cluster (UC-4/6/8/9) is higher-ACV, stickier, and defensible via RPA moats (ICEGATE/DGFT). Sell growth-side first for logos + fast revenue, expand into export/compliance for retention.

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Trend Forecasting (social/runway/search) | Agent | 1 (needs vision + scraping) | instagram (RPA-new), pinterest (RPA-new), google_trends (new), perception/vision, web_search, slack, pdf_generator, erp-api (new) | Research+doc-gen (weekly Mon) | No |
| UC-2 | Sample Development Tracking | Both | 2 | document_reader, whatsapp, email, erp-api, plm-connector (new), slack, stt (new) | Real-time-monitoring / High-volume | No |
| UC-3 | Size/Fit Analysis from Returns | Both | 2 | shopify, myntra/ajio (RPA-new), web_search, pdf_generator, erp-api | Research+doc-gen | No |
| UC-4 | Fabric & Material Sourcing + Negotiation | Both | 2 | document_reader, email, whatsapp, web_search, indiamart (RPA-new), erp-api, pdf_generator | Research+doc-gen / Approval-gated | Yes (vendor selection) |
| UC-5 | Collection Launch Campaign Orchestration | Agent | 2 | shopify, myntra/ajio (RPA-new), mailchimp, whatsapp, meta_ads (new), google_ads (new), slack, email | Real-time-monitoring / High-volume | Yes (launch approvals) |
| UC-6 | Retail Sell-Through & Markdown Optimisation | Both | 2 | shopify, unicommerce (new), erp-api, crm, pdf_generator, email | Research+doc-gen / Approval-gated | Yes (markdown plan) |
| UC-7 | Influencer & Brand Ambassador Management | Agent | 2 | phyllo/modash (new), whatsapp, email, perception/vision, shiprocket (new), razorpay, slack | High-volume / Approval-gated | Yes (query escalation, payment) |
| UC-8 | Export Compliance (RCMC, Shipping Bills) | Agent | 2 | dgft (RPA-new), icegate (RPA-new), aepc/epc (RPA-new), email, pdf_generator, scheduler | Research+doc-gen / Approval-gated | Yes (pre-submission review) |
| UC-9 | Sustainability Reporting (BCI/GOTS/Higg) | Agent | 2 | whatsapp, email, web_search, bci-platform (new), browser-rpa, pdf_generator, slack | Research+doc-gen | No |
| UC-10 | D2C Customer Data + Personalized Styling | Agent | 1 | shopify, crm, whatsapp, nlp, scheduler, pdf_generator | High-volume-repetitive | No (CX complaints → HITL) |
| **UC-11** (proposed) | Marketplace Listing Health & Price-Parity Monitoring | Agent | 2 | myntra/ajio/nykaa/amazon (RPA-new), shopify, web_search, slack, code-sandbox | Real-time-monitoring | Light (price-change approval) |
| **UC-12** (proposed) | Product Catalog Enrichment & Visual QC | Agent | 1 (needs vision) | perception/vision, shopify, myntra/ajio-content-api (new), pdf_generator, slack | High-volume-repetitive | No |

## 3. Connectors Required
**Existing (Bucket 1):** whatsapp, email, web_search (SearXNG), pdf_generator, document_reader, shopify, razorpay, mailchimp (Klaviyo), slack, code-sandbox, google_calendar/scheduler.
**New-API (Bucket 2):** Google Trends API, ERP connector (brand ERP for sell-through/vendor/PO), PLM connector, STT transcription (fit-comment voice notes), Unicommerce/WinRetail OMS, Meta Ads + Google Ads APIs, Phyllo/Modash influencer-discovery, Shiprocket/Delhivery logistics, BCI platform API, Myntra/AJIO content APIs.
**RPA-portal (Bucket 2, high build):** instagram + pinterest social scraping (RPA-new — trend signals), Myntra/AJIO/Nykaa/Amazon marketplace seller portals (RPA-new — returns data, listing health, content), IndiaMart/Textile-Exchange supplier directories (RPA-new), **DGFT + ICEGATE** export portals (dgft/icegate RPA-new — the export-compliance moat), AEPC/EPC RCMC portals, GOTS public-database scrape.
**Perception/Multimodal:** Vision is core to UC-1 (silhouette/colour/fabric/print classification, Pantone mapping), UC-7 (brand-guideline + ASCI-disclosure compliance on influencer posts), and proposed UC-12 (auto-tagging + photo QC). Reuse shared `perception/` capability.
**Build-cost callout.** DGFT/ICEGATE RPA is the highest-value moat (defensible, painful, recurring). Social scraping is high-maintenance (anti-bot). Vision trend-classification needs a fashion-attribute-labelled model. Marketplace RPA (returns + listing) underpins UC-3/6/11.

## 4. Knowledge Collections
Seed slugs:
- `fashion-trend-signals` — classified social/runway/search signals: silhouette, colour (Pantone), fabric, print, velocity metrics.
- `fashion-sell-through-history` — 24-month ERP sell-through for commercial-viability scoring + elasticity.
- `fashion-fit-intelligence` — SKU × size return-for-fit tags, grading corrections, size-chart deltas.
- `fashion-supplier-directory` — fabric mills/processors: specs, MOQ, lead time, quality history, market benchmarks.
- `fashion-export-rules` — HS codes, RODTEP/ROSL rates, RCMC/IEC/BRC/FEMA deadlines, DGFT notifications.
- `fashion-sustainability-standards` — BCI/GOTS/OEKO-TEX/ZDHC-MRSL/Higg-FEM/GRI frameworks + buyer-specific templates (H&M, M&S, Decathlon).
- `fashion-customer-profiles` — unified purchase/browse/return/chat per customer (PII, per-tenant).
- `fashion-influencer-registry` — vetted influencer profiles, past collabs, engagement, fake-follower score.
**Ingestion recipe.** Social/runway via scheduled scraping → vision-classify → embed (weekly); sell-through via ERP sync; export rules from DGFT gazettes + tariff (document_reader, on-notification refresh); sustainability standards from official framework docs; customer profiles from Shopify+CRM+WhatsApp merge.

## 5. Guardrails & Compliance
- **Export filings HITL (UC-8).** Shipping bill / RCMC / RODTEP data reviewed before ICEGATE/DGFT submission; HS-code classification flagged not guessed; BRC/FEMA 9-month realisation deadline alerts. Filing errors carry duty + penalty exposure.
- **Sourcing + markdown HITL.** Vendor selection (UC-4) and markdown plans (UC-6) require manager approval within decision deadline; markdown auto-applies to POS/e-commerce only post-approval.
- **Influencer compliance (UC-7).** Vision checks ASCI-mandated #ad/#collab disclosure + brand-guideline adherence before payment; payment only on verified compliance + performance threshold; GST-compliant TDS (Form 26Q) logging.
- **Personalisation (UC-10) + launch (UC-5).** CX complaints escalate to human via HITL; WhatsApp send-volume + opt-in compliance; no auto-discount beyond configured bounds.
- **Data classification = business_confidential + PII.** Customer profiles, supplier prices, export/financial data → per-tenant RLS isolation, 1095-day retention, audit trail, secrets in vault. hitl_mandatory + rls_tenant_isolation per manifest.

## 6. Scale Pattern & Cost Drivers
- **Dominant pattern:** mix of Research+doc-gen (weekly trend brief, sourcing, fit, export, sustainability) + Real-time-monitoring (launch day, marketplace listing health, sample milestones) + High-volume-repetitive (personalised styling messages at customer scale).
- **Cost drivers (ranked):** (1) Vision inference for trend classification (500 posts/category/week) + influencer post QC + catalog QC; (2) social + marketplace scraping infrastructure; (3) LLM for trend briefs, personalised styling copy, RFQ/negotiation drafting at scale; (4) WhatsApp per-message (₹0.80 billed-through — pass to usage tier); (5) RPA runtime for DGFT/ICEGATE/marketplace portals.
- **Mitigations:** cache + dedup vision classifications for near-duplicate trend images; LLM-response cache for personalised styling templates (vary by segment, not per-customer from scratch); batch weekly trend scan off-peak; per-plan Celery queues (enterprise export isolated); tool-cache marketplace pulls; prompt compression on high-volume styling path.

## 7. Decision & Phasing
- **Phase 1 (D2C wedge, weeks 0–8):** UC-1 Trend Forecasting + UC-10 Personalized Styling + UC-7 Influencer Management (the Startup tier bundle). Shopify-native, fast ROI, exercises vision + scraping + WhatsApp.
- **Phase 2 (Growth, D2C ops):** UC-5 Launch Orchestration + UC-6 Markdown + UC-3 Fit-from-Returns + UC-2 Sample Tracking. Merchandising + supply feedback loops.
- **Phase 3 (Export/Sustainability, high-ACV moat):** UC-4 Fabric Sourcing + UC-8 Export Compliance + UC-9 Sustainability. RPA-defensible; enterprise/exporter tier.
- **ENRICHMENT (flagged): this domain shipped with only 10 UCs — one of the 4 thinnest domains. Enrich to 12** by adding:
  - **UC-11 Marketplace Listing Health & Price-Parity Monitoring** — real-time monitoring across Myntra/AJIO/Nykaa/Amazon for suppressed listings, buy-box/price-parity violations, content-compliance gaps; alerts + auto price-sync (HITL-light). Fills the "you launched but the listing is broken/underpriced" gap between UC-5 (launch) and UC-6 (markdown), reuses marketplace RPA already built for UC-3.
  - **UC-12 Product Catalog Enrichment & Visual QC** — vision auto-tagging of product attributes (colour/fabric/silhouette/occasion), marketplace-format image QC (background, resolution, angle compliance), and auto-generated SEO metadata across channels. High-volume-repetitive, reuses the UC-1 vision model; accelerates the listing step in UC-5.
  - Rationale: both slot into the existing D2C/marketplace connector + vision footprint (near-zero net-new build), lift the domain from 10→12 UCs, and close real workflow gaps rather than padding.
- **Verdict:** Accept, with enrichment to 12 UCs. Two-buyer structure (D2C-growth vs export/supply-chain) is a strength — land fast on Shopify D2C, defend with DGFT/ICEGATE RPA moat. Gate on vision trend-classifier quality and social/marketplace scraping resilience.

## 8. KPIs
- Trend: markdown inventory −18–22%; time-to-market 12–16wk → 6–8wk (−34%).
- Sample: correction rounds 2.8→1.4; ₹6,000/style rework saved.
- Fit: fit-return rate −4–6% over 2 seasons.
- Sourcing: fabric COGS −12–19%.
- Launch: 3–5 day launch delays eliminated; ₹20–60L first-week revenue recovered.
- Markdown: +8–14% margin vs calendar-based.
- Influencer: management overhead −70%; tracked conversion 2.4×.
- Export: doc time 7h→45min; ₹4,800/shipment delay-cost eliminated.
- Sustainability: replaces ₹12–20L/yr consultant; unlocks ESG-gated orders.
- Personalisation: repeat-purchase +5% (₹2.5cr on ₹50cr GMV); CTR 4.2×, AOV 2.8×.
- (Post-enrichment) UC-11: listing-suppression detection latency, price-parity violation count; UC-12: catalog attribute-tag accuracy, image-QC pass rate.
- Platform: vision cache hit-rate, scraping success rate, per-message cost vs usage-tier price, DGFT/ICEGATE RPA reliability.
