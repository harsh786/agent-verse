# Plural (Pine Labs) vs Toucan Pay — Deep Product Comparison

> **Scope:** Features present in **Plural (Pine Labs Online)** that are **absent or significantly underdeveloped in Toucan Pay**.
> This document is organized by product category and goes deep into each gap with context on why it matters.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Platform Overview at a Glance](#2-platform-overview-at-a-glance)
3. [Developer & Integration Experience](#3-developer--integration-experience)
4. [Merchant Onboarding Experience](#4-merchant-onboarding-experience)
5. [Payment Acceptance & Checkout](#5-payment-acceptance--checkout)
6. [Success Rate (SR) Optimization](#6-success-rate-sr-optimization)
7. [Affordability Suite (EMI / BNPL)](#7-affordability-suite-emi--bnpl)
8. [Subscriptions & Recurring Payments](#8-subscriptions--recurring-payments)
9. [Split Settlements & Marketplace Payments](#9-split-settlements--marketplace-payments)
10. [Payouts (Outward Transfers)](#10-payouts-outward-transfers)
11. [International Payments](#11-international-payments)
12. [User / Customer Checkout Experience](#12-user--customer-checkout-experience)
13. [Merchant Dashboard & Analytics](#13-merchant-dashboard--analytics)
14. [AI & Next-Generation Capabilities](#14-ai--next-generation-capabilities)
15. [Security & Compliance](#15-security--compliance)
16. [Pricing & Transparency](#16-pricing--transparency)
17. [Extended Payment Capabilities](#17-extended-payment-capabilities)
18. [Ecosystem & Partnerships](#18-ecosystem--partnerships)
19. [Support & Documentation Quality](#19-support--documentation-quality)
20. [Summary Gap Table](#20-summary-gap-table)

---

## 1. Executive Summary

Toucan Pay and Plural (Pine Labs Online) both target the Indian payments market with RBI compliance, PCI-DSS certification, and a comparable base set of payment modes (UPI, cards, net banking, wallets). However, the depth of Plural's product surface area is substantially larger across nearly every category.

**Where Toucan leads:**
- Cross-border payments via a dedicated PA-CB authorized product (CrossStream) with e-FIRA automation
- Real estate / PropTech vertical platform (United Gates) — an embedded, industry-specific solution
- Identity verification suite (PAN, Aadhaar, UPI ID, IFSC, GSTIN, Bank Account)
- Simple, flat-fee cross-border pricing ($24/$25/0.25%)

**Where Plural leads (the focus of this document):**
- Developer tooling (MCP Server, CLI, OpenAPI, Postman, Playground, Changelog)
- AI-native payments (MCP, P3P, Agent Toolkit, Agentic Commerce)
- Full Affordability Suite (Credit/Debit/Cardless/DownPayment EMI, BNPL, No-Cost/Low-Cost EMI, Brand EMI, Full Swipe Offers, IMEI Validation)
- Subscriptions with UPI AutoPay mandates, retries, non-revocable mandates
- Split Settlements for marketplace multi-party distribution
- Payouts (Individual + Bulk via IMPS/NEFT/RTGS/UPI)
- Tokenization (Network token vault, cross-PA/PG token migration)
- Convenience Fees (configurable per payment method)
- International payments: Apple Pay, DCC, MCC, Import Payments
- Payment Links with bulk upload, conversion analytics
- Pre-authorization / capture flow
- UPI Reserve Pay (SBMD)
- Pay by Points, E-Challan, Third Party Validation

---

## 2. Platform Overview at a Glance

| Attribute | Plural (Pine Labs Online) | Toucan Pay |
|-----------|--------------------------|------------|
| **Company** | Pine Labs (Plural is the online payments brand) | Toucan Payments |
| **HQ** | Noida / Pan-India | Hyderabad + Noida |
| **Scale claimed** | Established enterprise; named among largest PGs in India | $2B+ processed globally, 1B+ transactions |
| **Core products** | Gateway, Affordability, Subscriptions, Split, Payouts, Int'l, AI | AcquireFlow (gateway), CrossStream (cross-border), United Gates (PropTech) |
| **RBI Authorization** | Payment Aggregator | PA / PA-CB |
| **PCI DSS** | PCI DSS certified + PCI P2PE + PA-DSS validated | PCI DSS v4 Level 1 + PCI-SSF |
| **ISO 27001** | Not explicitly listed | ISO 27001 certified |
| **Docs quality** | Comprehensive developer portal with API reference, changelog, glossary | API docs via toucanus.com; marketing-level detail on main site |
| **AI capabilities** | MCP Server, P3P, Agent Toolkit, Agentic Commerce | Not present |
| **Target segments** | Startups → Enterprise, Marketplaces, SaaS, D2C, BFSI | Startups, SMBs, Real Estate, Cross-border exporters |

---

## 3. Developer & Integration Experience

This is the **widest gap** between the two platforms. Plural invests heavily in developer tooling infrastructure that Toucan has not yet built.

### 3.1 SDK Depth and Language Coverage

**Plural:**
- **Web SDKs:** 6 distinct SDK variants for frontend web integration
- **Mobile SDKs:** 6 SDK variants (Android, iOS, Flutter, React Native, etc.)
- **Server SDKs:** 8 server-side SDK variants (Java, Node.js, Python, Go, PHP, Ruby, .NET, and more)
- **SDK Best Practices guide** documenting versioning, error handling, retries, and security

**Toucan:**
- Mentions SDKs and REST APIs on the marketing site
- API documentation hosted at `toucanus.com/api-docs` (separate domain, not integrated into the product site)
- No public breakdown of SDK languages or platform coverage
- No SDK best practices documentation found

**Impact:** Developers evaluating a payment gateway typically look for SDK coverage in their language before committing to integration. Plural's 8 server SDKs vs. Toucan's unspecified offering is a meaningful adoption barrier.

---

### 3.2 CLI Tool

**Plural:**
- Has a published **CLI (Command-Line Interface)** tool that allows developers to interact with the Plural API from the terminal
- Enables scripting, automation of test scenarios, API exploration without a UI
- Documented at `/docs/online-payments/cli`

**Toucan:**
- No CLI tool mentioned or documented anywhere on the public site

**Impact:** A CLI is critical for DevOps automation, CI/CD pipeline integration, and rapid prototyping. Its absence makes Toucan less attractive for engineering-led organizations.

---

### 3.3 MCP Server (AI-Native API Access)

**Plural:**
- A hosted **Model Context Protocol (MCP) Server** that connects AI coding assistants (Claude, Cursor, VS Code Copilot, GitHub Copilot) directly to Plural APIs via natural language
- Developers can query APIs, generate integration code, and test flows by talking to their AI tool — no Postman, no manual auth
- Per-request credentials, PII masking, no data persisted on MCP layer — secure by design
- Requirements: Node.js 22+, active Plural merchant account

**Toucan:**
- No MCP server, no AI-native tooling mentioned

**Impact:** The MCP approach is rapidly becoming the standard way AI-assisted development tools interact with external services. A payment gateway with an MCP server provides a significant DX advantage in 2025–2026.

---

### 3.4 OpenAPI Spec & Postman Collections

**Plural:**
- **Downloadable OpenAPI Specification** for all Plural Online APIs — enables automatic client code generation in any language via openapi-generator or similar tools
- **Postman Collection** available for immediate API exploration and testing without writing any code
- Both are linked prominently from the developer portal header

**Toucan:**
- No OpenAPI spec found
- No Postman collection found on public site

**Impact:** OpenAPI specs are the foundation for automated SDK generation, API mocking (for CI), and third-party integrations. Without it, every integration is manual.

---

### 3.5 Interactive API Playground

**Plural:**
- A live **Playground** environment (`/docs/online-payments/playground`) where developers can test and validate payment integrations in a sandboxed environment before going live
- Zero setup — test without production credentials

**Toucan:**
- Mentions an "API Sandbox" for developers but no interactive playground documented or accessible from the marketing site
- Sandbox appears to require contacting the team / onboarding

**Impact:** A self-serve playground enables zero-friction evaluation. Requiring a demo call or account creation before seeing the product in action increases time-to-integration.

---

### 3.6 Changelog & Versioning

**Plural:**
- Publicly accessible, versioned **Changelog** at `/docs/online-payments/changelog` listing API changes, new features, deprecations
- Allows developers and integration teams to track breaking changes proactively

**Toucan:**
- No public changelog found

**Impact:** For production payment systems, API stability and advance notice of changes is critical. No changelog = no trust for enterprise buyers.

---

### 3.7 Documentation Quality Indicators

**Plural:**
- Integrated developer portal at `harsh.com/docs/online-payments`
- AI-powered search ("Ask AI") on every doc page
- Every doc page has "Copy for LLM", "Open in ChatGPT", "Open in Claude", "Open in Perplexity" — making the docs instantly AI-queryable
- Glossary of payment terms
- Go-Live Checklist for production readiness
- Webhook documentation (4 webhook types with schemas)
- Error codes organized into 8 categories with resolution guidance
- IP & Cipher documentation for network security configuration
- Test card details for sandbox testing

**Toucan:**
- Developer docs hosted on a separate domain (`toucanus.com/api-docs`) — not integrated into the main product site
- No AI search on docs
- No glossary found
- No go-live checklist found
- No public webhook documentation found
- No error code catalog found

**Impact:** Documentation quality is often the first proxy developers use to judge a payment gateway's engineering culture and product maturity.

---

### 3.8 GitHub Presence

**Plural:**
- Public GitHub organization: `github.com/plural-harsh/`
- Open repositories for SDKs, sample integrations

**Toucan:**
- No public GitHub organization found

---

## 4. Merchant Onboarding Experience

### 4.1 Self-Serve Signup with Immediate UAT Access

**Plural:**
- Self-serve account creation at `dashboardv2.pluralonline.com/signup` with business email
- Email verification → immediate access to UAT credentials and API keys
- No human touchpoint required to start testing
- Go live without production credentials — full sandbox accessible from day one

**Toucan:**
- Onboarding mentioned as "100% Digital" and "Go live in 2 hours" / "Paperless and same day onboarding"
- However, the primary CTA throughout the site is "Request Demo" or "Contact Sales" — suggesting human-assisted onboarding
- No direct "Create Account" link on main site that bypasses a contact form

**Impact:** Developer-led organizations (SaaS, startups) want to integrate before talking to sales. Plural's self-serve model reduces time-to-first-payment from days to hours.

---

### 4.2 Dashboard-Based API Key Management

**Plural:**
- Dashboard → Settings → API Keys flow for generating and rotating test-mode credentials
- Clearly documented with screenshots in official docs

**Toucan:**
- API keys appear to be provisioned during onboarding by the team
- No self-serve key rotation documented

---

### 4.3 Merchant Onboarding Documentation

**Plural:**
- Full dashboard sign-up guide at `/docs/online-payments/dashboard/sign-up`
- Step-by-step integration guide with code samples in multiple languages

**Toucan:**
- Has a "Merchant Onboarding Policy" page on the website (compliance-focused)
- Integration steps described in 4 bullet points (marketing level)

---

## 5. Payment Acceptance & Checkout

### 5.1 Hosted Checkout vs. Custom Checkout Options

**Plural:**

| Checkout Type | Description | PCI Required | Customization |
|--------------|-------------|-------------|---------------|
| **Hosted Checkout (Redirect Flow)** | Customer redirected to Plural-hosted page | No | Low |
| **Custom Checkout (UI Customization Flow)** | Branded checkout with Plural components | Yes (PCI certificate) | High |
| **SDK-based** | Embedded in app/web with custom UI | Varies | Very High |
| **Plugins** | Platform-native (Shopify, WooCommerce, etc.) | No | Platform-level |

**Toucan:**
- Mentions REST APIs, SDKs, and plugins
- No formal distinction between hosted and custom checkout flows in documentation
- No pre-auth / capture flow documented

**Impact:** The hosted vs. custom checkout split matters enormously. Merchants without PCI certification cannot use custom checkout. Plural explicitly handles this with hosted checkout (no PCI needed). Toucan's checkout options are not clearly delineated.

---

### 5.2 Pre-Authorization (Auth & Capture)

**Plural:**
- **Pre-authorization** supported: `pre_auth: true` flag in Create Order API
- Separate **Capture Order** and **Cancel Order** APIs
- Order status lifecycle: `CREATED → AUTHORIZED → PROCESSED` or `CANCELLED`
- Allows merchants to hold funds and capture only after fulfillment (critical for travel, hotel, marketplace escrow)

**Toucan:**
- No pre-authorization flow documented
- Settlement flow appears to be immediate capture only

**Impact:** Pre-auth is a hard requirement for hotel/travel bookings, on-demand services, and marketplaces where fulfillment confirmation precedes charge.

---

### 5.3 Tokenization / Card Vault

**Plural:**
- Full **Network Tokenization** support compliant with COFT (Card on File Tokenization) RBI mandate
- Two token types:
  - **Platform Token** (Plural-managed token ID)
  - **Service Provider Token** (Visa/Mastercard network-issued token)
- **Cross-PA/PG Token Migration**: Process payments using tokens created on another payment aggregator — critical for merchants migrating from competitors
- Save-card flow for new users, pay-by-saved-token for returning users
- Customer Vault with customer profile management (billing address, shipping address, metadata)

**Toucan:**
- Mentions "Interoperable Tokenization" and "RBI-compliant secure token management" at marketing level
- No technical documentation on token types, vault APIs, or cross-PA migration

**Impact:** Tokenization reduces checkout friction significantly (one-click repeat purchases). The cross-PA token migration feature directly enables merchants to migrate from another gateway without losing saved card relationships.

---

### 5.4 Payment Links

**Plural:**
- Create via **API or Dashboard**
- **Bulk upload** (hundreds of payment links at once via dashboard CSV upload)
- **Real-time tracking and analytics**: conversion rates, preferred payment methods, transaction success
- **Multi-channel automated sharing**: Auto-send via SMS, email
- **Customizable checkout** for payment links (branding, payment methods, expiry)
- Full **lifecycle status tracking**: CREATED → CLICKED → PAYMENT_INITIATED → PROCESSED / CANCELLED / EXPIRED
- Link expiry management

**Toucan:**
- Payment links mentioned as a no-code collection method
- "E-commerce plugins" and "payment buttons" mentioned in FAQs
- No bulk upload, no conversion analytics, no lifecycle tracking documented

**Impact:** For D2C brands and field sales teams, bulk payment links and conversion analytics are table-stakes features. The "clicked but not paid" tracking alone enables significant revenue recovery.

---

### 5.5 Payment Methods Depth

**Plural:** 10 documented payment method categories including Apple Pay (under international), UPI Reserve Pay (SBMD), and Pay by Points (loyalty reward redemption)

**Toucan:** Supports UPI, cards, net banking, wallets, EMI — standard set, no Apple Pay, no loyalty point redemption documented

---

## 6. Success Rate (SR) Optimization

### 6.1 Smart Routing

**Plural:** Not explicitly called out as a standalone feature in docs (routing happens internally)

**Toucan:** Claims "AI-Driven Smart Routing" that "Boosts success rate by 40%" — this is actually a **Toucan strength** vs. Plural in terms of marketing claims

### 6.2 Automatic Retry Logic

**Plural:**
- In Subscriptions: **3 automatic retries** on debit failure — 10 minutes after failure, then 1 hour later, then a third attempt
- Merchant-controlled retry APIs also available

**Toucan:**
- No retry logic documented for any payment flow

### 6.3 Fraud Risk Management (FRM)

**Plural:**
- Dedicated **FRM engine** for international card transactions
- Risk evaluation across customer, device, and transaction-level factors
- Automatic blocking of high-risk transactions with error code return
- Described as a comprehensive risk engine that prevents fraudulent activity before it reaches the bank

**Toucan:**
- "AI Fraud Detection" and "Proactive AI Fraud Prevention" claimed at marketing level
- "3D Secure 2.0 + Risk Engine" mentioned
- No technical detail on risk scoring, signal types, or automatic blocking

### 6.4 Third Party Validation (TPV)

**Plural:**
- Full **Third Party Validation** documented (4 sub-sections)
- VPA verification: Verify the Virtual Payment Address linked to a bank account before mandate creation — prevents mandate creation fraud

**Toucan:**
- Not documented

---

## 7. Affordability Suite (EMI / BNPL)

This is one of the **largest product gaps**. Plural has a fully built-out Affordability Suite with multiple EMI types, offer structures, and IMEI validation. Toucan has EMI on the roadmap as "Coming Soon" for Credit/Debit Card EMI.

### 7.1 EMI Types Comparison

| EMI Type | Plural | Toucan |
|----------|--------|--------|
| **Credit Card EMI** | Full credit limit deducted upfront, repaid in EMIs | "Coming Soon" |
| **Debit Card EMI** | Pre-approved debit cardholders, monthly deductions from savings | "Coming Soon" |
| **Cardless EMI (BNPL)** | Mobile number / PAN / KYC, pre-approved NBFC limits, no card needed | Not present |
| **Down Payment EMI** | Upfront down payment + EMI balance; supports interest subvention, cashback | Not present |
| **No-Cost EMI** | Zero additional cost — interest fully subsidized by brand/partner | Not present |
| **Low-Cost EMI** | Reduced interest, subvention shared by brand/partner | Not present |
| **Standard EMI** | Full interest borne by customer | Not present |

### 7.2 Bank EMI vs. Brand EMI

**Plural:**
- **Bank EMI**: Partnerships with leading banks for broad accessibility
- **Brand EMI**: Brand-specific EMI offers supported by individual brands (e.g., Samsung EMI offer)
- **Bundled Offers**: Package brand EMI with additional benefits
- **Split EMI**: Split the EMI further for high-value items

**Toucan:**
- No Bank EMI or Brand EMI product documented

### 7.3 Full Swipe Offers

**Plural:**
- **Full Swipe Offer**: Customer pays full amount upfront, receives cashback funded by brand + issuing bank collaboration
- Example: ₹20,000 purchase → ₹2,000 cashback funded by brand + bank
- Managed through predefined stakeholder agreements with clear funding responsibilities

**Toucan:**
- Not present

### 7.4 Instant Cashback on UPI

**Plural:**
- **Instant Cashback on UPI** — documented under Affordability Suite
- Allows brands to fund instant discounts on UPI payments

**Toucan:**
- Not present

### 7.5 IMEI Validation

**Plural:**
- **IMEI Validation** for device-linked EMI offers
- Validates device IMEI before enabling specific hardware brand EMI
- Critical for electronics/consumer durables merchants

**Toucan:**
- Not present

### 7.6 Business Impact of Missing Affordability Suite

EMI/BNPL adoption in India directly correlates with Average Order Value (AOV) and conversion rates, especially for:
- Electronics (₹30,000+ purchases)
- Furniture (₹50,000+ purchases)
- Travel (₹20,000+ bookings)
- D2C premium brands

Without an Affordability Suite, Toucan merchants lose sales to competitors whose checkout offers EMI options. This is not a minor gap — it is a conversion-critical feature for any merchant selling goods above ₹5,000.

---

## 8. Subscriptions & Recurring Payments

### 8.1 Subscription Models

**Plural:**
- **Fixed Frequency**: Fixed amount at fixed intervals (daily, weekly, monthly, yearly)
- **Variable Frequency**: Fixed amount, customer-chosen interval
- Full API lifecycle management (Create, Pause, Resume, Cancel, Modify)
- UPI AutoPay mandate-based — RBI-compliant, consent-driven

**Toucan:**
- "SaaS & Subscriptions" listed as a use case: "Recurring billing, dunning management, usage-based pricing, international invoicing"
- No technical documentation on subscription APIs, mandate types, or lifecycle management found on public site

### 8.2 Mandate Types

**Plural:**
- **One-Time Mandate**: Single debit, auto-expires after use
- **On-Demand Mandate**: Merchant-initiated debits at any time, varying amounts, within validity period
- **Recurring Mandate**: Automated debits at fixed intervals with fixed or variable amounts

**Toucan:**
- Not documented

### 8.3 Key Subscription Features

**Plural:**
| Feature | Plural | Toucan |
|---------|--------|--------|
| Auto-debit execution | Yes (PL system executes automatically) | Not documented |
| Pre-debit notification | Yes (minimum 24 hours before debit) | Not documented |
| Automatic retry on failure | Yes (3 retries: 10min, 1hr, then final) | Not documented |
| Merchant-controlled retry API | Yes | Not documented |
| Non-Revocable Mandate | Yes (customer cannot modify post-setup) | Not documented |
| Trial Period | Yes (define days, auto-debit after trial) | Not documented |
| VPA third-party verification | Yes | Not documented |
| Customer cancellation | Yes (merchant or customer initiated) | Not documented |

### 8.4 Subscription Dashboard

**Plural:**
- Real-time monitoring: subscription status, payment success rates, customer activity
- Revenue analytics: mandates created, successful debits, status changes
- Customer management: manage subscriptions, process refunds

**Toucan:**
- Not documented

### 8.5 Business Impact

For SaaS companies, OTT platforms, insurance, investment apps, and utility services — subscriptions are the entire revenue model. Toucan's absence of documented subscription infrastructure is a major gap for these segments.

---

## 9. Split Settlements & Marketplace Payments

### 9.1 Split Settlement

**Plural:**
- **Automatic distribution** of a single transaction's settlement amount among multiple entities
- Designed for: marketplaces, franchise networks, travel agencies, food delivery platforms
- Use cases: E-commerce seller commission splits, restaurant + delivery partner + platform fee splits
- Sub-merchant dashboard for direct, transparent payouts
- Earning visibility for sub-merchants/partners
- Automated reconciliation

**Toucan:**
- CrossStream mentions "split settlements" for marketplace cross-border payments and "automated reconciliation" in the cross-border context only
- No domestic split settlement product documented

### 9.2 Business Impact

Marketplaces (Meesho, Swiggy clones, travel aggregators) cannot operate without split settlement. It is a fundamental infrastructure requirement for any multi-vendor platform. Without this, every marketplace merchant must run their own payout logic, which introduces compliance, reconciliation, and operational overhead.

---

## 10. Payouts (Outward Transfers)

### 10.1 Payout Infrastructure

**Plural:**
- Full **Payouts API platform** supporting:
  - **Individual Payouts**: One-off transfers to a single beneficiary
  - **Bulk Payouts**: Batch disbursements to multiple beneficiaries simultaneously
- Transfer rails: **IMPS**, **NEFT**, **RTGS**, **UPI**
- Beneficiary types: Bank accounts, UPI IDs, Wallets
- Role-based dashboard with authorization workflows
- Account verification, balance inquiry, transaction status tracking (VAS)
- Real-time tracking with downloadable reports
- 99.9% uptime SLA, sub-second API response times
- Bank-grade security, end-to-end encryption

**Toucan:**
- "Instant Settlements" at 0.30% is documented — this is inward fund settlement to the merchant's bank
- No outward Payouts API (sending money to third parties) documented

### 10.2 Use Cases Enabled by Payouts

| Use Case | Enabled by Plural Payouts | Toucan Status |
|----------|--------------------------|---------------|
| Vendor/supplier disbursements | Yes | Not present |
| Insurance claim payouts | Yes | Not present |
| Gig economy worker payments | Yes | Not present |
| Refund-to-source alternative (bank transfer) | Yes | Not present |
| Festival bonus / incentive payouts | Yes | Not present |
| Lending disbursements | Yes | Not present |

### 10.3 Business Impact

Payouts are the second half of the payments equation. Any platform that collects money also needs to distribute it. Without a Payouts API, Toucan merchants must use a separate vendor for disbursements, creating reconciliation complexity and vendor management overhead.

---

## 11. International Payments

Both platforms support international payments, but the feature depth differs significantly.

### 11.1 Feature Comparison

| Feature | Plural | Toucan |
|---------|--------|--------|
| International card acceptance | Yes | Yes (2.99%) |
| Cross-border inward remittance | Yes (import payments) | Yes (CrossStream) |
| e-FIRA / compliance docs | Not explicitly mentioned | Yes (auto-generated) |
| **Apple Pay** | Yes | No |
| **Dynamic Currency Conversion (DCC)** | Yes | No |
| **Multi-Currency Conversion (MCC)** | Yes | No |
| Supported currencies (inward) | 50+ | 140+ (claimed on pricing page) |
| Virtual global accounts | Not highlighted | Yes (CrossStream, 190+ countries) |
| T+1 INR settlement | Yes | Yes |
| FRM for international cards | Yes (documented) | Mentioned at marketing level |
| Cross-border export payments | Via standard card flow | Via CrossStream with PA-CB |

### 11.2 Apple Pay

**Plural:**
- Full Apple Pay integration documented under International Payments
- Enables frictionless checkout for iPhone/Mac users without entering card details
- Growing adoption in India among premium segment customers

**Toucan:**
- Not mentioned anywhere

### 11.3 Dynamic Currency Conversion (DCC)

**Plural:**
- **DCC**: International cardholders can choose to pay in their home currency at checkout
- They see the amount in USD/EUR/GBP on the checkout page
- Increases conversion for international customers (familiar currency = less hesitation)
- Exchange rate is shown transparently before confirmation

**Toucan:**
- Not mentioned

### 11.4 Multi-Currency Conversion (MCC)

**Plural:**
- **MCC**: Merchants can price products and accept payment in multiple currencies and settle in INR
- Distinct from DCC — MCC is merchant-initiated multi-currency pricing

**Toucan:**
- Not documented beyond flat international card rate (2.99%)

---

## 12. User / Customer Checkout Experience

### 12.1 Mobile-Optimized Checkout

**Plural:**
- Separate mobile-optimized and web-optimized checkout interfaces documented with screenshots
- Subscription checkout specifically designed for smaller screens
- SDK-based in-app checkout for smooth embedded payment experience

**Toucan:**
- Responsive checkout assumed but not separately documented or shown

### 12.2 One-Click / Saved Card Payments

**Plural:**
- Customer Vault with token management enables one-click repeat purchases
- Returning customers skip re-entering card details (only CVV required)
- Cross-PA token migration means customers' saved cards from other gateways can be used

**Toucan:**
- Tokenization mentioned but no one-click flow or customer vault API documented

### 12.3 Subscription Checkout Interface

**Plural:**
- Custom subscription mandate checkout showing:
  - Mandate duration
  - One-time fee
  - Maximum amount
  - Recurring amount
- Separate mobile and web views documented with screenshots

**Toucan:**
- Not applicable (subscriptions not documented)

### 12.4 Checkout Customization for Payment Links

**Plural:**
- Branding customization on payment links
- Payment method filter (enable/disable specific methods per link)
- Expiry date configuration
- Conversion tracking per link

**Toucan:**
- Basic payment links; no customization documentation found

---

## 13. Merchant Dashboard & Analytics

### 13.1 Dashboard Feature Coverage

| Feature | Plural | Toucan |
|---------|--------|--------|
| Payments tracking | Yes | Yes |
| Refunds management | Yes | Yes |
| Settlements | Yes | Yes |
| **Brand Wallets** | Yes | No |
| **Payouts dashboard** | Yes | No |
| Payment Links dashboard | Yes | Not documented |
| Subscription management | Yes | No |
| **Webhook configuration UI** | Yes | Not documented |
| **API key management** | Self-serve (Settings → API Keys) | Team-provisioned |

### 13.2 Brand Wallets

**Plural:**
- **Brand Wallets** is a separate dashboard section
- Manages loyalty wallets, reward balances associated with brand EMI and cashback offers
- Unique to Plural's Affordability Suite ecosystem

**Toucan:**
- No loyalty wallet infrastructure

### 13.3 Subscription Dashboard

**Plural:**
- Mandate creation count
- Successful debit count and rate
- Status change history per mandate
- Customer-level subscription management
- Refund processing from dashboard

**Toucan:**
- Not applicable

### 13.4 Settlement Tracking

**Plural:**
- Dedicated settlements view
- Filter by date, payment method, status

**Toucan:**
- "Automated settlement and refund reports" mentioned
- Specific dashboard filtering not documented

---

## 14. AI & Next-Generation Capabilities

Plural is the **only** payment gateway in India with a publicly documented, production-ready AI-native payments stack. This is a 12–18 month product lead.

### 14.1 MCP Server (Model Context Protocol)

**Plural:**
- Hosted MCP Server — zero infrastructure to deploy by the merchant
- Connects Claude, Cursor, VS Code Copilot, GitHub Copilot, or any MCP-compatible AI tool directly to Plural APIs
- Use cases:
  - Query transaction history in natural language ("Show me all failed UPI transactions yesterday")
  - Generate working integration code ("Write me a Node.js function to create an order with EMI option")
  - Test payment flows without Postman
  - Support/ops teams querying live data without SQL or dashboard access
- Security: Per-request credentials, PII masking, nothing persisted in MCP layer

**Toucan:**
- No AI-native tooling

### 14.2 Pine Labs Payments Protocol (P3P)

**Plural:**
- **P3P** is an HTTP-native protocol for autonomous AI agents to pay APIs
- Built on the **402 Payment Required** HTTP status code
- Enables AI agents to autonomously initiate and complete payments as part of multi-step agentic workflows
- Example: An AI travel agent that books flights, hotels, and pays — without human intervention at the payment step
- This is infrastructural — P3P positions Plural as the payment rail for the agentic web

**Toucan:**
- No equivalent

### 14.3 Agent Enablement Toolkit

**Plural:**
- Pre-built tool integrations for major AI agent frameworks:
  - **LangChain** (Python & JavaScript)
  - **OpenAI Agents SDK**
  - **Vercel AI SDK**
- Pre-built tools for: orders, refunds, payment status, and payment initiation
- Enables function calling — AI models can invoke Plural APIs as native tools
- Documented at `/docs/online-payments/ai/agent-enablement-toolkit`

**Toucan:**
- No agent framework integrations

### 14.4 Agentic Commerce Suite

**Plural:**
- Full documentation on how AI-powered conversations transform digital payments
- Covers conversational checkout, AI-assisted payment recovery, intelligent offer personalization
- Positions Plural for the next evolution of e-commerce (WhatsApp/voice-initiated payments)

**Toucan:**
- No equivalent

### 14.5 Business Impact of AI Gap

The AI tooling gap matters in two ways:
1. **Developer adoption**: Engineers choosing a payment gateway for a new AI-first product will default to the gateway with an MCP server and agent toolkit
2. **Future payment paradigm**: As agentic AI takes over transactional workflows (booking, purchasing, subscribing), payment gateways without P3P/agent support will be bypassed

---

## 15. Security & Compliance

### 15.1 Certification Comparison

| Certification | Plural | Toucan |
|--------------|--------|--------|
| **PCI DSS** | Certified | Level 1 Certified |
| **PCI P2PE** | Certified | Not mentioned |
| **PA-DSS** | Validated | Not mentioned |
| **PCI-SSF** | Not mentioned | Certified |
| **ISO 27001** | Not mentioned | Certified |
| **SOC 2 Type II** | Not mentioned | Certified (cross-border) |
| **GDPR** | Not mentioned | Compliant (cross-border) |
| **RBI PA License** | Yes | Yes |
| **RBI PA-CB License** | Not highlighted | Yes |
| **UKAS Management** | Yes | Not mentioned |

**Key point:** Plural has **PCI P2PE** (Point-to-Point Encryption) and **PA-DSS validation** — these are distinct from PCI DSS and are particularly relevant for card-present and high-security use cases. Toucan has ISO 27001 and SOC 2 which are more relevant for enterprise security reviews.

### 15.2 Signature Verification

**Plural:**
- Mandatory signature verification step documented with Java/multi-language sample code
- SHA-256 HMAC-based signature on all payment callbacks
- Prevents spoofed payment notifications

**Toucan:**
- Not documented on public site

---

## 16. Pricing & Transparency

### 16.1 Pricing Model Comparison

| Payment Type | Plural | Toucan |
|-------------|--------|--------|
| Standard transactions | Not publicly listed (contact sales) | 1.95% platform fee |
| UPI on Credit Cards | Not listed | "Coming Soon" |
| International cards | Not listed | 2.99% |
| Instant settlements | Not listed | 0.30% |
| Virtual Bank Account | Not listed | ₹20/txn |
| Cross-border (inward) | 2.99% (from product page) | $24 flat / $25 flat / 0.25% |

**Toucan Strength:** Toucan publishes **specific rates publicly** including the cross-border flat fee model, which is highly differentiated and transparent. Plural's domestic gateway rates are not publicly listed.

**Plural Strength:** Plural's pricing for cross-border is percentage-based (industry standard), while Toucan's flat-fee model can be more economical for small transactions.

### 16.2 Convenience Fees (Surcharge Passthrough)

**Plural:**
- Full **Convenience Fee** configuration — merchants can define fees per payment method (fixed amount, percentage, or combination)
- Configured at the merchant ID level, enforced at checkout
- Fee calculation API available
- System-initiated refunds include convenience fee; customer-initiated refunds exclude it

**Toucan:**
- No convenience fee configuration documented

**Impact:** Convenience fees allow merchants to recover gateway costs — particularly important for low-margin businesses (utilities, government payments, insurance). Without this, the cost of payments is borne entirely by the merchant.

---

## 17. Extended Payment Capabilities

### 17.1 Pay by Points

**Plural:**
- **Pay by Points** — loyalty reward redemption at checkout
- Documented with 3 sub-pages (likely covering integration, use cases, and dashboard)
- Enables merchants to accept loyalty points as payment currency (or partial payment)
- Critical for airlines, hotel chains, co-branded card programs

**Toucan:**
- Not mentioned

### 17.2 Payments E-Challan

**Plural:**
- **Payments E-Challan** — government/semi-government payment collection integration
- Generates electronic challans for fee collection (traffic fines, utility dues, government service fees)
- 3 sub-pages of documentation
- Positions Plural for government-adjacent markets (municipal corporations, transport authorities)

**Toucan:**
- Not mentioned (though "Andhra Pradesh Capital Region Development Authority" is listed as a client, suggesting some government use)

### 17.3 UPI Reserve Pay (SBMD)

**Plural:**
- **UPI Reserve Pay / SBMD (Single Block Multiple Debit)** — advanced UPI feature
- Customer pre-authorizes a UPI block; merchant can debit multiple times from that block
- Use cases: Deposit-based bookings, travel reservations, rental businesses
- 3 sub-pages of documentation

**Toucan:**
- Not documented

### 17.4 Third Party Validation

**Plural:**
- 4-section documented TPV flow
- VPA (Virtual Payment Address) verification against bank account information
- Critical for subscription mandates — prevents fraudulent mandate creation with incorrect VPA

**Toucan:**
- Not documented

---

## 18. Ecosystem & Partnerships

### 18.1 E-Commerce Plugin Coverage

| Platform | Plural | Toucan |
|----------|--------|--------|
| **Shopify** | Yes | Yes |
| **WooCommerce** | Yes | Yes |
| **Magento** | Yes | Yes |
| **OpenCart** | Yes | Yes |
| **Wix** | Not mentioned | Mentioned in FAQs |
| **Zoho** | Not mentioned | Mentioned in FAQs |

Both platforms cover the main e-commerce platforms. Toucan claims Wix and Zoho as well.

### 18.2 Juspay Integration

**Toucan:**
- "Now live on Juspay. Connect today" — Juspay is a smart routing / orchestration layer used by many major merchants
- This gives Toucan access to merchants already using Juspay for multi-PG routing

**Plural:**
- Not mentioned

### 18.3 n8n / No-Code Automation

**Plural:**
- AI Solutions page references n8n (no-code workflow automation) integration
- Enables non-technical teams to build payment workflows without code

**Toucan:**
- Not mentioned

---

## 19. Support & Documentation Quality

### 19.1 Support Channels

**Plural:**
- 24/7 technical support (stated in Payouts section)
- `pgsupport@harsh.com` for feature activation requests
- Contact support via developer portal
- GitHub issues (via public org)
- Documentation: FAQ section, Go-live checklist

**Toucan:**
- 24x7 Technical and Merchant Support mentioned
- `sales@toucanpay.in` primary contact
- Dedicated Account Manager with fortnightly check-ins (premium AMC plan)
- Priority support with 30-min assured response time (premium plan)
- 20% faster dispute resolution (premium plan)

**Toucan Advantage:** Toucan's premium support tier with dedicated account manager and guaranteed response times is a concrete differentiator for mid-market merchants who value relationship-based support.

### 19.2 Migration Guide

**Plural:**
- Dedicated guide: **"Migrate to Pine Labs Payment Gateway"**
- Step-by-step migration from another PG
- Addresses cross-PA token migration (preserving saved cards)

**Toucan:**
- No migration guide found

---

## 20. Summary Gap Table

The following table summarizes features **present in Plural but absent in Toucan**. Features where Toucan leads or matches are not listed.

| Category | Feature | Plural | Toucan | Impact |
|----------|---------|--------|--------|--------|
| **Developer Experience** | MCP Server (AI-native API access) | Yes | No | High |
| | CLI Tool | Yes | No | Medium |
| | OpenAPI Spec download | Yes | No | High |
| | Postman Collection | Yes | No | Medium |
| | Interactive API Playground | Yes | Sandbox (human-assisted) | High |
| | Changelog (public, versioned) | Yes | No | Medium |
| | GitHub public organization | Yes | No | Medium |
| | SDK Best Practices guide | Yes | No | Low |
| | Web SDKs (6 types) | Yes | Unspecified | High |
| | Mobile SDKs (6 types) | Yes | Unspecified | High |
| | Server SDKs (8 types) | Yes | Unspecified | High |
| | AI-powered doc search | Yes | No | Low |
| | Error code catalog (8 categories) | Yes | No | Medium |
| | Webhook documentation | Yes | No | High |
| | Go-Live Checklist | Yes | No | Medium |
| | Glossary | Yes | No | Low |
| **Onboarding** | Self-serve signup with immediate UAT | Yes | Partially | High |
| | Self-serve API key rotation | Yes | No | Medium |
| | Migration guide from other PG | Yes | No | Medium |
| **Payment Acceptance** | Pre-authorization (Auth & Capture) | Yes | No | High |
| | Network Tokenization (Visa/MC tokens) | Yes | Mentioned only | High |
| | Cross-PA/PG token migration | Yes | No | High |
| | Customer Vault / Profile management | Yes | No | Medium |
| | Payment link bulk upload | Yes | No | Medium |
| | Payment link conversion analytics | Yes | No | Medium |
| | Payment link lifecycle tracking | Yes | No | Medium |
| | Hosted checkout (no PCI) | Yes | Not documented | High |
| | Custom checkout (branded UI) | Yes | Not documented | High |
| **Affordability** | Credit Card EMI | Yes | Coming Soon | Critical |
| | Debit Card EMI | Yes | Coming Soon | Critical |
| | Cardless EMI (BNPL) | Yes | No | Critical |
| | Down Payment EMI | Yes | No | High |
| | No-Cost EMI | Yes | No | High |
| | Low-Cost EMI | Yes | No | High |
| | Bank EMI | Yes | No | High |
| | Brand EMI | Yes | No | High |
| | Bundled Offers | Yes | No | Medium |
| | Split EMI | Yes | No | Medium |
| | Full Swipe Offers (cashback) | Yes | No | Medium |
| | Instant Cashback on UPI | Yes | No | Medium |
| | IMEI Validation | Yes | No | Medium |
| **Subscriptions** | UPI AutoPay mandate creation | Yes | No | Critical |
| | Fixed Frequency subscriptions | Yes | No | Critical |
| | Variable Frequency subscriptions | Yes | No | High |
| | Non-revocable mandates | Yes | No | High |
| | Pre-debit notifications (24hr) | Yes | No | High |
| | Auto-retry on failure (3x) | Yes | No | High |
| | Trial period support | Yes | No | High |
| | One-Time / On-Demand / Recurring mandate types | Yes | No | High |
| | Subscription dashboard analytics | Yes | No | Medium |
| **Marketplace** | Domestic Split Settlement | Yes | No | Critical |
| | Sub-merchant onboarding | Yes | No | High |
| | Sub-merchant earnings dashboard | Yes | No | Medium |
| **Payouts** | Individual Payouts API | Yes | No | Critical |
| | Bulk Payouts API | Yes | No | Critical |
| | IMPS / NEFT / RTGS / UPI payout rails | Yes | No | Critical |
| | Payout to bank / UPI / wallet | Yes | No | Critical |
| | Payout role-based dashboard | Yes | No | High |
| **International** | Apple Pay | Yes | No | High |
| | Dynamic Currency Conversion (DCC) | Yes | No | High |
| | Multi-Currency Conversion (MCC) | Yes | No | High |
| | Import Payments (inward for importers) | Yes | No | Medium |
| | FRM for international cards (documented) | Yes | Mentioned only | Medium |
| **Checkout UX** | Mobile-optimized subscription checkout | Yes | No | Medium |
| | One-click repeat payments (token vault) | Yes | No | High |
| | Pre-authorization + deferred capture | Yes | No | High |
| **Dashboard** | Brand Wallets management | Yes | No | Medium |
| | Payouts dashboard | Yes | No | High |
| | Webhook configuration UI | Yes | No | Medium |
| | Self-serve API key management | Yes | No | High |
| **AI** | MCP Server (hosted) | Yes | No | High |
| | Pine Labs Payments Protocol (P3P) | Yes | No | High |
| | Agent Enablement Toolkit (LangChain/OpenAI/Vercel) | Yes | No | High |
| | Agentic Commerce Suite | Yes | No | Medium |
| | n8n / no-code automation | Yes | No | Low |
| **Extended Features** | Convenience Fees (surcharge config) | Yes | No | High |
| | UPI Reserve Pay (SBMD) | Yes | No | High |
| | Pay by Points (loyalty redemption) | Yes | No | Medium |
| | Payments E-Challan | Yes | No | Medium |
| | Third Party Validation (TPV) | Yes | No | Medium |
| **Compliance** | PCI P2PE Certification | Yes | No | Medium |
| | PA-DSS Validation | Yes | No | Medium |
| **Support** | Public migration guide | Yes | No | Medium |
| | 24/7 documented technical support | Yes | Yes | Parity |
| **Pricing** | Convenience fee passthrough to customer | Yes | No | High |

---

## Conclusion

Plural (Pine Labs Online) and Toucan Pay serve overlapping segments but at very different levels of product completeness. For a developer-led merchant evaluating both:

**Choose Plural if you need:**
- A full affordability/EMI suite right now
- Subscriptions with UPI AutoPay mandates
- Payouts infrastructure (sending money, not just collecting)
- Marketplace split settlement
- AI-native developer tooling (MCP, P3P, Agent Toolkit)
- Pre-authorization / deferred capture
- Token vault with cross-PG migration
- Deep API documentation with CLI, OpenAPI, Postman, Playground

**Choose Toucan if you need:**
- Cross-border PA-CB authorized collection with automated e-FIRA (a genuine product lead)
- Real estate / PropTech embedded payment platform (United Gates — nothing comparable from Plural)
- KYC / identity verification suite (PAN, Aadhaar, UPI ID, Bank Account, GSTIN — no equivalent from Plural)
- Flat-fee cross-border pricing ($24/$25/0.25%) which is more transparent than percentage-based
- Relationship-based premium support with dedicated account manager
- Juspay ecosystem access

**The fundamental product maturity gap** is that Toucan has built a strong foundation (RBI licensing, core payment modes, cross-border) but lacks the depth of an enterprise payment platform. Plural has 5–7 years of product development advantage across affordability, subscriptions, marketplace, payouts, and developer tooling. Toucan's roadmap (EMI "Coming Soon", more SDK coverage) suggests they are actively building toward parity, but the gap is currently significant for any merchant who needs more than basic payment acceptance.

---

*Document prepared: July 2026*
*Sources: https://toucanpay.in/ and https://www.harsh.com/docs/online-payments/*
*Based on publicly available information only. Features may exist in either product without public documentation.*
