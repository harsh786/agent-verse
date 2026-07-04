# ADR-D21: Agriculture Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/21-agriculture/use-cases.md

## 1. Market & Monetization
- **TAM.** Indian agriculture underpins 600M livelihoods and ₹30 lakh crore of GDP. The serviceable digital-advisory + agri-fintech slice is large but low-ARPU at the farmer level; value concentrates in institutional buyers. Anchor spend: 28 State Agriculture Departments, NABARD/SFAC, ~10,000 FPOs (15M members), agri-input majors (Bayer, Syngenta, BASF, Coromandel, IFFCO), and rural banks/RRBs/DCCBs.
- **Buyer.** Primary economic buyer is institutional (State Ag Dept, FPO federation, agri-input company, bank), NOT the individual farmer. Farmer is the end-user; the institution pays to reach them at scale. Classic B2B2C / B2G2C.
- **3 tiers (₹).**
  - **Kisan (farmer direct):** ₹499/year — disease diagnosis (50/yr), mandi/weather alerts, scheme eligibility, Hindi + 3 regional languages.
  - **FPO/Agribusiness:** ₹24,999/month — all 10 UCs, 500 farmers, collective trading, contract-farming, KCC assistance, success manager.
  - **Government/Enterprise:** ₹4,99,999/month — unlimited farmers/FPOs, custom state integrations (land records, APMC, PMFBY), multi-state, offline/IVR, quarterly scheme-impact reporting.
- **Consumption model.** Hybrid: per-transaction (₹15/diagnosis, ₹200/scheme application, ₹300/KCC, ₹50/cold-storage booking, 0.8% FPO trade commission) layered under institutional flat subscriptions. Government bulk seats dominate revenue; transaction fees dominate volume.
- **WTP.** Farmer WTP is thin (₹199–499/yr ceiling — must be near-free or input-company-subsidised). Institutions have strong WTP because the alternative is field extension officers at 1:750 ratio and unclaimed subsidy leakage.
- **Time-to-first-revenue.** Fastest via input-company white-label (disease diagnosis as loyalty tool, ~4–8 weeks) and FPO subscriptions. Government contracts are high-value but 6–12 month procurement cycles.
- **Monetization note.** Do NOT bet on direct-farmer subscriptions for margin. Sell the aggregator: input companies and state schemes pay for reach; farmer transactions prove engagement to justify institutional renewal. Vision + RPA-heavy UCs (disease, mandi, schemes) are the wedge; FPO trading + contract farming are the high-margin expansion.

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Crop Disease Diagnosis from Field Photos | Agent | 1 (needs vision) | whatsapp, perception/multimodal-vision, web_search, postgres, translation, code-sandbox | High-volume-repetitive | No (auto-escalate to ICAR expert if confidence <70%) |
| UC-2 | Weather-Based Irrigation & Fertilisation Advisory | Agent | 2 | imd_api (RPA-new), isro_bhuvan (RPA-new), weather_api, code-sandbox, whatsapp, translation | High-volume-repetitive (daily 05:00 cron) | No |
| UC-3 | Mandi Price Comparison & Sell Timing | Both | 2 | agamarknet_api (RPA-new), enam_mandi (RPA-new), web_search, code-sandbox, whatsapp, translation | Real-time-monitoring | No |
| UC-4 | Input Procurement Optimization | Both | 2 | agrostar/dehaat/bighaat (RPA-new), coop-portal (RPA-new), web_search, payment, whatsapp | Research+doc-gen | Light (OTP to place order) |
| UC-5 | PM-KISAN / PMFBY Eligibility & Application | Agent | 2 | pm_kisan (RPA-new), pmfby (RPA-new), document_reader, payment, whatsapp, translation | Approval-gated / Research+doc-gen | Yes (form submit) |
| UC-6 | Soil Health Analysis & Custom Fertiliser Plan | Both (Template-led) | 1 | document_reader, code-sandbox (LP optimiser), postgres, agrostar (RPA-new), whatsapp | Research+doc-gen | No |
| UC-7 | Cold Storage Availability & Price Discovery | Both | 2 | postgres (registry), wdra-eNWR (RPA-new), agamarknet_api (RPA-new), sms, whatsapp | Real-time-monitoring | No |
| UC-8 | Contract Farming Agreement Management | Agent | 2 | aadhaar_esign (RPA-new), payment, perception-vision, pdf_generator, postgres, whatsapp | Approval-gated | Yes (quality/price dispute, payment) |
| UC-9 | Crop Loan / KCC Application Assistance | Agent | 2 | cibil_api (RPA-new), land_records (RPA-new), document_reader, pdf_generator, whatsapp | Approval-gated / Research+doc-gen | Yes (branch sanction) |
| UC-10 | FPO Trading Support & Collective Marketing | Agent | 3 | web_search, mca21_api (RPA-new), email, pdf_generator, postgres, whatsapp | Research+doc-gen / Approval-gated | Yes (trade >₹5L, floor-price) |

## 3. Connectors Required
**Existing (Bucket 1, ~0 build):** whatsapp, sms, web_search (SearXNG), pdf_generator, document_reader (PDF/image OCR for SHC, land records, Aadhaar), payment gateway (razorpay/stripe), google_sheets, email.
**New-API (Bucket 2, moderate build):** IMD weather API, ISRO Bhuvan / Sentinel soil-moisture, Agamarknet API, eNAM API, ICAR/NCIPM advisory API (where exposed), CIBIL agri-score API, MCA21 API, Aadhaar eSign API, AgroStar/DeHaat/BigHaat commerce APIs.
**RPA-portal (Bucket 2, high build + ongoing maintenance):** pm_kisan (RPA-new), pmfby (RPA-new), enam_mandi / state APMC daily-price pages (RPA-new), state land-records portals (RPA-new — per-state variance is the real cost), WDRA e-NWR portal, state cooperative/subsidy portals.
**Perception/Multimodal (net-new capability):** crop-disease vision classifier + contract-farming field-photo verification (vegetation index, planting density). This is the domain's single most differentiating build — route through `perception/` + embedder-vision.
**Build-cost callout.** State land-records and PM-KISAN/PMFBY RPA are the cost sinks (fragmented, CAPTCHA'd, frequently redesigned). Vision model needs a labelled India-crop-disease dataset; budget data acquisition, not just inference.

## 4. Knowledge Collections
Seed slugs (hybrid pgvector + trigram):
- `agri-crop-disease-catalog` — disease/pest → symptoms, botanical + regional names, contagion class.
- `agri-geo-seasonal-prevalence` — district × crop × season disease incidence priors.
- `agri-agrichemical-registry` — Insecticides-Act-approved molecules, dosage, PHI, state bans.
- `agri-crop-nutrition` — target-yield nutrient uptake per crop/variety; soil-type fixation factors.
- `agri-mandi-price-history` — 24-month APMC/eNAM commodity prices for ARIMA/seasonal models.
- `agri-transport-cost` — distance × vehicle rental for net-at-farm computation.
- `agri-scheme-rules` — PM-KISAN/PMFBY/KCC/SHC eligibility, deadlines, premium formulae, subvention.
- `agri-cold-storage-registry` — facility location, capacity, rates, contacts.
- `agri-input-catalog` — district-available products, prices, subsidy mapping.
**Ingestion recipe.** ICAR/NCIPM advisories + State Ag University bulletins (PDF → chunk → embed); Agamarknet/eNAM daily pulls (structured → append to price-history); scheme rules from gazette notifications (SearXNG + document_reader, quarterly refresh); disease catalog from labelled image corpus + expert curation. Multilingual embeddings required (11 languages).

## 5. Guardrails & Compliance
- **Pesticide safety (highest risk).** Every chemical recommendation MUST validate against `agri-agrichemical-registry`: approved molecule, correct dosage, pre-harvest interval, and **state-specific ban list**. Wrong advice = crop loss + legal exposure. Hard block on restricted molecules; cite source advisory.
- **Diagnosis confidence gate.** Disease confidence <70% → escalate to ICAR/SAU human expert with farmer consent; never emit a low-confidence chemical prescription.
- **Financial advice boundary.** KCC/loan and sell-timing outputs are decision-support, not guarantees — disclaim; MSP/procurement info must be sourced from current notifications.
- **HITL gates.** FPO trade acceptance >₹5L and below-floor-price; contract-farming grade disputes; scheme form submission; loan sanction (bank officer).
- **Data residency = India** (Aadhaar, land records, CIBIL are sensitive PII). Aadhaar data minimisation; no raw Aadhaar in logs. Audit trail on all scheme/financial actions.
- **Input-credit anti-misuse:** contract-farming input credit pays supplier directly, never cash to farmer.

## 6. Scale Pattern & Cost Drivers
- **Dominant pattern:** High-volume-repetitive (disease diagnosis, daily weather cron across every registered plot) + Real-time-monitoring (mandi prices, cold-storage). A single state contract = millions of daily WhatsApp advisories.
- **Cost drivers (ranked):** (1) Vision inference at farmer scale; (2) WhatsApp Business messaging volume; (3) LLM translation across 11 languages per message; (4) RPA maintenance for volatile government portals; (5) IVR/offline fallback for low-connectivity. 
- **Mitigations:** aggressive LLM-response caching + semantic dedup for repeated advisories in a district; batch cluster-detection (Celery) so one disease outbreak → one bulk advisory not N diagnoses; per-plan Celery queue routing (govt/enterprise isolated from noisy free tier); prompt compression on the multilingual path.

## 7. Decision & Phasing
- **Phase 1 (wedge, weeks 0–8):** UC-1 Disease Diagnosis + UC-3 Mandi Prices + UC-2 Weather — the WhatsApp+vision trio that input companies white-label. Highest engagement, proves the multilingual perception pipeline.
- **Phase 2 (institutional lock-in):** UC-5 Schemes + UC-9 KCC + UC-6 Soil Health — government/bank contracts; heavy RPA. Requires land-records + PM-KISAN/PMFBY RPA hardened.
- **Phase 3 (high-margin expansion):** UC-10 FPO Trading + UC-8 Contract Farming + UC-7 Cold Storage + UC-4 Input Procurement — the FPO/agribusiness tier where commissions and per-acre fees compound.
- **Verdict:** Accept. Agriculture is a flagship for AgentVerse's perception + RPA + multilingual + HITL stack; institutional/B2G buyers de-risk the low farmer ARPU. Gate go-live on the pesticide-safety guardrail and land-records RPA reliability.

## 8. KPIs
- Disease diagnosis accuracy ≥87%; median response <3 min; <70%-confidence escalation rate.
- Mandi advisory price-realisation uplift (target +₹180/quintal); farmer income uplift ₹15k–45k/yr.
- PM-KISAN enrolment success ≥94% (vs 61% manual); PMFBY claim settlement 68%→89%.
- KCC application success ≥88%; institutional-credit access expansion.
- FPO commercial activation 40%→85%; average sale price +18–28% vs local mandi.
- Platform: WhatsApp cost/advisory, cache hit-rate on repeat advisories, RPA success rate per government portal, per-tenant cost vs govt subscription price.
