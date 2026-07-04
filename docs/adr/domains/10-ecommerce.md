# ADR-D10: E-Commerce & Retail Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/10-ecommerce/use-cases.md

## 1. Market & Monetization
- **TAM:** Indian e-commerce GMV crossed ₹8.8 lakh crore in FY2024, growing ~20%/year. Serviceable buyers: D2C brands, marketplace power sellers, and multi-channel retailers managing 1,000+ SKUs. Mid-size sellers currently spend ₹4–6L/month on catalog + ops headcount.
- **Buyer:** Founder/Head of E-commerce or Ops at D2C brands; category managers at marketplace agencies; finance/CA for the GST use case.
- **3 Tiers (₹):**
  - Tier 1 — Starter: **₹9,999/month** (3 agents: catalog enrichment + dynamic pricing + abandoned cart; up to 5 connectors; 10,000 actions; single brand).
  - Tier 2 — Growth: **₹34,999/month** (all 12 agents; up to 15 connectors; 1,00,000 actions; up to 3 brands; 5 seats; mobile HITL).
  - Tier 3 — Enterprise: **₹1,50,000+/month** (unlimited; custom ERP/WMS; white-label for agencies; multi-tenant; 99.9% SLA).
- **Consumption model:** Hybrid — flat subscription plus per-unit usage overages (₹1.20/SKU enriched, ₹15/return processed, 1.5% of attributed cart recovery capped at ₹50K/mo). Usage metering runs through `governance/cost.py` (per-goal/per-tenant Redis budgets).
- **WTP:** High and demonstrable. Repricing alone drives ₹3–6L/month incremental GMV for a ₹1cr/month seller (37–75× ROI); cart recovery 65× ROI. Sellers already pay Feedvisor/Teikametrics for a narrow slice.
- **Time-to-first-revenue:** **Fast (days).** Largely Bucket-1 (Shopify + marketplace APIs already exist as connectors). Starter bundle can go live within a self-serve trial.
- **Monetization note:** This is the flagship land-and-expand vertical. Lead with the Starter bundle (measurable ROI in week 1), then expand via consumption on GST/returns/reviews. Agencies are a channel — sell Enterprise white-label for multi-brand management.

## 2. Use Cases -> Product Mapping
| UC-N | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|------|-------|----------|--------|-----------|---------------|-------|
| UC-1 | Product Catalog Enrichment at Scale | Both | 1 | shopify, amazon_sp, flipkart (RPA-new), meesho (RPA-new), web_search, slack | High-volume-repetitive | No |
| UC-2 | Dynamic Pricing Optimization | Agent | 1 | shopify, amazon_sp, flipkart (RPA-new), browser RPA, slack | Real-time-monitoring | Yes (>15% change) |
| UC-3 | Multi-Marketplace Listing Sync | Agent | 1 | shopify, amazon_sp, flipkart (RPA-new), meesho (RPA-new), myntra (RPA-new), google_sheets | Real-time-monitoring | No |
| UC-4 | Abandoned Cart Recovery | Agent | 1 | shopify, mailchimp, whatsapp, sms/twilio, slack | Real-time-monitoring | No |
| UC-5 | Inventory Reorder & Demand Forecasting | Both | 1 | shopify, web_search, pdf_generator, email, google_sheets | Research+doc-gen | Yes (PO >₹50K) |
| UC-6 | Customer Review Management | Agent | 1 | amazon_sp, flipkart (RPA-new), slack, email | High-volume-repetitive | Yes (neutral/negative) |
| UC-7 | Return & Refund + Fraud Detection | Agent | 1/2 | shopify, amazon_sp, razorpay, stripe, shiprocket (RPA-new), email, slack | Approval-gated | Yes (fraud 0.30–0.75) |
| UC-8 | Flash Sale Orchestration | Both | 1 | shopify, amazon_sp, flipkart (RPA-new), mailchimp, whatsapp, slack | Real-time-monitoring | Yes (negative margin) |
| UC-9 | Influencer Campaign Discovery & Outreach | Agent | 2 | web_search, browser RPA (IG/YT), email, google_sheets | Research+doc-gen | No |
| UC-10 | Customer Segmentation & Personalization | Agent | 1 | shopify, mailchimp, whatsapp, google_sheets | High-volume-repetitive | No |
| UC-11 | Supplier Negotiation & PO Management | Agent | 1 | email, document_reader, pdf_generator, web_search, google_sheets, razorpay | Approval-gated | Yes (counter-offer/PO) |
| UC-12 | GST Reconciliation for Multi-Channel Sales | Both | 2 | amazon_sp, flipkart (RPA-new), shopify, zoho_books (RPA-new), gst_portal (RPA-portal), email | Research+doc-gen | Yes (final GSTR-1) |

## 3. Connectors Required
- **Existing (Bucket 1, reuse):** shopify, amazon_sp, gmail/email, whatsapp, sms, razorpay, stripe, google_sheets, document_reader, web_search, pdf_generator, twilio, mailchimp. Browser RPA framework exists (`rpa/` + Playwright).
- **New-API (build, low-medium cost):** flipkart_seller_api, meesho_api. Estimate ~1 week each (documented REST APIs, OAuth). zoho_books connector for GST export.
- **RPA-portal (build, medium-high cost):** myntra_partner_portal (no public API), shiprocket/delhivery label generation, **gst_portal** (GSTR-2B download, GSTR-1 upload), amazon/flipkart settlement-report scraping where API gaps exist. Each RPA flow ~1.5–2 weeks incl. selector maintenance.
- **RPA-new (unknown/fragile):** Instagram/YouTube creator scraping for UC-9 (anti-bot risk — treat as best-effort, gate with rate limits).
- **Build cost summary:** Bucket-1 use cases ship near-immediately. Full 12-UC coverage needs ~4–6 connector builds, mostly RPA. GST portal RPA is the single highest-cost item and highest-compliance-risk.

## 4. Knowledge Collections
- **Seed slugs:** `pricing-rules`, `margin-floors-by-category`, `competitor-mapping`, `brand-voice-guide`, `marketplace-category-attributes` (Amazon/Flipkart required fields), `hsn-gst-rate-table`, `return-fraud-signals`, `seo-keyword-clusters`, `supplier-master-registry`, `festival-event-calendar-in`.
- **Ingestion recipe:** (1) Ingest seller's historical Shopify/marketplace export into `KnowledgeStore` (hybrid pgvector + trigram). (2) Scrape category-specific marketplace attribute schemas via RPA into `marketplace-category-attributes`. (3) Load HSN/GST rate master from CBIC bulletins (document_reader → structured). (4) Brand voice: ingest existing top-performing listings + brand style doc; embed for retrieval during generation. (5) `SemanticCache` dedupes repeat enrichment/response LLM calls across the 10,000-SKU batch to control cost.

## 5. Guardrails & Compliance
- **Regime:** Marketplace ToS (Amazon/Flipkart/Meesho seller policies), GST law (CGST/SGST/IGST, TCS 1%, GSTR-1/2B), Consumer Protection (E-Commerce) Rules 2020, DPDP Act 2023 for customer PII in CRM/cart data.
- **HITL gates:** price changes >15% (UC-2); negative-margin flash items (UC-8); fraud score 0.30–0.75 refunds and all >0.75 holds (UC-7); POs >₹50K (UC-5) / counter-offers (UC-11); final GSTR-1 before portal upload (UC-12). Use `governance/hitl.py` approval queue.
- **Fail-closed:** margin-floor policy is a hard `deny` in the tool policy engine (`policies.py`) — pricing agent can never push below (cost×1.15 + fee + shipping). GST filing agent never auto-submits to gst_portal; it stages JSON for human upload. Fraud holds block refund tool dispatch until cleared.
- **Audit:** all price changes, refunds, PO issuance, and GST filings written to append-only `governance/audit.py` trail with timestamp, trigger reason, old/new values, competitor/comparable reference. DPDP: PII masking in logs; consent tracked for WhatsApp/SMS re-opt-in.

## 6. Scale Pattern & Cost Drivers
- **Dominant patterns:** High-volume-repetitive (catalog, reviews, segmentation) and Real-time-monitoring (pricing, listing sync, cart recovery, flash sales). Route enterprise tenants to dedicated Celery queues (`goals.enterprise`) to avoid noisy-neighbour during Big Billion Day-style spikes.
- **Cost drivers:** (1) LLM token volume on catalog enrichment (12,000 SKUs × multi-field generation) — mitigate with `SemanticCache`, `prompt_compressor`, and Haiku-tier executor via `model_router`. (2) Marketplace API rate limits and RPA fragility (competitor scraping every 15 min). (3) Webhook fan-out for real-time listing/inventory sync. (4) Cross-replica cost accounting for consumption billing accuracy.
- **Optimizations already in tree:** `mcp/tool_cache.py`, `rag/llm_response_cache.py`, `services/dedup.py` directly reduce per-SKU and per-poll cost.

## 7. Decision & Phasing
- **Decision:** Adopt e-commerce as the **flagship Phase-1 vertical** — highest WTP, fastest time-to-revenue, mostly Bucket-1.
- **Phase 1 (launch, weeks 0–4):** UC-1, UC-2, UC-4, UC-10 on Shopify + Amazon SP-API only. Ship Starter tier self-serve.
- **Phase 2 (weeks 4–10):** Add flipkart/meesho connectors → UC-3, UC-6, UC-8. Add returns/fraud (UC-7) with shiprocket RPA.
- **Phase 3 (weeks 10–16):** UC-5, UC-11 (procurement), UC-9 (influencer, best-effort RPA), and UC-12 GST reconciliation (highest-cost, compliance-reviewed). Launch Growth + Enterprise tiers.

## 8. KPIs
- Time-to-first-value: seller live and seeing repriced SKUs / recovered carts within 48h of signup.
- Business impact: listing quality lift (target +28%), Buy Box hours won, cart recovery rate (target 8–12%), stockouts avoided, review response time <48h, GST reconciliation error rate → near-zero.
- Platform health: agent success rate per UC, HITL approval latency, price-floor policy violations (must be 0), RPA flow uptime, LLM cost per SKU/action, marketplace API error rate.
- Commercial: activation rate (trial→paid), net revenue retention via consumption expansion, agency-channel Enterprise seats.
