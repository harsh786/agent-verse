# Merchant Onboarding on a Payment Gateway — Complete Requirements Guide

> Covers KYC, documentation, website verification, risk underwriting, compliance, technical integration, and ongoing monitoring.
> Applicable to Indian payment gateways operating under RBI PA/PG Master Directions 2020.

---

## Table of Contents

1. [Overview of the Onboarding Flow](#1-overview-of-the-onboarding-flow)
2. [Business Entity Types and Required Documents](#2-business-entity-types-and-required-documents)
3. [KYC Requirements in Depth](#3-kyc-requirements-in-depth)
4. [Bank Account Verification](#4-bank-account-verification)
5. [Website / App Verification](#5-website--app-verification)
6. [Risk Assessment and Underwriting](#6-risk-assessment-and-underwriting)
7. [MCC (Merchant Category Code) Assignment](#7-mcc-merchant-category-code-assignment)
8. [Compliance and Regulatory Checks](#8-compliance-and-regulatory-checks)
9. [Merchant Agreement](#9-merchant-agreement)
10. [Technical Integration Requirements](#10-technical-integration-requirements)
11. [Rolling Reserve and Settlements](#11-rolling-reserve-and-settlements)
12. [Ongoing Monitoring Post-Onboarding](#12-ongoing-monitoring-post-onboarding)
13. [Special Requirements by Industry Vertical](#13-special-requirements-by-industry-vertical)
14. [Prohibited and Restricted Categories](#14-prohibited-and-restricted-categories)
15. [Summary Checklist](#15-summary-checklist)

---

## 1. Overview of the Onboarding Flow

The merchant onboarding process on a payment gateway has six distinct phases:

```
Phase 1: Application & Business Details Collection
         ↓
Phase 2: KYC Verification (Individual + Business)
         ↓
Phase 3: Website / Product / Service Validation
         ↓
Phase 4: Risk Underwriting (financial + operational risk)
         ↓
Phase 5: Compliance Screening (AML, sanctions, PEP)
         ↓
Phase 6: Agreement Signing + Technical Integration + Go-Live
```

**Typical Timelines:**
| Merchant Type | Onboarding Time |
|--------------|----------------|
| Sole Proprietorship (low risk) | 24–48 hours |
| Private Limited (low risk) | 2–3 business days |
| High-Risk Category | 5–10 business days |
| Regulated Sectors (NBFC, Insurance) | 1–3 weeks |
| Enterprise / Custom Agreement | 2–4 weeks |

---

## 2. Business Entity Types and Required Documents

### 2.1 Sole Proprietorship

| Document | Purpose | Mandatory? |
|----------|---------|------------|
| PAN Card of Proprietor | Tax identity | Yes |
| Aadhaar Card of Proprietor | Individual KYC + address | Yes |
| Business Registration (any one): Shop & Establishment Act certificate / Udyam/MSME certificate / Trade License / Municipal License | Business existence proof | Yes |
| GST Certificate | Tax registration + business address | If turnover > ₹20L; else optional |
| Cancelled Cheque (current account in business name) | Bank account verification | Yes |
| Bank Statement (last 3–6 months) | Financial activity proof | Yes |
| Passport-size photograph of proprietor | Photo KYC | Sometimes |

---

### 2.2 Partnership Firm

| Document | Purpose | Mandatory? |
|----------|---------|------------|
| PAN Card of the Firm | Business tax identity | Yes |
| Registered Partnership Deed | Business constitution | Yes |
| PAN Card of all Partners | Individual KYC | Yes |
| Aadhaar Card of all Partners | Individual KYC | Yes |
| GST Certificate | Tax registration | Yes (if applicable) |
| Current Account Bank Details | Settlement account | Yes |
| Cancelled Cheque in firm's name | Bank verification | Yes |
| Authorization Letter (which partner is authorized to sign) | Operational control | Yes |

---

### 2.3 Private Limited / Public Limited Company

| Document | Purpose | Mandatory? |
|----------|---------|------------|
| Certificate of Incorporation (CoI) | Company existence | Yes |
| Memorandum of Association (MoA) | Business object clauses | Yes |
| Articles of Association (AoA) | Internal governance | Yes |
| PAN Card of the Company | Business tax identity | Yes |
| GST Certificate | Tax registration | Yes |
| Board Resolution | Authorizes specific person(s) to operate the PG account | Yes |
| List of Directors with DIN | Corporate structure | Yes |
| PAN + Aadhaar of all directors / authorized signatories | Individual KYC | Yes |
| Shareholding Pattern | Beneficial ownership | Yes (>25% ownership flagged) |
| Bank Account (current account in company name) | Settlement | Yes |
| Cancelled Cheque | Bank verification | Yes |
| Bank Statement (6 months) | Financial health | Yes |
| Latest ITR / CA Certificate | Revenue declaration | Sometimes |

---

### 2.4 LLP (Limited Liability Partnership)

| Document | Purpose | Mandatory? |
|----------|---------|------------|
| LLP Incorporation Certificate | Existence proof | Yes |
| LLP Agreement | Business constitution | Yes |
| PAN Card of LLP | Business tax identity | Yes |
| PAN + Aadhaar of Designated Partners | Individual KYC | Yes |
| GST Certificate | Tax registration | Yes |
| Bank Account details + Cancelled Cheque | Settlement | Yes |

---

### 2.5 Trust / NGO / Society

| Document | Purpose | Mandatory? |
|----------|---------|------------|
| Trust Deed / Society Registration Certificate | Entity existence | Yes |
| 80G / 12A Certificate | Tax-exempt donation eligibility | For donation collection |
| PAN Card of Trust | Tax identity | Yes |
| Aadhaar/PAN of Trustees / Managing Committee | Individual KYC | Yes |
| Resolution authorizing the signatory | Operational control | Yes |
| Bank Account in Trust/Society name | Settlement | Yes |
| Cancelled Cheque | Bank verification | Yes |
| Annual Report or Balance Sheet | Financial credibility | Sometimes |

---

### 2.6 Government / PSU Entities

- Government order / notification
- PFMS (Public Financial Management System) registration
- Treasury account details
- Nodal officer designation letter

---

## 3. KYC Requirements in Depth

### 3.1 Individual KYC (Proprietor / Director / Signatory)

**PAN Verification:**
- PAN number cross-verified against NSDL / UTIITSL database in real time
- Name, date of birth, and PAN status (active/inactive) checked
- PAN must not appear on RBI defaulter list or court orders
- PAN OCR supported by many gateways (upload image → auto-fill details)

**Aadhaar Verification:**
- Two methods: OTP-based (live Aadhaar linkage verification) or Aadhaar OCR
- Aadhaar number is never stored — only the last 4 digits retained (per UIDAI mandate)
- Address extracted from Aadhaar used as registered address
- Biometric Aadhaar (fingerprint/iris) used for in-person verification at bank branches

**Live Photo / Video KYC (V-KYC):**
- Increasingly used by progressive gateways
- Short video call with agent: customer shows PAN + Aadhaar, confirms details
- Or AI-based liveness detection: selfie match with PAN photo

**Proof of Address (if Aadhaar not used):**
- Passport
- Driving License
- Voter ID
- Utility bills (electricity, gas, water — not older than 3 months)
- Rent agreement + utility bill combo

### 3.2 Business KYC

**GSTIN Verification:**
- Real-time API check against GST portal
- Confirms: business name, registration date, address, type (Regular/Composition), active/cancelled/suspended status
- GSTIN must match the name on PAN and bank account

**MCA / ROC Verification (for companies):**
- Director details cross-checked with MCA21 database
- Company active status confirmed (not struck off, not under liquidation)
- Charges registered against the company reviewed (secured creditors can indicate financial distress)
- Director's DIN (Director Identification Number) validated

**Udyam / MSME Verification:**
- Cross-checked with Udyam portal for MSME classification
- Provides additional credibility for small businesses

### 3.3 UBO (Ultimate Beneficial Owner) Identification

Per RBI and PMLA regulations, payment gateways must identify the UBO — the natural person(s) who ultimately own or control the merchant entity.

**Threshold:** Any individual with >25% direct or indirect ownership / control / voting rights

**Documents Required:**
- Shareholding chart showing ownership chain
- UBO declaration signed by the entity
- PAN and Aadhaar of each UBO
- If ownership is through another company: repeat the chain until a natural person is identified

---

## 4. Bank Account Verification

### 4.1 Penny Drop Verification

The most standard method for bank account validation:

1. Payment gateway initiates a ₹1 (or small amount) NEFT/IMPS credit to the provided account
2. The beneficiary name returned by the bank is cross-matched with the merchant's entity name
3. If name matches: account verified instantly
4. If mismatched: manual review or rejection

**Why it matters:** Prevents merchants from settling to a third-party account (money laundering risk)

### 4.2 Bank Account Requirements

| Requirement | Detail |
|------------|--------|
| Account Type | Current account (mandatory for business entities; savings accounts rejected) |
| Account Name | Must exactly match the registered business name (Pvt Ltd/LLP/Partnership deed name) |
| Account Status | Active, not dormant, not under freeze/lien |
| Bank | Schedule commercial bank or co-operative bank (no payment bank accounts for settlement) |
| IFSC | Valid and active IFSC code |

### 4.3 Supporting Documents

- **Cancelled Cheque**: Pre-printed name preferred; must show account number + IFSC
- **Bank Statement (3–6 months)**: Should show regular inward credits (business activity), no unusual round-trip transactions
- **Bank Letter / Certificate of Account**: Sometimes requested for additional confirmation

### 4.4 Multiple Bank Accounts

Larger merchants may request:
- Primary settlement account (daily settlements)
- Refund account (separate account for refund processing)
- Holding account (for rolling reserve)

All require individual penny drop verification.

---

## 5. Website / App Verification

This is the **most scrutinized step** in India post the 2020 RBI PA/PG Master Directions.

### 5.1 Technical Website Requirements

| Requirement | Standard | Why |
|------------|----------|-----|
| SSL Certificate | Valid HTTPS, not expired, TLS 1.2+ | Security for customer transactions |
| Domain Age | Minimum 30–90 days (varies by PG) | Reduces fly-by-night merchant risk |
| Domain Ownership | WHOIS check — must align with merchant entity | Prevents domain spoofing |
| Website Status | Fully live, not under construction / coming soon | Can't verify a placeholder |
| Page Load | Basic functionality verified | Automated scan |
| Hosting Country | Not blocked/sanctioned jurisdiction | Compliance |

### 5.2 Mandatory Pages (RBI-mandated for PA/PG merchants)

**1. Terms & Conditions**
- Must cover: cancellation, disputes, liability, governing law
- Must be dated and signed/approved
- Cannot be a generic template with company name blank

**2. Privacy Policy**
- Must explain what data is collected, how it's stored, who it's shared with
- Must reference data protection law (IT Act / DPDP Act 2023)
- Contact details for privacy queries

**3. Refund and Cancellation Policy**
- Clear timelines: when refund is issued (e.g., within 5–7 business days)
- Conditions for refund eligibility
- Non-refundable conditions clearly stated
- How to raise a refund request (contact channel)

**4. Shipping Policy (physical goods only)**
- Expected delivery timelines
- Geographic coverage
- Shipping partners
- What happens if delivery fails

**5. Contact Us Page**
- Physical registered office address (PO Box not acceptable)
- Working email ID (not a generic Gmail — must be on the business domain)
- Phone number (reachable, not blank)
- Grievance redressal mechanism
- Business hours

**6. About Us / Business Description**
- What the merchant sells / offers
- Must be consistent with the MCC code declared during onboarding

### 5.3 Content Scanning (Automated + Manual)

**Automated scans check for:**
- Prohibited keywords (adult, drugs, gambling, weapons, counterfeit)
- Broken checkout links
- Payment gateway integration test (payment button must work)
- Price listing presence
- Currency display (INR for domestic)

**Manual review checks for:**
- Product images consistent with declared business
- Pricing in line with declared average ticket size
- No fake products (brand impersonation — fake Nike, Apple, etc.)
- No misleading claims (guaranteed returns, risk-free investments)
- Customer testimonials not fabricated
- Social media links active and consistent

### 5.4 App Store Verification (Mobile-First Merchants)

| Check | Detail |
|-------|--------|
| App live on Play Store / App Store | Cannot be in draft / unlisted |
| App rating | Very low ratings (below 2.5) flagged |
| App permissions | Excessive permissions (reading SMS, contacts for non-banking apps) trigger review |
| App description consistency | Matches declared business activity |
| Developer account name | Cross-checked with merchant entity name |

### 5.5 Social Media Verification

For some PGs and for determining credibility:
- LinkedIn company page exists and matches
- Facebook/Instagram page active with real engagement
- No reputation crisis in recent posts
- Follower count and post history indicate genuine business

---

## 6. Risk Assessment and Underwriting

### 6.1 Financial Risk Metrics

| Metric | What PG Evaluates | Risk Indicator |
|--------|-------------------|----------------|
| Declared GMV | Monthly expected transaction volume | Very high GMV from new merchant = suspicious |
| Average ticket size | Per-transaction expected amount | >₹50,000 average = higher scrutiny |
| Business vintage | How long merchant has operated | < 6 months = higher risk |
| Refund rate | Expected % of transactions refunded | >10% = medium risk; >20% = high risk |
| Chargeback history | History with previous PG (if migrating) | >1% = VISA/MC threshold breach |
| Revenue proof | Bank statement / ITR / CA certificate | Inconsistency between declared and actual = red flag |

### 6.2 Chargeback Risk Assessment

Chargebacks (customer disputing a transaction) are the primary financial risk for payment gateways. They evaluate:

- **Merchant's industry chargeback history**: Travel, digital goods, subscriptions have higher chargeback rates
- **Business model risk**: Subscription businesses with free trials have high chargeback propensity
- **Delivery model**: Digital delivery = no proof of delivery = higher chargeback risk
- **Dispute resolution process**: Does the merchant have a clear process?

### 6.3 Operational Risk Metrics

- Is the business model seasonal (high volume during festivals, then dormant)?
- Does the merchant have customer support infrastructure?
- Is there a return/refund process that reduces chargebacks?
- Are there repeat complaints on consumer forums (Google reviews, MouthShut, consumer courts)?

### 6.4 Behavioral Risk Signals

Red flags during onboarding:
- Merchant refuses to share bank statement
- Declared GMV wildly inconsistent with business size
- Multiple failed onboarding attempts at other PGs
- Address mismatch across documents
- Director recently involved in fraud/legal case
- Business registered in the last 30 days with very high declared volume
- Shell company structure with no operational presence

---

## 7. MCC (Merchant Category Code) Assignment

### 7.1 What is MCC?

A 4-digit numeric code assigned to every merchant that classifies their primary line of business. Defined by Visa/Mastercard/NPCI. Used to:
- Set TDR (Transaction Discount Rate)
- Determine risk category
- Apply cashback/reward rules from banks
- Flag high-risk categories

### 7.2 Important MCCs in India

| MCC | Category | Risk Level |
|-----|----------|------------|
| 5411 | Grocery Stores, Supermarkets | Low |
| 5912 | Drug Stores, Pharmacies | Low-Medium |
| 5732 | Electronics | Medium |
| 7011 | Lodging / Hotels | Medium |
| 4111 | Transportation / IRCTC type | Low |
| 4812 | Telecom | Low |
| 5945 | Gaming / Toy Stores | Low |
| 5816 | Digital Games | Medium |
| 7801 | Gambling (licensed) | High |
| 6211 | Securities Trading | High |
| 4829 | Money Transfer | High |
| 7273 | Dating Services | High |
| 5999 | Miscellaneous Retail | Medium |

### 7.3 MCC Mismatch Risk

If a merchant declares they sell electronics (MCC 5732) but the actual transactions show patterns consistent with forex trading — the PG's risk engine flags this as MCC mismatch fraud. This is a serious compliance violation.

---

## 8. Compliance and Regulatory Checks

### 8.1 AML / CFT Screening (Anti-Money Laundering / Counter Financing of Terrorism)

**Sanctions Screening:**
- OFAC (US Treasury Office of Foreign Assets Control) list
- UN Security Council Consolidated Sanctions List
- EU Restrictive Measures
- RBI watchlist
- Interpol notices

**PEP Screening (Politically Exposed Persons):**
- Directors/UBOs cross-checked against PEP databases
- PEPs are not automatically rejected but require Enhanced Due Diligence (EDD)
- Higher monitoring frequency for PEP-linked merchants

**Adverse Media Screening:**
- Google / news database search on entity name, director names
- Look for: fraud, money laundering, court cases, RBI show-cause notices, ED/CBI involvement

**FIU-IND Reporting:**
- Suspicious Transaction Reports (STR) filed with Financial Intelligence Unit India when warranted
- Cash Transaction Reports (CTR) for cash-based merchants above threshold

### 8.2 PMLA Compliance

Under the Prevention of Money Laundering Act:
- PG must conduct KYC before onboarding any merchant
- Records must be maintained for 5 years
- Suspicious transactions must be reported within 7 days
- Merchants linked to PMLA notices cannot be onboarded

### 8.3 RBI-Specific Requirements (PA/PG Master Directions 2020)

For payment aggregators (like Razorpay, Payu, Cashfree, Plural, Toucan):

- Merchants must only use the PA for their own business (not resell PG access to others — that requires a separate PA license)
- PA must have a merchant grievance redressal mechanism
- PA must ensure merchants display their policies (T&C, Privacy, Refund)
- PA must maintain audit trails of all transactions
- PA must not settle to accounts not verified via KYC
- PA must implement two-factor authentication for merchant dashboard

### 8.4 DPDP Act 2023 (Digital Personal Data Protection)

New compliance layer being enforced:
- Merchant must be a "Data Fiduciary" for their customer data
- Consent management must be documented
- Data localization: customer data must be stored in India
- Right to erasure: customers can request deletion of their payment data

---

## 9. Merchant Agreement

### 9.1 Key Clauses

**Financial Terms:**
- TDR (Transaction Discount Rate) per payment method — e.g., UPI: 0%, Debit Card: 0.4–0.9%, Credit Card: 1.5–2.5%, EMI: 1.5–2.5%, International Card: 2.5–3%
- Settlement cycle: T+1 / T+2 / T+3 business days
- Instant settlement fee (if applicable): typically 0.25–0.50% additional
- Rolling reserve: % and release schedule
- Chargeback fee: ₹250–₹500 per chargeback
- Dispute handling fee
- Setup fee (most modern PGs: zero)
- Annual maintenance charge (optional premium tier)

**Operational Terms:**
- Transaction limits per day/month (can be increased post-track-record)
- Refund processing timeline (merchant-initiated: T+5 to T+7 typically)
- Maximum hold on suspicious transactions

**Liability and Risk:**
- Who bears chargeback losses (merchant liability in most cases)
- What happens when merchant account is suspended (fund hold period)
- Indemnification clauses
- Force majeure

**Compliance Obligations:**
- Merchant must maintain PCI DSS compliance (or use hosted checkout which keeps merchant out of PCI scope)
- Merchant must not store card data
- Merchant must display proper policies on website (T&C, Privacy, Refund)
- Merchant must cooperate with disputes/chargebacks
- Merchant must update information when business details change (directors, address, bank account)

**Termination:**
- Right to terminate for breach
- Notice period (typically 30 days)
- Settlement of pending funds after termination
- Clawback rights for outstanding chargebacks post-termination

---

## 10. Technical Integration Requirements

### 10.1 Integration Modes

| Mode | PCI Scope? | Best For |
|------|-----------|----------|
| Hosted Checkout (Redirect) | Out of scope | Small merchants, quick go-live |
| Custom/Seamless Checkout | Full PCI-DSS required | Brands needing custom UX |
| SDK (Web/Mobile) | Partial scope | App-based businesses |
| Payment Links | Out of scope | Offline-to-online, invoicing |
| E-commerce Plugins | Out of scope | Shopify, WooCommerce merchants |

### 10.2 Security Checklist Before Go-Live

**API Security:**
- Client ID and Client Secret stored only in backend environment variables (never in frontend code, never in GitHub)
- All API calls server-to-server (no frontend exposure of secrets)
- API key rotation capability in place
- IP whitelisting configured (allowlist PG IP ranges)

**Webhook Security:**
- Webhook endpoint on HTTPS only
- Signature verification implemented (HMAC-SHA256) on every webhook event
- Idempotency: duplicate webhook events handled correctly (same payment_id processed only once)
- Webhook retry handling (PG will retry failed webhooks — merchant must respond with 200 OK)

**Data Security:**
- No card numbers, CVV, or expiry dates stored on merchant server
- PII (email, phone, address) stored with encryption at rest
- Transaction logs retained for regulatory compliance (5 years)

**Callback/Return URL Security:**
- Callback URL on HTTPS
- Payment status validated via API (never trust only the redirect callback — always verify via server-to-server status API call)
- Signature on callback parameters verified before marking order as paid

### 10.3 Mandatory Integration Tests Before Go-Live

| Test Scenario | Status |
|--------------|--------|
| Successful payment (card) | Pass |
| Successful payment (UPI) | Pass |
| Payment failure (insufficient funds) | Pass |
| Payment failure (wrong CVV) | Pass |
| Payment timeout / session expiry | Pass |
| Duplicate payment attempt | Pass |
| Refund initiation | Pass |
| Partial refund | Pass |
| Webhook delivery and processing | Pass |
| Signature verification | Pass |
| Pre-auth + capture (if applicable) | Pass |
| Pre-auth + cancel (if applicable) | Pass |

---

## 11. Rolling Reserve and Settlements

### 11.1 What is Rolling Reserve?

A rolling reserve is a percentage of the merchant's daily settlement that the payment gateway holds back for a defined period (typically 90–180 days) before releasing it. This protects the PG against future chargebacks, refunds, or merchant default.

**Typical Rolling Reserve Parameters:**

| Merchant Profile | Reserve % | Hold Period |
|-----------------|-----------|-------------|
| New merchant, low-risk | 5% | 90 days |
| New merchant, medium-risk | 10% | 180 days |
| High-risk category | 10–15% | 180–365 days |
| Established merchant (>1 year) | 0–5% or waived | N/A |

**Example:**
- Merchant settles ₹10 lakh on Day 1
- PG holds ₹50,000 (5%) as rolling reserve
- ₹9,50,000 settles to merchant's account
- After 90 days, the ₹50,000 is released (unless consumed by chargebacks)

### 11.2 Settlement Cycles

| Settlement Type | Timeline | Additional Cost |
|----------------|----------|----------------|
| Standard T+2 | 2 business days after transaction | None |
| T+1 | 1 business day after transaction | None (most PGs now default T+1) |
| T+0 (Same Day) | Same day (cutoff time applies) | None or small fee |
| Instant Settlement | Within 30–60 minutes | 0.25–0.50% additional fee |
| Custom cycle | Negotiated for enterprise | Negotiated |

### 11.3 Settlement Deductions

From each settlement, the following are deducted:
1. **TDR (Transaction Discount Rate)**: Platform fee per transaction
2. **GST on TDR**: 18% GST on the TDR amount
3. **Chargeback deductions**: Any CB amounts that were settled but disputed
4. **Refund deductions**: Merchant-initiated refunds for previous transactions
5. **Rolling reserve**: % withheld (if applicable)

---

## 12. Ongoing Monitoring Post-Onboarding

### 12.1 Transaction Monitoring

**Real-Time Fraud Signals:**
- Velocity checks: too many transactions from the same card/device/IP in a short window
- Geographic anomaly: card issued in USA used from an unknown Indian IP
- Ticket size anomaly: merchant who typically does ₹500 transactions suddenly attempts ₹2 lakh transactions
- Round-number transactions: ₹10,000 exactly from multiple cards = structuring red flag

**Chargeback Ratio Monitoring:**
- Visa threshold: 1% of monthly transactions (above = "Merchant Monitoring Program")
- Mastercard threshold: 1.5% of monthly transactions
- RBI guidance: Excessive chargeback merchants to be reported
- Action at breach: Warning → Enhanced monitoring → Settlement hold → Account suspension

### 12.2 Periodic Re-KYC

| Trigger | Frequency |
|---------|-----------|
| Director change | Immediate — new director KYC required |
| Bank account change | Immediate — penny drop for new account |
| Business model change | Immediate — website re-verification |
| Address change | Immediate — new address proof |
| Annual review (high-risk merchant) | Every 12 months |
| Annual review (standard merchant) | Every 24–36 months |

### 12.3 Website Re-Scanning

- Automated scans run periodically (typically monthly or quarterly)
- If merchant adds prohibited products/services after onboarding: account flagged
- Merchants must notify PG of significant website changes
- Some PGs use tools like PerimeterX or custom crawlers for ongoing scanning

### 12.4 Escalation Actions

| Trigger | Action |
|---------|--------|
| Chargeback rate > 1% | Warning letter + enhanced monitoring |
| Chargeback rate > 2% | Settlement hold (no payouts) |
| Prohibited product found on website | Account suspension |
| Sanctions list match for director | Immediate freeze |
| Regulatory notice to merchant | Funds hold pending clarification |
| Merchant becomes unresponsive to chargeback disputes | Account closure |
| Merchant GMV drops to zero for 90+ days | Dormancy review |

---

## 13. Special Requirements by Industry Vertical

### 13.1 NBFC / Fintech / Lending

- **RBI NBFC Registration Certificate** (CoR — Certificate of Registration)
- Authorized to collect repayments (relevant for loan disbursement apps)
- FLDG (First Loss Default Guarantee) arrangement may be required
- Regulatory risk: RBI can direct PG to freeze accounts of unlicensed lenders

### 13.2 Healthcare / Pharma

- **Drug License** (Form 20/21) for pharmacies
- Doctor / clinic registration proof
- For telemedicine: NMC (National Medical Commission) registration
- If selling medical devices: CDSCO registration

### 13.3 Education

- Trust Registration / Society Registration Certificate
- For coaching: educational society registration or private limited
- AICTE / UGC affiliation (for degree-granting institutions)
- No-objection for fee collection

### 13.4 Travel & Tourism

- **IATA Accreditation** (if selling airline tickets)
- Ministry of Tourism travel agent license
- For cab aggregators: state transport license
- Package tour operators: IATA / state tourism registration

### 13.5 Insurance

- **IRDAI License**: Direct insurance company license
- For aggregators/distributors: IRDAI Web Aggregator or Corporate Agent license
- Distribution agreement with insurance companies

### 13.6 Mutual Funds / Securities

- **SEBI Registration** (stockbroker, investment advisor, mutual fund distributor)
- AMFI (Association of Mutual Funds in India) registration number
- Not all PGs can onboard securities brokers — requires additional compliance review

### 13.7 Real Estate

- RERA (Real Estate Regulatory Authority) registration mandatory for developers
- MahaRERA / HRERA / equivalent state-level RERA number required
- Escrow account details (collected funds must go into RERA escrow)
- For Toucan's United Gates platform: this is specifically built for this vertical

### 13.8 Cryptocurrency / VDA (Virtual Digital Assets)

- Most PGs in India do not onboard crypto exchanges due to regulatory ambiguity
- VASPs (Virtual Asset Service Providers) must register with FIU-IND (mandatory post PMLA amendment 2023)
- FIU-IND registration number required
- Very few PGs accept this category — typically requires direct negotiation

### 13.9 Online Gaming / Fantasy Sports

- Highly state-dependent (Karnataka, Tamil Nadu, Telangana = prohibited; Maharashtra, Goa = licensed)
- TDG (Tender Document for Gambling) or state gaming license
- Dream11 / MPL model: skill gaming argued as non-gambling
- PGs generally require legal opinion + state license + legal counsel sign-off
- Separate MCC code (7801 for gambling, 7993 for video games)

### 13.10 NGO / Donation Collection

- 80G Certificate (tax exemption for donors)
- 12A / 12AA Certificate (tax exemption for the NGO)
- FCRA registration (Foreign Contribution Regulation Act) if accepting international donations
- Board resolution for authorized signatories
- RBI guidelines for donation collection are distinct from commercial transactions

---

## 14. Prohibited and Restricted Categories

### 14.1 Fully Prohibited (No PG in India can onboard)

| Category | Reason |
|----------|--------|
| Narcotic drugs, controlled substances | NDPS Act |
| Illegal weapons, ammunition | Arms Act |
| Ponzi / pyramid schemes | Sebi/SFIO |
| Child sexual abuse material | IPC |
| Human trafficking services | IPC |
| Counterfeit currency | RBI / IPC |
| Pirated software / content | Copyright Act |
| Wildlife products (CITES protected) | Wildlife Protection Act |
| Unlicensed money transfer / hawala | FEMA |

### 14.2 Restricted (Require Special Approvals / Specific PG Policies)

| Category | Restriction |
|----------|------------|
| Adult entertainment (age-verified) | Few PGs accept; requires age verification layer |
| Tobacco / nicotine products | Advertising restrictions; requires seller registration |
| Alcohol (e-commerce) | State excise license required; very few PGs accept |
| Pharmaceuticals without license | Drug Controller license required |
| Forex / binary options | SEBI/RBI license required |
| Online gambling | State-specific license; most PGs avoid |
| Firearms accessories (legal) | Weapons dealer license |
| Ayurveda / herbal claims | FSSAI / Ayush license required for health claims |
| Multi-Level Marketing | Pyramid/MLM restrictions under Prize Chit Act; extreme scrutiny |
| Crypto / VDA | FIU-IND registration; selective PGs only |

---

## 15. Summary Checklist

### Business Documents
- [ ] Business entity registration proof
- [ ] PAN card (business + individual directors/proprietors)
- [ ] Aadhaar card (all KYC persons)
- [ ] GST Certificate (if applicable)
- [ ] Board Resolution / Authorization Letter
- [ ] MoA + AoA / LLP Agreement / Partnership Deed
- [ ] Shareholding pattern / UBO declaration
- [ ] Cancelled cheque (current account in business name)
- [ ] Bank statement (last 3–6 months)
- [ ] Latest ITR / CA Certificate (for volume above ₹50L/month)

### Website Requirements
- [ ] Live HTTPS website with valid SSL
- [ ] Terms & Conditions page
- [ ] Privacy Policy page
- [ ] Refund & Cancellation Policy
- [ ] Shipping Policy (physical goods)
- [ ] Contact page (address, email, phone)
- [ ] Clear product/service description with pricing
- [ ] Payment button / checkout functional
- [ ] No prohibited content
- [ ] Domain age > 30 days

### Risk & Compliance
- [ ] MCC code confirmed and consistent with business
- [ ] Declared GMV consistent with bank statement / business size
- [ ] Sanctions / PEP screening passed
- [ ] Adverse media check passed
- [ ] No RBI/SEBI/ED/CBI notices against entity or directors
- [ ] UBO identified and KYC'd

### Technical Integration
- [ ] API keys secured in backend only
- [ ] HTTPS on all callback/webhook URLs
- [ ] Signature verification implemented
- [ ] All payment scenarios tested in sandbox
- [ ] Webhook handling tested (success, failure, refund)
- [ ] No card data stored on merchant server
- [ ] IP whitelist configured

### Agreement & Operations
- [ ] Merchant agreement signed
- [ ] TDR and settlement cycle confirmed
- [ ] Rolling reserve terms accepted
- [ ] Chargeback handling process documented
- [ ] Customer support contact operational

---

*This document reflects standard practices for Indian payment gateways operating under RBI PA/PG Master Directions 2020 and subsequent amendments. Specific requirements vary by gateway and may be updated as regulations evolve.*
