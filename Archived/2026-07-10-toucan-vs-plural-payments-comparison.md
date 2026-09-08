# Toucan Pay vs Pine Labs Plural Payments: Deep Public Feature Gap Analysis

Date: 2026-07-10

Scope: This document compares publicly available Toucan Pay material against Pine Labs Plural / Pine Labs Online Payments public documentation. The main question is: which product features, product categories, and operating capabilities are publicly present in Plural but not publicly present in Toucan Pay?

## Evidence Standard

This is a public-documentation comparison, not a private product audit.

| Label | Meaning |
| --- | --- |
| Plural-only public feature | Plural publicly documents the capability and Toucan Pay public pages reviewed do not show an equivalent. |
| Plural deeper public feature | Toucan mentions a broad capability, but Plural publishes materially deeper APIs, dashboard flows, lifecycle states, developer tooling, or operational controls. |
| Not a gap | Toucan publicly documents a similar capability. |

Important: "Not publicly present in Toucan" does not prove Toucan cannot support it privately through sales, custom enterprise enablement, partner integrations, or unpublished APIs. It means it was not evidenced on the public Toucan Pay pages and public Toucan API-docs shell reviewed.

## Sources Reviewed

### Toucan Pay public sources

| Source | URL | Relevant evidence |
| --- | --- | --- |
| Toucan Pay home | https://toucanpay.in/ | Gateway, CrossStream, United Gates, smart routing, every payment mode, real-time reconciliation, AI fraud detection, cross-border, APIs/SDKs/plugins, industry claims. |
| AcquireFlow payment gateway | https://toucanpay.in/payment-gateway-india/ | UPI, cards, net banking, wallets, EMI, 10,000+ TPS, 99.95% uptime, instant settlements, dashboard, fraud prevention, tokenization, 3DS 2.0, onboarding, plugins. |
| CrossStream cross-border | https://toucanpay.in/cross-border-payments/ | Global bank accounts, 190+ countries, auto e-FIRA, real-time tracking, transparent FX, PA-CB, T+1/T+2, marketplace split-settlement mention. |
| Pricing | https://toucanpay.in/pricing/ | Payment gateway pricing, international cards, virtual bank account, instant settlement fee, verification suite, UPI on credit cards coming soon, debit/credit card EMI coming soon, dashboard/plugins/refunds/developer kits. |
| Merchant onboarding policy | https://toucanpay.in/merchant-onboarding-policy/ | Sourcing methods, account creation, registration form, eKYC/manual document verification, merchant scoring, risk/fraud/dispute/offboarding policies. |
| Toucan API-docs page | https://toucanus.com/api-docs/ | Public page fetched as a shell with no visible endpoint catalog in fetched content. |

### Pine Labs Plural public sources

| Source | URL | Relevant evidence |
| --- | --- | --- |
| Plural overview | https://www.harsh.com/docs/online-payments/overview | Product catalog, checkout, payment links, SDKs, affordability, subscriptions, split settlements, payouts, international payments, plugins, MCP, API/OpenAPI/playground. |
| Quick start | https://www.harsh.com/docs/online-payments/quick-start-guide | Self-serve developer account, UAT credentials, API keys, dashboard setup. |
| Accept payments | https://www.harsh.com/docs/online-payments/accept-payments | Hosted/custom checkout, SDKs, plugins, recurring, refunds, settlements, webhooks, real-time status. |
| API reference | https://www.harsh.com/docs/online-payments/api/ | Endpoint catalog for authentication, orders, refunds, settlements, split settlements, checkout, payment links, card, UPI, net banking, wallet, Pay by Points, e-challans, Apple Pay, international, customers, tokenization, payouts, affordability, BNPL, convenience fees, brand wallet, subscriptions, UPI Reserve Pay. |
| Checkout options | https://www.harsh.com/docs/online-payments/checkout-options | Hosted, custom, iFrame, pre-auth, split settlement, convenience fees, tokenized payments, device fingerprinting, express checkout. |
| Payment methods | https://www.harsh.com/docs/online-payments/payment-methods | Cards, UPI, net banking, wallets, Pay by Points, BNPL, international, e-challan, brand wallets. |
| Cards | https://www.harsh.com/docs/online-payments/payment-methods/cards | Native OTP, CVV-less, tokenized card flow, decoupled authorization. |
| UPI | https://www.harsh.com/docs/online-payments/payment-methods/upi | UPI collect, intent, TPV for BFSI/SEBI compliance. |
| Tokenization | https://www.harsh.com/docs/online-payments/tokenization | COFT token management, customer vault, external PA/PG token support, cryptograms, capture/cancel flows. |
| Payment links | https://www.harsh.com/docs/online-payments/payment-links | Link lifecycle, API/dashboard creation, cancel/expiry states, automated SMS/email sharing, analytics. |
| Settlements | https://www.harsh.com/docs/online-payments/settlements | T+1, early batch, same-day, dashboard/API settlement management, 60-day query range, 6-month storage, near real-time data. |
| Subscriptions | https://www.harsh.com/docs/online-payments/subscriptions | UPI AutoPay, fixed/variable/on-demand mandates, trial periods, retries, lifecycle APIs, dashboards. |
| UPI Reserve Pay | https://www.harsh.com/docs/online-payments/upi-reserve-pay | Single Block Multi Debit / Reserve Pay, block once, debit multiple times, live balance. |
| Affordability Suite | https://www.harsh.com/docs/online-payments/affordability-suite | Credit EMI, debit EMI, cardless EMI, down payment EMI, no-cost/low-cost/standard EMI, brand EMI, bundled offers, split EMI, full swipe offers. |
| Payouts | https://www.harsh.com/docs/online-payments/payouts | Individual/bulk payouts, IMPS/NEFT/RTGS/UPI, real-time tracking, role-based dashboard, VAS. |
| Split settlements | https://www.harsh.com/docs/online-payments/split-settlements | Marketplace split settlement, automated distribution, sub-merchant visibility, release/cancel APIs. |
| International payments | https://www.harsh.com/docs/online-payments/international-payments | DCC, MCC, Apple Pay, import payments, FRM, 50+ currencies, dashboard, TCS/invoice/AWB APIs in API reference. |
| Convenience fees | https://www.harsh.com/docs/online-payments/convenience-fees | Fixed/percentage/combined surcharge calculation and refund behavior. |
| Pay by Points | https://www.harsh.com/docs/online-payments/pay-by-points | Reward-point redemption, point balance, point-to-INR conversion, 14 banks, settlement/refund behavior. |
| E-challan / ECMS | https://www.harsh.com/docs/online-payments/payments-e-challan | Bank transfer challans, unique customer identifier, IMPS/NEFT/RTGS/offline bank branch, challan PDF, reconciliation. |
| Dashboard | https://www.harsh.com/docs/online-payments/dashboard | Payments, refunds, settlements, payouts, payment links, settings, exports, filters, team roles, UAT/prod environments. |
| Developer tools | https://www.harsh.com/docs/online-payments/developer-tools | Postman/OpenAPI, IPs/ciphers, method-wise error codes, webhooks, test cards. |
| Webhooks | https://www.harsh.com/docs/online-payments/developer-tools/webhooks | JSON POST webhooks, retry attempts, webhook setup, NAT IP allowlist. |
| Go-live checklist | https://www.harsh.com/docs/online-payments/go-live-checklist | Production readiness, idempotency, webhook signature verification, PCI SAQ guidance, load testing. |
| CLI | https://www.harsh.com/docs/online-payments/cli | Official CLI, orders/refunds/payouts/subscriptions/settlements, webhook relay/replay, doctor/whoami, audit logs, CI. |
| AI solutions | https://www.harsh.com/docs/online-payments/ai | MCP server, Pine Labs Payments Protocol, Agent Enablement Toolkit, Agentic Commerce. |
| Ecommerce plugins | https://www.harsh.com/docs/online-payments/e-commerce-plugins | Shopify, Magento, WooCommerce, OpenCart plugin docs. |

## Executive Summary

Plural has a much broader publicly documented product and developer surface than Toucan Pay. Toucan publicly positions strongly around payment gateway processing, AI smart routing, instant settlements, cross-border virtual accounts, automated e-FIRA, verification suite, compliance, and digital onboarding. Plural publicly documents not just payment acceptance but a full API-first payments platform with checkout variants, subscriptions, payouts, split settlements, affordability financing, UPI Reserve Pay, Pay by Points, e-challan bank transfers, brand wallets, developer CLI, OpenAPI/Postman, webhooks, error codes, AI/MCP tooling, and detailed dashboard/RBAC workflows.

The biggest Plural-only or Plural-deeper public categories are:

| Rank | Category | Plural capability publicly documented | Toucan public status |
| --- | --- | --- | --- |
| 1 | Developer and integration experience | Public API reference, OpenAPI/Postman, CLI, SDK language matrix, webhook docs, test cards, error codes, go-live checklist, AI/MCP tooling. | Toucan mentions APIs, SDKs, sandbox, plugins, and docs, but the fetched public API-docs page did not expose an endpoint catalog or comparable tooling detail. |
| 2 | Checkout architecture | Hosted, custom/seamless, and iFrame checkout with a published feature matrix. | Toucan documents hosted checkout/plugins/SDKs/no-code tools broadly, but not a public hosted/custom/iFrame comparison. |
| 3 | Advanced card flows | Native OTP, CVV-less, tokenized-card flow, decoupled 3DS authorization. | Toucan mentions tokenization and 3DS 2.0 + risk engine, but not these public card-flow controls. |
| 4 | Subscription and recurring billing | UPI AutoPay subscriptions, plans, presentations, trial periods, retries, pause/resume/cancel, lifecycle dashboard. | Toucan mentions SaaS subscriptions, recurring billing, dunning, and usage-based pricing on the home page, but no comparable public subscription API/lifecycle docs were visible. |
| 5 | Affordability suite | Credit/debit/cardless/down-payment EMI, BNPL, no-cost/low-cost EMI, bank/brand EMI, bundled offers, split EMI, full-swipe cashback, offer discovery/validation APIs. | Toucan mentions EMI/BNPL generally; pricing says debit/credit card EMI and UPI on credit cards are coming soon. No public offer-discovery suite was visible. |
| 6 | UPI specialization | UPI Collect, UPI Intent, TPV, UPI Reserve Pay / SBMD. | Toucan supports UPI, but TPV and Reserve Pay were not publicly documented. |
| 7 | Alternative payment products | Pay by Points, e-challan/ECMS bank transfer, brand wallets, Apple Pay. | Not publicly found on Toucan Pay pages reviewed. |
| 8 | Payouts and marketplace operations | Individual/bulk payouts, scheduled payout update/cancel, payout balance, split settlement release/cancel APIs. | Toucan mentions payouts and cross-border split settlements in some copy, but no comparable public payout/split API docs were visible. |
| 9 | Merchant operations | Dashboard modules, exports, filters, role-based team access, UAT/prod environments, dashboard-vs-API matrix. | Toucan documents an easy dashboard, tracking, reports, failed transaction visibility, and reconciliation, but not equivalent RBAC/export/API-operation detail. |
| 10 | AI-native payments | MCP Server, Payments Protocol, Agent Toolkit, Agentic Commerce. | Toucan uses AI in routing/fraud positioning, but not AI-native developer/payment-agent tooling in the reviewed Toucan Pay public material. |

## Toucan Capabilities That Should Not Be Counted as Plural Gaps

Toucan has multiple public strengths and overlaps. These should be excluded from a strict "Plural has it but Toucan does not" list unless the gap is about depth, APIs, or public implementation detail.

| Capability | Toucan public evidence | Gap interpretation |
| --- | --- | --- |
| Domestic payment gateway | AcquireFlow supports UPI, credit/debit cards, net banking, wallets, EMI, BNPL in FAQ. | Not a Plural-only category. |
| Uptime and scale | 99.95% uptime, 10,000+ TPS, 10M+ API hits daily. | Not a Plural-only category. |
| Smart routing and fraud | AI-driven smart routing, AI fraud prevention, 3DS 2.0 + risk engine. | Plural may have deeper docs in some areas, but AI routing/fraud itself is not absent from Toucan. |
| Merchant dashboard | Real-time tracking, reconciliation, analytics, settlement/refund reports. | Plural is deeper publicly, but dashboard existence is not absent. |
| Digital onboarding | Digital KYC, self-onboarding, merchant scoring, 24-hour/live-in-hours claims. | Not absent from Toucan. |
| Plugins and SDKs | Toucan says REST APIs, SDKs, plugins for Shopify, Magento, WooCommerce, Wix, Zoho, custom apps. | Plural has deeper SDK/plugin docs; plugins/SDKs as a concept are not absent. |
| Payment links/no-code tools | Toucan FAQ mentions payment links, buttons, embedded widgets. | Payment links are not absent; Plural has deeper public lifecycle/API/docs. |
| Tokenization/security | Interoperable tokenization, PCI DSS 4.0, PCI-SSF, ISO 27001, RBI PA/PG. | Tokenization/security are not absent; Plural has deeper token API docs. |
| Instant settlements | T+0/T+1/T+2, instant settlements 24x7. | Not absent. |
| Cross-border virtual accounts/e-FIRA | CrossStream provides global bank accounts, 190+ countries, auto e-FIRA, live FX, T+1/T+2. | Toucan may be stronger for exporter-oriented virtual account + e-FIRA flows. |
| Verification suite | Bank account, IFSC, UPI ID, PAN, Aadhaar, GSTIN, onboarding SDK. | This is a Toucan capability, not a Plural-only gap. |

## Category-Wise Deep Gap Analysis

### 1. Developer and Integration Experience

Plural's developer experience is publicly documented as a complete integration ecosystem. Toucan says it is developer-friendly with REST APIs, SDKs, plugins, sandbox, and documentation, but the reviewed Toucan public API-docs page did not expose endpoint groups, request/response examples, OpenAPI download, webhook reference, or SDK language matrices in fetched content.

| Plural feature | Present in Plural | Toucan public status | Why it matters |
| --- | --- | --- | --- |
| Public endpoint catalog | API reference lists endpoint groups for auth, orders, refunds, settlements, split settlements, checkout, payment links, card payments, UPI, net banking, wallet, Pay by Points, e-challans, Apple Pay, international payments, customers, tokenization, payouts, affordability, BNPL, convenience fees, brand wallet, subscriptions, and UPI Reserve Pay. | Toucan public API-docs page fetched as a visible shell without endpoint catalog. | Reduces buyer friction. A developer can estimate integration scope before a sales call. |
| OpenAPI spec | Plural docs link to OpenAPI spec download. | No comparable public OpenAPI artifact found on Toucan pages reviewed. | Enables generated clients, typed SDKs, contract testing, mock servers, and integration governance. |
| Postman collection | Plural docs link to Postman collection. | No comparable public Postman collection found. | Makes QA, support, and partner onboarding faster. |
| Playground | Plural overview links to an API playground for safe validation. | Toucan mentions sandbox but no comparable public playground was found. | Lets developers test requests before code changes. |
| SDK language matrix | Plural documents web SDK, mobile SDKs for Android/iOS/Flutter/React Native, and server SDKs for Node.js, Python, PHP, Java, Ruby, Go, and .NET. | Toucan says SDKs exist but no public language matrix was found in reviewed Toucan pages. | Enterprise teams can choose official libraries aligned with their stack. |
| CLI | Plural documents official `harsh` CLI for orders, refunds, payouts, subscriptions, settlements, webhook listen/replay, doctor/whoami, audit logs, CI use, and MCP-backed ask. | No Toucan CLI found in public pages reviewed. | This is a major developer-ops differentiator for debugging, CI smoke tests, local webhook development, and support handoffs. |
| Webhook documentation | Plural documents JSON POST webhooks, retries, polling recommendation, webhook endpoint setup, and IP allowlist. | Toucan public pages reviewed do not expose webhook docs. | Event-driven merchants can automate fulfillment, refunds, reconciliation, and alerts. |
| Webhook reliability tooling | Plural CLI supports local webhook relay, signature verification, failed-delivery spool, and replay. | No comparable public Toucan tooling found. | Reduces one of the highest-friction parts of payment integration: local and staging webhook testing. |
| Method-specific error codes | Plural developer tools split errors into common, UPI, card, net banking, EMI, and BNPL categories. | Toucan public pages reviewed do not expose error-code catalog. | Helps merchants build precise failure handling, payment retry logic, and support scripts. |
| Test cards | Plural documents sandbox test cards covering common card scenarios. | Toucan mentions sandbox but no public test-card details were found. | Testing becomes deterministic instead of depending on live low-value payments. |
| Go-live checklist | Plural publishes production readiness checks for APIs, idempotency, webhooks, security, testing, dashboard config, and monitoring. | Toucan does not show equivalent public checklist in reviewed pages. | Reduces production launch risk and improves enterprise signoff. |
| Idempotency guidance | Plural go-live and CLI docs discuss idempotency keys for critical requests. | No comparable public Toucan idempotency guidance found. | Prevents duplicate charges/refunds/payouts during retries. |
| Dashboard API keys | Plural quick start shows self-service API keys under Settings -> API Keys. | Toucan onboarding mentions account creation/KYC and sandbox but not this public flow. | Lets developers self-serve credentials with less support dependency. |
| AI-assisted developer operations | Plural AI docs and CLI describe MCP/agent tooling. | Toucan AI positioning is routing/fraud oriented, not developer-agent tooling in reviewed pages. | Emerging differentiator for developers using Claude, Cursor, VS Code Copilot, LangChain, OpenAI Agents SDK, and Vercel AI SDK. |

**Net assessment:** Plural is publicly stronger for developer-led evaluation and integration. Toucan may have private docs or merchant portal docs, but the publicly visible Toucan material is more sales-led and less API-reference-led.

### 2. Merchant Onboarding Experience

Toucan has strong public onboarding claims: digital onboarding, KYC, manual verification, merchant scoring, same-day onboarding, live in hours, and support-assisted activation. Plural's gap advantage is self-service developer account creation and immediate UAT readiness.

| Plural feature | Present in Plural | Toucan public status | Why it matters |
| --- | --- | --- | --- |
| Free developer account | Quick start says create a free Pine Labs Online developer account. | Toucan has create account/sign-up links and digital onboarding, but the reviewed material focuses more on KYC and approval. | Developers can start before full commercial onboarding. |
| UAT credentials before production | Plural says developers can access UAT credentials, API keys, and dashboard before going live, with no production credentials required. | Toucan mentions sandbox, but equivalent self-serve UAT credential flow was not publicly shown. | Separates technical integration from compliance/production activation. |
| Email verification + API key generation flow | Plural documents sign-up, email verification, Settings -> API Keys. | Toucan public onboarding policy documents account creation, registration, eKYC/document verification, scoring. | Plural gives a more explicit developer credential path. |
| UAT/prod dashboard separation | Plural dashboard docs list UAT and production dashboard URLs/environments. | Toucan pages reviewed do not expose equivalent environment documentation. | Helps engineering, QA, finance, and support avoid mixing test and live operations. |
| Production readiness checklist | Plural documents integration, webhook, security, testing, dashboard, and go-live checks. | No comparable public Toucan checklist found. | Stronger enterprise implementation governance. |
| Role-based team onboarding | Plural dashboard supports Owner, Admin, Finance, Operations, View only roles. | Toucan public pages reviewed do not document dashboard RBAC. | More scalable for multi-team merchants with finance, ops, and support users. |

**Net assessment:** Toucan appears strong at regulated merchant KYC/onboarding. Plural appears stronger at developer self-service onboarding and operational readiness documentation.

### 3. Merchant Experience and Operations Dashboard

Toucan publicly promises an easy dashboard, real-time tracking, reconciliation, analytics, settlement/refund reports, and failed transaction visibility. Plural publicly breaks the dashboard into specific operational modules, search/export workflows, and team roles.

| Plural feature | Present in Plural | Toucan public status | Why it matters |
| --- | --- | --- | --- |
| Dashboard modules | Plural documents Payments, Refunds, Settlements, Brand Wallets, Payouts, Payment Links, Settings. | Toucan documents dashboard for payments, analytics, reconciliation, settlement/refund reports. No Brand Wallet/Payout/Settings module detail found. | Plural shows broader operational control from one dashboard. |
| Search and filters | Plural documents filters by date, status, amount, order ID, UTR, payment method. | Toucan does not publicly detail filter/search capabilities in reviewed pages. | Essential for support, finance, and reconciliation teams. |
| CSV/Excel exports | Plural documents dashboard exports for reconciliation/accounting. | Toucan mentions reports but not export formats/workflow. | Finance teams often require raw exports for ERP/accounting. |
| Dashboard vs API matrix | Plural explicitly says which actions are dashboard-only, API-supported, or automation-ready. | No comparable public matrix found. | Helps merchants design operating models and automation boundaries. |
| Role-based access | Plural documents Owner, Admin, Finance, Operations, View only. | No Toucan dashboard RBAC found in reviewed pages. | Reduces operational risk by limiting refund/settings access. |
| Payment link management | Plural dashboard supports creation, tracking, and link payments. | Toucan mentions no-code tools/payment links broadly, but not public link management docs. | Better for non-technical sales/ops teams. |
| Refund operations | Plural dashboard supports full/partial refunds and refund history. | Toucan mentions instant refunds and refund reports, but detailed dashboard workflow was not found. | Clearer operational SOPs for support teams. |
| Settlement operations | Plural dashboard shows settlement batches, amounts, UTR numbers, reports. | Toucan mentions settlement reports/reconciliation. No public UTR workflow found. | Stronger bank reconciliation flow. |
| Payout operations | Plural dashboard supports payout schedule, details, pending amounts. | Toucan mentions payouts in general copy but no detailed payout dashboard found. | Required by marketplaces, gig platforms, lenders, and platforms. |

**Net assessment:** Plural's merchant dashboard is more publicly specified. Toucan may have similar dashboard functions privately, but public evidence is less granular.

### 4. Payment Acceptance and Checkout Coverage

Toucan supports core payment acceptance: UPI, cards, net banking, wallets, EMI/BNPL mentions, international cards, virtual bank accounts, hosted checkout, plugins, SDKs, and no-code tools. Plural's public differentiation is in the number of specialized acceptance modes and the detail of checkout architectures.

| Plural feature | Present in Plural | Toucan public status | Why it matters |
| --- | --- | --- | --- |
| Hosted checkout | Publicly documented. | Toucan documents hosted checkout/platform fee. | Not a gap. |
| Custom/seamless checkout | Plural documents custom checkout with full UI control and payment-method APIs. | Toucan says APIs/SDK/custom-stack but no public custom checkout feature matrix found. | Critical for enterprise merchants with strict UX/branding control. |
| iFrame checkout | Plural documents iFrame checkout using `integration_mode: IFRAME`. | No Toucan public equivalent found. | Keeps the user on the merchant page while Pine Labs manages PCI-sensitive collection. |
| Checkout feature matrix | Plural compares hosted/custom/iFrame by PCI responsibility, supported methods, express checkout, pre-auth, webhooks, split settlement, convenience fees, refunds, SDKs, tokenization, device fingerprinting. | No Toucan public equivalent found. | Helps merchants choose the right integration path. |
| Express checkout | Plural documents saved cards and autofill in hosted/iFrame. | Toucan mentions checkout optimization but no public express checkout details found. | Reduces checkout friction for repeat users. |
| Device fingerprinting | Plural says hosted/iFrame has built-in device fingerprinting, custom requires merchant implementation. | Toucan mentions risk engine/fraud but not public device-fingerprinting implementation. | Helps risk scoring and fraud prevention. |
| Pre-authorization | Plural documents `pre_auth` and capture/cancel APIs. | Toucan does not publicly show pre-auth/capture/cancel flow in reviewed pages. | Needed for hotels, travel, rentals, delivery, inventory reservation, and partial capture. |
| Full/partial refunds | Plural documents dashboard/API refund operations. | Toucan mentions instant refunds and reports. | Not absent, but Plural has deeper public API detail. |
| Payment link lifecycle | Plural documents `CREATED`, `CLICKED`, `PAYMENT_INITIATED`, `PROCESSED`, `CANCELLED`, `EXPIRED`. | Toucan mentions payment links/no-code tools but not public lifecycle states. | Helps sales/ops follow up on unpaid links and expired links. |
| Payment link notification resend | Plural API reference includes resend payment link notification. | No Toucan public equivalent found. | Useful for collections teams. |
| Pay by Points | Plural supports reward points redemption with point balance, point-to-INR conversion, 14 banks, settlement/refund handling. | Not found in Toucan Pay public pages reviewed. | Adds loyalty redemption as a payment instrument, improving conversion without direct discounting. |
| E-challan / ECMS bank transfer | Plural supports generated challans, persistent customer identifiers, IMPS/NEFT/RTGS, bank branch/offline payments, challan PDF. | Toucan supports virtual bank accounts for cross-border and bank transfers in pricing, but not ECMS e-challan product docs. | Useful for education, government, B2B invoices, and offline-assisted collection. |
| Brand Wallets | Plural API reference lists brand wallet creation, validation, activation/deactivation, balance, load money, transaction history, reset PIN, and brand wallet payments. | Not found in Toucan Pay public pages reviewed. | Enables closed-loop wallet and stored-value programs. |
| Apple Pay | Plural international payments/API reference includes Apple Pay. | Not found in Toucan Pay public pages reviewed. | Useful for premium/international customers and mobile-first checkout. |
| UPI Collect and Intent | Plural documents both. | Toucan supports UPI but does not break out Collect/Intent in reviewed public pages. | Not necessarily absent, but Plural is clearer publicly. |
| UPI TPV | Plural documents TPV for BFSI/SEBI compliance. | Toucan has UPI ID verification in its verification suite, but payment-flow TPV for SEBI/BFSI was not found. | Critical for broking, mutual funds, securities, and regulated fund-flow use cases. |
| UPI Reserve Pay / SBMD | Plural documents block-once, multi-debit reserve pay up to current NPCI limit, live remaining balance, no pre-debit notification. | Not found in Toucan Pay public pages reviewed. | Enables higher payment certainty for reserved/usage-based payment models. |

**Net assessment:** Toucan covers mainstream payment acceptance well. Plural publicly goes deeper into specialized payment instruments, acceptance architectures, and regulated UPI/card variants.

### 5. Success Rate, Routing, Risk, and Reliability (SR)

The user mentioned "SR". This section treats SR as success rate, routing, and reliability. Toucan is strong in public SR positioning: AI smart routing, 99.95% uptime, 10,000+ TPS, 10M+ daily API hits, AI fraud prevention, and 3DS 2.0 + risk engine. Plural's public gaps are more around documented controls and tools that merchants can implement to lift success and reduce failures.

| Plural feature | Present in Plural | Toucan public status | Why it matters |
| --- | --- | --- | --- |
| Native OTP card flow | Plural says OTP can be completed inside merchant app/site without redirect and claims 2-3% authentication success improvement. | Toucan mentions 3DS 2.0 but not native OTP flow. | Reduces redirection drop-offs and improves card authentication completion. |
| CVV-less flow | Plural supports CVV-less transactions for supported tokenized Visa/Mastercard seamless flows. | No Toucan public equivalent found. | Reduces repeat-payment friction for saved cards. |
| Decoupled authorization | Plural supports merchant-managed 3DS provider flow and submission of authentication outcome to Pine Labs. | No Toucan public equivalent found. | Useful for large merchants that want full control over authentication UX or existing 3DS providers. |
| UPI Reserve Pay | Plural reserves funds upfront and allows multiple partial debits. | No Toucan public equivalent found. | Reduces insufficient-fund failures for future debits. |
| TPV for UPI | Plural validates that payments come from the customer's registered bank account. | No public Toucan payment-flow TPV found. | Reduces unauthorized payment risk and supports BFSI compliance. |
| Method-wise error codes | Plural publishes error references by payment method. | Toucan public pages reviewed do not expose comparable error references. | Better retry routing, customer messaging, and support triage. |
| Webhook retries | Plural documents up to three webhook delivery attempts and recommends polling for time-sensitive actions. | No public Toucan webhook retry docs found. | Improves reliability of downstream fulfillment and status updates. |
| Idempotency checklist | Plural go-live checklist explicitly calls out idempotency keys. | No public Toucan idempotency guidance found. | Prevents duplicate side effects during retries. |
| CLI doctor/audit logs | Plural CLI provides diagnostics and redacted audit logs. | No public Toucan equivalent found. | Speeds issue diagnosis and support escalation. |
| International FRM details | Plural describes international card transactions passing through a risk engine that blocks high-risk transactions and returns errors. | Toucan has AI fraud prevention/risk engine; this is not fully absent, but Plural has more public flow detail for international card FRM. | Better implementation predictability for cross-border card merchants. |

**Net assessment:** Toucan markets SR/routing strength more strongly. Plural publicly exposes more implementable SR levers for developers and operations teams.

### 6. Settlement and Reconciliation

Toucan publicly documents real-time reconciliation, automated settlement/refund reports, instant settlements, T+0/T+1/T+2 options, CrossStream T+1/T+2, and transparent cross-border settlement. Plural's public gap advantage is settlement API detail, operational reporting detail, split settlement controls, and settlement data constraints.

| Plural feature | Present in Plural | Toucan public status | Why it matters |
| --- | --- | --- | --- |
| Settlement API | Plural API reference includes Get All Settlements and Get Settlements by UTR. | Toucan mentions reports/reconciliation but no public settlement API docs found. | Finance systems can programmatically reconcile deposits. |
| UTR-based lookup | Plural dashboard/API references UTR lookup and settlement detail. | Not publicly found on Toucan pages reviewed. | UTR is a core bank reconciliation key. |
| Settlement data constraints | Plural documents 60-day max query range, 10 records/page, 6 months storage, near-real-time data with possible 3-hour delay. | No comparable public operational limits found. | Helps merchants design data extraction and archival jobs. |
| Early batch settlements | Plural documents early batch settlements in addition to T+1 and same-day. | Toucan documents flexible T+0/T+1/T+2 and instant settlements. | Not necessarily absent, but Plural defines the settlement type publicly. |
| Same-day settlement docs | Plural links same-day settlement docs. | Toucan supports instant settlements 24x7. | Not a gap as a capability. |
| Holiday/weekend settlement docs | Plural links holiday/weekend settlement docs. | Toucan instant settlements 24x7 may overlap, but no similar public docs found. | Important for cash-flow planning. |
| Split settlement release/cancel APIs | Plural API reference includes release and cancel settlement endpoints for split settlements. | Toucan CrossStream mentions split settlements for marketplaces, but no public release/cancel APIs found. | Marketplace platforms need deterministic settlement controls. |
| Sub-merchant/partner earning visibility | Plural split settlement docs mention detailed earning visibility via dashboard. | Not publicly found in Toucan pages reviewed. | Reduces partner payout disputes. |
| E-challan unique identifiers | Plural ECMS assigns persistent customer identifiers for matching bank transfers. | Toucan supports virtual bank account pricing and CrossStream accounts, but not ECMS e-challan matching docs. | Reduces manual reconciliation for offline/NEFT/RTGS flows. |
| Convenience fee refund behavior | Plural documents how convenience fee refunds behave depending on who initiates the refund. | No Toucan public equivalent found. | Finance and customer support need predictable fee reversal rules. |

**Net assessment:** Toucan is strong on settlement speed and cross-border e-FIRA. Plural is stronger on publicly documented settlement APIs, reconciliation metadata, and marketplace settlement controls.

### 7. Affordability and Financing

This is one of the clearest Plural-only public gaps. Toucan mentions EMI and BNPL, but its pricing page marks UPI on credit cards and debit/credit card EMI as coming soon. Plural documents a mature affordability product suite.

| Plural feature | Present in Plural | Toucan public status | Why it matters |
| --- | --- | --- | --- |
| Credit Card EMI | Plural documents. | Toucan mentions EMI/BNPL, but pricing marks debit/credit card EMI as coming soon. | Standard high-ticket conversion feature. |
| Debit Card EMI | Plural documents pre-approved debit card EMI. | Toucan pricing marks debit/credit card EMI as coming soon. | Expands affordability beyond credit-card users. |
| Cardless EMI | Plural documents mobile/PAN/KYC based cardless EMI linked to lenders/NBFCs. | Not found in Toucan public pages reviewed. | Captures customers without eligible cards. |
| Down Payment EMI | Plural documents upfront down payment plus subsequent EMI. | Not found in Toucan public pages reviewed. | Useful for higher-value retail and devices. |
| BNPL eligibility/payment APIs | Plural API reference includes BNPL eligibility, OTP submit/resend, create payment. | Toucan mentions BNPL but no public BNPL API detail found. | Lets merchants show eligible pay-later options before checkout. |
| No-cost EMI | Plural documents no-cost EMI where interest is subsidized. | Toucan does not publicly detail no-cost EMI in reviewed pages. | Major conversion lever in electronics, furniture, education, healthcare. |
| Low-cost and standard EMI | Plural documents multiple EMI cost models. | Not publicly detailed by Toucan. | Supports varied brand/bank economics. |
| Bank EMI and Brand EMI | Plural documents bank and brand EMI categories. | Not publicly found on Toucan pages reviewed. | Enables bank-funded and brand-funded offers. |
| Bundled offers | Plural documents SKU-bundle based offers with constraints and benefits. | Not found in Toucan public pages reviewed. | Enables cross-sell/upsell promotions without manual discounting. |
| Split EMI | Plural documents EMI plus bullet payment. | Not found in Toucan public pages reviewed. | Supports differentiated high-ticket financing. |
| Full Swipe Offer | Plural documents full upfront payment followed by cashback/discount funded by stakeholders. | Not found in Toucan public pages reviewed. | Lets merchants offer incentives without changing payment mode. |
| Offer discovery API | Plural API reference includes offer discovery v1/v2 and cardless discovery. | No Toucan public equivalent found. | Merchants can show eligible EMI/offer options before payment. |
| Offer validation API | Plural API reference includes offer validation. | No Toucan public equivalent found. | Prevents displaying offers that cannot be honored. |
| Down payment details API | Plural API reference includes down payment details. | No Toucan public equivalent found. | Supports complex affordability checkout. |
| IMEI validation API | Plural API reference includes IMEI validation for affordability. | No Toucan public equivalent found. | Useful for device financing, fraud control, and brand offers. |

**Net assessment:** Plural has a publicly mature affordability suite. Toucan's public affordability story is thin and partially "coming soon" on pricing.

### 8. User Experience

Plural publicly documents multiple UX mechanisms that either keep customers on the merchant site, reduce repeated data entry, make payments more familiar, or provide more payment choices. Toucan also promotes checkout optimization, no-code tools, plugins, SDKs, and dashboard tracking, but does not publicly expose many UX flow details.

| Plural UX feature | Present in Plural | Toucan public status | Why it matters |
| --- | --- | --- | --- |
| iFrame checkout | Embedded checkout keeps users on merchant page with Pine Labs-managed secure collection. | Not publicly found on Toucan pages reviewed. | Less redirect friction while keeping PCI scope manageable. |
| Native OTP | OTP handled in merchant UI. | Not publicly found. | Fewer redirects and lower authentication drop-off. |
| CVV-less repeat payment | Supported for eligible network-token flows. | Not publicly found. | Faster returning-customer checkout. |
| Express checkout | Hosted/iFrame supports saved cards/autofill. | Toucan mentions checkout optimization but no public express details. | Faster repeat purchases. |
| Pay by Points balance display | Customers can view points and point-to-INR conversion. | Not publicly found. | Increases perceived affordability and loyalty usage. |
| DCC/MCC international currency UX | Plural supports customers seeing/paying in familiar currencies through DCC/MCC. | Toucan cross-border focuses on receiving foreign funds into India and international cards, but DCC/MCC UX not publicly found. | Reduces international checkout confusion. |
| Payment link lifecycle visibility | Link clicked, initiated, processed, cancelled, expired states. | Toucan mentions payment links but no lifecycle states. | Sales/ops can follow up accurately. |
| Subscription checkout UI | Plural documents mobile/web mandate checkout interfaces showing mandate duration, one-time fee, max amount, recurring amount. | Toucan mentions subscriptions on home page, but no public subscription checkout UI found. | Builds customer trust for recurring debits. |
| UPI Reserve Pay balance visibility | Merchant can retrieve utilized and remaining reserve balance. | Not publicly found. | Allows clear customer communication for reserve-based charges. |
| E-challan download | Customers can download challans with identifier, IFSC, expiry, amount. | Not publicly found. | Useful for bank-transfer and offline-assisted payment users. |

**Net assessment:** Plural provides richer public UX patterns for advanced checkout and non-standard payment journeys.

### 9. Subscriptions and Recurring Payments

Toucan's home page says SaaS and subscriptions include recurring billing, dunning management, usage-based pricing, and international invoicing. However, no public Toucan subscription API/lifecycle documentation was visible in the reviewed sources. Plural publishes detailed subscription concepts, APIs, dashboards, mandate types, retry behavior, and trial-period support.

| Plural feature | Present in Plural | Toucan public status | Why it matters |
| --- | --- | --- | --- |
| Subscription plans | Plural API reference includes create/list/get/update/delete plan and get by merchant reference. | No Toucan public API equivalent found. | Core for SaaS, OTT, insurance, investment platforms. |
| Subscription creation | Plural supports subscription creation against plans. | Toucan mentions recurring billing but no public API found. | Enables self-service recurring billing. |
| Fixed frequency model | Plural documents daily/weekly/monthly/yearly style fixed schedules. | Toucan does not publicly detail models. | Predictable recurring revenue. |
| Variable frequency model | Plural documents flexible charge timing with consistent pricing. | Not publicly found. | Useful for utilities, freelancers, project billing. |
| One-time mandate | Plural documents single debit mandate. | Not publicly found. | One-off high-trust authorization use cases. |
| On-demand mandate | Plural documents merchant-initiated debits within validity. | Not publicly found. | Usage-based or variable billing. |
| Recurring mandate | Plural documents multiple automatic debits. | Toucan broadly mentions recurring billing. | Plural has deeper public support. |
| Automatic pre-debit notifications | Plural says pre-debit notification at least 24 hours before debit. | Not publicly found. | Regulatory and customer-trust requirement. |
| Automatic retries | Plural documents retry schedule: first retry 10 minutes after failure, second retry 1 hour later, and merchant-controlled retries via APIs. | Not publicly found. | Improves collection success. |
| Merchant-controlled debits | Plural supports APIs to control notifications/debits. | Not publicly found. | Needed for variable billing and platform-led collection. |
| Pause/resume/cancel | API reference lists pause, resume, cancel. | Not publicly found. | Required for subscription lifecycle management. |
| Trial period | Plural documents trial period with no charges until trial ends. | Not publicly found. | Common SaaS/OTT growth feature. |
| Subscription dashboard | Plural documents real-time monitoring, revenue analytics, customer management. | Not publicly found. | Finance and growth teams need recurring revenue visibility. |

**Net assessment:** Toucan may target subscriptions at a marketing level; Plural publishes a full subscription product surface.

### 10. Payouts and Disbursements

Toucan's home/footer/pricing copy says it helps businesses make payouts and collect/make overseas payments, but no detailed Toucan payout product/API page was visible in the reviewed sources. Plural documents payouts as a standalone API and dashboard product.

| Plural feature | Present in Plural | Toucan public status | Why it matters |
| --- | --- | --- | --- |
| Individual payouts | Plural documents individual payout workflows. | Toucan mentions payouts broadly but no detailed public workflow found. | Needed for refunds, vendor payments, customer credits, gig payouts. |
| Bulk payouts | Plural documents bulk payouts. | Not publicly found in Toucan pages reviewed. | Important for payroll-like, gig, vendor, and lending disbursements. |
| Payout rails | Plural supports IMPS, NEFT, RTGS, UPI. | Toucan has virtual account IMPS/NEFT/RTGS pricing and verification, but no public payout rail docs. | Determines speed/cost for disbursement operations. |
| Scheduled payout update/cancel | Plural API reference includes update scheduled payout and cancel scheduled payout. | Not publicly found. | Required for operational correction and risk control. |
| Payout balance API | Plural API includes funding account balance. | Not publicly found. | Prevents failed disbursements due to insufficient funding. |
| Payout dashboard | Plural documents complete visibility into payout operations. | Not publicly found. | Finance teams need status, pending amounts, and reconciliation. |
| Payout VAS | Plural documents account verification, refunds, balance inquiry, transaction status tracking. | Toucan has a separate verification suite, but no public payout VAS bundle found. | Improves payout accuracy and compliance. |
| Developer-first payout APIs | Plural explicitly documents RESTful APIs and SDKs for payouts. | No public Toucan payout API docs found. | Platform businesses need automated payout orchestration. |

**Net assessment:** Plural is publicly stronger for domestic payout productization and API-led disbursement workflows.

### 11. Marketplace and Platform Payments

Toucan's CrossStream page mentions marketplaces can manage cross-border collections and seller payouts with split settlements. Plural publicly documents marketplace-style capabilities across core online payments, settlement release/cancel APIs, split settlements, payouts, sub-merchant visibility, convenience fees, and brand wallets.

| Plural feature | Present in Plural | Toucan public status | Why it matters |
| --- | --- | --- | --- |
| Split settlements product | Plural documents split settlement for e-commerce platforms, travel agencies, food delivery, subscription revenue sharing. | Toucan mentions split settlements for CrossStream marketplaces only. | Plural positions split settlements as a broader online payments product. |
| Split release/cancel APIs | Plural API reference includes release/cancel settlement. | No Toucan public equivalent found. | Enables controlled escrow-like release operations. |
| Sub-merchant visibility | Plural split settlement docs mention partner earning visibility via dashboard. | Not publicly found. | Helps reduce marketplace seller disputes. |
| Payout APIs | Plural has standalone payout APIs. | Toucan no detailed public payout API found. | Split settlement plus payouts covers both collect and distribute. |
| Convenience fee product | Plural supports configurable fee recovery by payment method. | No Toucan public equivalent found. | Marketplaces often pass fees or platform charges to users. |
| Brand wallet APIs | Plural supports brand wallet lifecycle and payments. | Not publicly found. | Enables platform wallet balance, gift card, loyalty, or stored value flows. |

**Net assessment:** Toucan's cross-border marketplace story is credible but narrower publicly. Plural's marketplace/platform tooling is more explicit and API-backed in public docs.

### 12. International Payments

Toucan is strong in exporter-focused cross-border payments: global bank accounts, 190+ countries, zero FX markup claims, flat fees for inward remittance, T+1/T+2, and automated e-FIRA. Plural's international feature set is more card/acquiring/checkout oriented, with DCC/MCC, Apple Pay, TCS, invoice/AWB APIs, and fraud/risk management.

| Plural international feature | Present in Plural | Toucan public status | Why it matters |
| --- | --- | --- | --- |
| DCC | Plural documents Dynamic Currency Conversion. | Toucan documents live FX and INR settlement for CrossStream, but not DCC at checkout. | Lets international cardholders pay/see prices in familiar currency. |
| MCC | Plural documents merchant currency conversion. | Not publicly found on Toucan pages reviewed. | Useful for multi-currency merchant pricing. |
| Apple Pay | Plural docs/API include Apple Pay. | Not publicly found. | Important for global/mobile checkout acceptance. |
| Import payments | Plural international docs link import payments. | Toucan CrossStream focuses more on receive international payments and PA-CB inbound/outbound capability; no comparable public import payment flow found. | Relevant to cross-border commerce and import workflows. |
| TCS compute API | Plural API reference includes Compute TCS. | Toucan public pages reviewed did not expose TCS API. | Important for India LRS/cross-border tax workflows. |
| Invoice capture/status/upload APIs | Plural API reference includes invoice capture, status, and upload file APIs. | Toucan has automated e-FIRA; no public invoice API found. | Automates compliance artifacts for international orders. |
| AWB create/get/upload APIs | Plural API reference includes AWB bulk/create/status/upload. | Not publicly found on Toucan pages reviewed. | Useful for physical cross-border goods compliance/logistics. |
| International FRM | Plural documents risk evaluation and blocking high-risk transactions. | Toucan has AI fraud and risk engine. | Not absent, but Plural publishes more international card flow detail. |

**Net assessment:** Do not frame all international payments as Plural-only. Toucan may be stronger for export collections and e-FIRA. Plural has Plural-only public features around international card checkout, DCC/MCC, Apple Pay, and compliance APIs like TCS/invoice/AWB.

### 13. Security, Compliance, and Risk Controls

Both vendors publicly document compliance and security. Toucan is explicit about RBI PA/PG, PCI DSS, PCI-SSF, ISO 27001, 3DS 2.0, risk engine, SOC 2/GDPR for CrossStream. Plural's public gaps are more developer-operational security controls and implementation-specific guidance.

| Plural feature | Present in Plural | Toucan public status | Why it matters |
| --- | --- | --- | --- |
| PCI responsibility by checkout mode | Plural checkout docs say Hosted/iFrame PCI handled by Pine Labs, Custom requires merchant PCI certificate; go-live checklist mentions SAQ-A, SAQ-A-EP/SAQ-D. | Toucan says PCI DSS 4.0 and tokenization, but no public checkout-mode PCI responsibility matrix found. | Helps compliance teams choose integration mode. |
| Webhook IP allowlist | Plural webhook docs publish UAT/prod NAT IPs/ranges. | No Toucan public webhook/IP allowlist found. | Required by merchants with locked-down ingress. |
| TLS/cipher guidance | Plural developer tools include IPs and ciphers. | Not publicly found on Toucan pages reviewed. | Enterprise network/security teams need this before production. |
| Webhook signature verification | Plural go-live checklist includes signature verification; CLI has verify/sign tooling. | No public Toucan signature docs found. | Prevents spoofed payment events. |
| COFT token API details | Plural tokenization docs cover token IDs, service provider tokens, network tokens, cryptograms, customer token APIs, deletion, external PA/PG token processing. | Toucan mentions interoperable tokenization but no comparable public token API detail found. | Important for saved-card migration, repeat payments, and compliance. |
| Decoupled auth liability notes | Plural card docs warn about data consistency and merchant liability in decoupled 3DS. | No public Toucan equivalent found. | Prevents fraud/liability mistakes in custom authentication flows. |
| Redacted CLI audit logs | Plural CLI writes audit logs with PCI/PII redaction. | No Toucan CLI found. | Helps support and audit without leaking sensitive data. |

**Net assessment:** Toucan is not weak on compliance; it is strong in certifications. Plural publicly documents more implementation-level security controls.

### 14. AI-Native Payments and Agentic Commerce

Plural publicly documents AI-native payment developer tools. Toucan publicly uses AI in smart routing and fraud detection, which is a different product dimension.

| Plural feature | Present in Plural | Toucan public status | Why it matters |
| --- | --- | --- | --- |
| Hosted MCP Server | Plural AI docs describe hosted MCP server for Claude, Cursor, VS Code Copilot, and MCP clients. | Toucan does not publicly show MCP tooling in reviewed pages. | Lets developers/operators query and execute payment APIs from AI coding tools. |
| Pine Labs Payments Protocol | Plural documents HTTP-native protocol for autonomous AI agents to pay APIs, built on 402 Payment Required. | No Toucan public equivalent found. | Positions Plural for agentic commerce and autonomous payment workflows. |
| Agent Enablement Toolkit | Plural documents tools for LangChain, OpenAI Agents SDK, and Vercel AI SDK. | No Toucan public equivalent found. | Makes Pine Labs APIs callable from product AI agents. |
| Agentic Commerce Suite | Plural documents agentic commerce positioning. | No Toucan public equivalent found. | Strategic feature for conversational commerce. |
| Natural-language operations | Plural says AI assistants can query/test/execute payment APIs. | Toucan AI is payment routing/fraud focused in reviewed pages. | Useful for support, ops, and developer productivity. |

**Net assessment:** Plural has a clear public AI-developer/agentic-commerce story. Toucan's public AI story is internal optimization and fraud.

### 15. Ecommerce Plugins and Low-Code Tools

This category is not a clean Plural-only gap because Toucan publicly mentions plugins for Shopify, Magento, WooCommerce, Wix, Zoho, and custom-stack applications. Plural publicly documents plugins for Shopify, Magento, WooCommerce, and OpenCart.

| Plural feature | Present in Plural | Toucan public status | Gap interpretation |
| --- | --- | --- | --- |
| Shopify plugin | Plural docs. | Toucan public pages mention Shopify. | Not a gap. |
| Magento plugin | Plural docs. | Toucan public pages mention Magento. | Not a gap. |
| WooCommerce plugin | Plural docs. | Toucan public pages mention WooCommerce. | Not a gap. |
| OpenCart plugin | Plural docs. | Toucan pages reviewed did not mention OpenCart. | Plural-only public plugin target. |
| Plugin install docs | Plural has plugin docs section. | Toucan mentions plugins but no comparable public install docs found in reviewed pages. | Plural deeper public detail. |
| Hosted checkout requirement for plugins | Plural checkout docs state plugins use hosted checkout. | Toucan does not publicly explain plugin architecture in reviewed pages. | Helps merchants understand PCI and UX implications. |

**Net assessment:** Do not claim ecommerce plugins as absent from Toucan. Claim Plural has clearer public plugin documentation and OpenCart coverage in the reviewed docs.

## Product Category Gap Map

| Product category | Plural public capability | Toucan public capability | Gap conclusion |
| --- | --- | --- | --- |
| Core payment gateway | Cards, UPI, net banking, wallets, BNPL, EMI, international. | UPI, cards, net banking, wallets, EMI, BNPL, international cards. | Not a broad gap. |
| Hosted checkout | Documented. | Documented broadly. | Not a gap. |
| Custom checkout | Detailed custom/seamless flow. | APIs/SDKs broadly mentioned. | Plural deeper public detail. |
| iFrame checkout | Documented. | Not found. | Plural-only public. |
| Payment links | API/dashboard, lifecycle, cancel, resend, statuses. | Payment links/buttons/widgets mentioned. | Plural deeper public detail. |
| Cards advanced flows | Native OTP, CVV-less, tokenized, decoupled auth. | 3DS 2.0, tokenization, risk engine. | Plural-only public flow controls. |
| UPI | Collect, Intent, TPV, Reserve Pay. | UPI supported. | TPV/Reserve Pay Plural-only public. |
| Net banking | Documented. | Supported. | Not a broad gap. |
| Wallets | Wallet payments plus brand wallets. | Wallets supported. | Brand wallets Plural-only public. |
| Pay by Points | Documented. | Not found. | Plural-only public. |
| E-challan / ECMS | Documented. | Not found as ECMS/challan. | Plural-only public. |
| Affordability | Deep suite and APIs. | EMI/BNPL mentioned; some coming soon. | Major Plural-only/deeper gap. |
| Subscriptions | Deep UPI AutoPay lifecycle. | Subscription use case mentioned. | Major Plural deeper public gap. |
| UPI Reserve Pay | Documented. | Not found. | Plural-only public. |
| Payouts | Deep API/dashboard product. | Payouts mentioned broadly. | Plural deeper public gap. |
| Split settlements | Product plus release/cancel APIs. | Cross-border marketplace split settlement mentioned. | Plural deeper public gap. |
| Convenience fees | Calculate and configure surcharge. | Not found. | Plural-only public. |
| International card checkout | DCC, MCC, Apple Pay, TCS, invoice/AWB APIs. | CrossStream and international card support. | Plural-only for DCC/MCC/Apple Pay/TCS/AWB/invoice APIs; Toucan strong for virtual accounts/e-FIRA. |
| Dashboard | Modules, filters, exports, RBAC. | Dashboard, tracking, reconciliation, reports. | Plural deeper public gap. |
| Developer tooling | API ref, OpenAPI, Postman, CLI, webhooks, error codes, test cards. | APIs/SDKs/docs/sandbox mentioned; visible endpoint docs not found. | Major Plural public gap. |
| AI tooling | MCP, P3P, Agent Toolkit. | AI routing/fraud. | Plural-only for AI-native developer tooling. |

## Most Material Plural-Only Features by Business Impact

| Priority | Feature | Impact if Toucan lacks it publicly | Best-fit merchant segment |
| --- | --- | --- | --- |
| High | Public API/OpenAPI/Postman/CLI/webhooks/error codes | Slower developer evaluation, slower partner onboarding, higher support dependency. | SaaS, marketplaces, enterprise merchants, platforms. |
| High | Advanced affordability suite | Lower conversion for high-ticket purchases where EMI/BNPL/offers are critical. | Electronics, furniture, healthcare, education, travel. |
| High | Subscriptions/UPI AutoPay lifecycle | Harder to support recurring revenue with self-service APIs. | SaaS, OTT, insurance, investments, utilities. |
| High | Payouts API and dashboard | Harder for marketplaces/gig/lending platforms to automate disbursements. | Marketplaces, gig platforms, lenders, creator platforms. |
| High | Split settlement release/cancel APIs | Harder to run complex marketplace settlement controls. | Platforms, aggregators, franchises, travel, food delivery. |
| Medium | Native OTP/CVV-less/decoupled auth | Potentially higher drop-off or less control in custom card UX. | Large ecommerce, mobile apps, enterprise custom checkout. |
| Medium | UPI Reserve Pay | Less payment certainty for reserve/usage-based models. | Mobility, travel, rentals, metered services, agentic commerce. |
| Medium | TPV | Harder to support SEBI/BFSI compliant payment validation publicly. | Broking, mutual funds, securities. |
| Medium | Pay by Points | Missing loyalty-funded checkout instrument. | Retail, travel, high-frequency consumer commerce. |
| Medium | E-challan/ECMS | Less support for offline/NEFT/RTGS challan-based payments. | Education, government, B2B, healthcare. |
| Medium | Brand wallets | Less support for closed-loop wallets and stored value. | Retail chains, loyalty programs, marketplaces. |
| Medium | AI/MCP/agent toolkit | Less differentiated for AI-first developer workflows. | AI commerce, developer-led platforms, support automation. |
| Low to medium | Dashboard RBAC/export details | Less confidence for finance/ops teams evaluating controls. | Mid-market and enterprise merchants. |

## Deep Notes by User-Requested Categories

### Developer and Integration Experience

Plural's strongest differentiator is that it documents the integration as a complete developer product. The API reference is not just a marketing page; it lists resources, endpoint counts, HTTP methods, paths, authentication model, sandbox/production hosts, and product-specific endpoint groups. It also wraps these APIs with SDKs, Postman, OpenAPI, CLI, webhooks, error codes, test cards, go-live checklist, and AI/MCP tools.

Toucan's public messaging says developers can use REST APIs, SDKs, sandbox, and plugins, and can go live quickly. The missing public piece is implementation transparency: endpoint catalog, downloadable contracts, method-wise error handling, webhook retry/security details, test data, and CLI automation were not visible in the reviewed Toucan public material.

Practical implication: Plural looks easier for a developer to evaluate without talking to sales. Toucan looks more demo/sales-led unless private docs are shared after onboarding.

### Merchant Onboarding Experience

Toucan publishes a regulated onboarding policy with sourcing channels, account creation, registration form, eKYC, document verification, merchant scoring, fraud/risk/dispute/offboarding policies, and nodal officer contacts. That is strong compliance transparency.

Plural's advantage is the technical onboarding path: create developer account, verify email, generate test credentials, use UAT dashboard, explore test payments/refunds/settlements/webhooks, and integrate before production activation.

Practical implication: Toucan appears strong for compliance onboarding; Plural appears stronger for self-serve developer onboarding and sandbox readiness.

### Merchant Experience

Plural publicly documents day-to-day operations in more detail: payments search, refund workflows, settlement reports, UTR lookup, payout operations, payment link management, API keys, webhooks, team roles, exports, and dashboard/API tradeoffs.

Toucan documents an easy dashboard, real-time tracking, analytics, automated settlement/refund reports, and failed transaction visibility. It does not publicly expose RBAC, export formats, UTR lookup, module-by-module workflows, or automation boundaries in the reviewed pages.

Practical implication: Plural gives finance, support, and operations teams a clearer view of how they would run payments after launch.

### Payment Acceptance

Both cover mainstream acceptance. Plural differentiates in specialized acceptance modes: Pay by Points, e-challans, brand wallets, UPI Reserve Pay, TPV, Apple Pay, iFrame checkout, DCC/MCC, native OTP, CVV-less, and decoupled authorization.

Toucan differentiates in cross-border virtual accounts, e-FIRA, global bank accounts, transparent FX, and PA-CB positioning. Those are strong Toucan capabilities and should not be ignored.

Practical implication: Plural looks broader for online checkout variants and payment instruments. Toucan looks strong for Indian exporters and cross-border collections into INR.

### SR: Success Rate, Routing, and Reliability

Toucan publicly claims AI smart routing, 40% success-rate boost, 99.95% uptime, 10,000+ TPS, and AI fraud detection. Plural's public SR advantage is not necessarily routing marketing; it is controllable flows and implementation reliability: native OTP, CVV-less, UPI Reserve Pay, error codes, webhooks, retries, idempotency, test cards, and CLI diagnostics.

Practical implication: Toucan's SR story is platform-operated optimization. Plural's SR story is also developer-operated optimization.

### Affordability

Plural's affordability suite is materially deeper: it covers payment instruments, offer economics, SKU-level bundle logic, bank/brand offers, no-cost/low-cost EMI, cardless/down-payment models, full-swipe cashback, offer discovery, offer validation, down-payment details, and IMEI validation.

Toucan public pages mention EMI/BNPL, but pricing shows debit/credit card EMI and UPI on credit cards as coming soon. No comparable affordability API/offer docs were found.

Practical implication: For high-ticket retail, Plural has a clearer public product advantage.

### User Experience

Plural publicly supports more UX patterns: redirect hosted checkout, iFrame embedded checkout, custom UI, native OTP, CVV-less, saved-card express checkout, loyalty point redemption, familiar currency display through DCC/MCC, payment link lifecycle, and subscription mandate UI.

Toucan has broad checkout optimization and no-code tool messaging but fewer public UX flow specifics.

Practical implication: Plural gives product teams more public evidence for designing checkout journeys.

## Appendix A: Plural API Surface Not Evidenced Publicly in Toucan

The following Plural API groups are visible in the public API reference and were not matched by a comparable public Toucan endpoint catalog in reviewed sources.

| API group | Example operations visible in Plural public API reference | Public Toucan equivalent found? |
| --- | --- | --- |
| Authentication | Generate token. | No visible endpoint catalog. |
| Orders | Create, get by ID/reference, capture, cancel. | No visible endpoint catalog. |
| Refunds | Create refund. | Toucan mentions refunds, no public endpoint found. |
| Settlements | Get all settlements, get by UTR. | Toucan mentions settlement reports, no public endpoint found. |
| Split settlements | Release settlement, cancel settlement. | Toucan mentions cross-border split settlements, no public endpoint found. |
| Checkout | Generate checkout link. | Toucan hosted checkout mentioned, no public endpoint found. |
| Payment links | Create, get by ID/ref, cancel, resend notification. | Toucan links/no-code mentioned, no public endpoint found. |
| Card payments | Create payment, generate/submit/resend OTP, get card details, decoupled authorization. | Toucan 3DS/tokenization mentioned, no public endpoint found. |
| UPI payments | Create payment. | UPI supported, no public endpoint found. |
| NetBanking | Create payment. | Net banking supported, no public endpoint found. |
| Wallet | Create payment. | Wallets supported, no public endpoint found. |
| Pay by Points | Payment option / reward points flow. | Not found. |
| E-challans | Create/get challan, get challan PDF. | Not found. |
| Apple Pay | Authorize Apple Pay payment. | Not found. |
| International payments | DCC/MCC conversion, TCS compute, invoice capture/status/upload, AWB create/get/upload. | Toucan has cross-border features but not these public APIs. |
| Customers | Create, get, update customer. | No public endpoint found. |
| Tokenization | Generate token, get service provider token, cryptogram, delete token, customer tokens. | Tokenization mentioned, no public endpoint found. |
| Payouts | Create payout, list, balance, bulk payout, update/cancel scheduled payout. | Payouts mentioned broadly, no public endpoint found. |
| Affordability | Offer discovery, cardless offer discovery, down-payment details, validation, IMEI validation, create payment. | EMI/BNPL mentioned, no public endpoint found. |
| BNPL | Eligibility, create payment, OTP submit/resend. | BNPL mentioned, no public endpoint found. |
| Convenience fee | Calculate fee. | Not found. |
| Brand wallet | Create, validate, balance, activate/deactivate, reset PIN, load, history. | Not found. |
| Brand wallet payments | Create payment, OTP, add money. | Not found. |
| Subscriptions plans | Create/list/get/update/delete plans. | Subscription marketing mention only. |
| Subscriptions subscriptions | Create/list/get/update/cancel/pause/resume subscriptions. | Subscription marketing mention only. |
| Subscriptions presentations | Create/list/get/delete presentation, notify, execute debit, merchant retry. | Not found. |
| UPI Reserve Pay | Create SBMD subscription, fetch, create debit. | Not found. |

## Appendix B: Toucan Strengths Where Plural Should Not Be Assumed Better

This document focuses on Plural-only gaps, but a fair comparison should note Toucan strengths.

| Toucan strength | Why it matters |
| --- | --- |
| CrossStream global bank accounts | Dedicated multi-currency account details and local collection from 190+ countries are compelling for exporters/freelancers/SaaS. |
| Automated e-FIRA | Strong India export-compliance differentiator. Plural docs reviewed focus more on international cards/DCC/MCC/TCS/invoice/AWB. |
| Zero FX markup and transparent flat fee claims | Clear public pricing for inward remittance can be attractive versus opaque bank transfers. |
| PA-CB inbound/outbound and AD-I bank partnerships | Important for regulated cross-border payment credibility. |
| Verification suite | Toucan publicly offers bank account, IFSC, UPI ID, PAN, Aadhaar, GSTIN, and onboarding SDK. |
| AI smart routing and 10,000+ TPS positioning | Strong for merchants evaluating high throughput and payment success. |
| United Gates / real-estate vertical | Toucan has a verticalized property/community platform story that Plural docs do not mirror. |

## Strategic Interpretation

Plural appears to be positioned as a broad, API-first, developer-first online payments platform with deep product modules. Toucan Pay appears publicly positioned as a regulated payment infrastructure provider with strong domestic gateway, smart routing, instant settlements, cross-border export collection, compliance, verification, and vertical platform capabilities.

For a developer-led merchant, Plural's public documentation lowers evaluation risk. For an exporter needing virtual global accounts and e-FIRA, Toucan has a strong public proposition. For high-ticket retail, subscriptions, marketplaces, and platform businesses needing APIs for payouts/splits/affordability/subscriptions, Plural currently has more public product evidence.

## Recommended Toucan Parity Roadmap If Competing With Plural

This is not a build plan, but a market-gap prioritization based on public evidence.

| Priority | Area | Suggested public parity move |
| --- | --- | --- |
| P0 | Developer portal | Publish endpoint reference, OpenAPI spec, Postman collection, test cards, webhook docs, error codes, idempotency guidance, go-live checklist. |
| P0 | Checkout docs | Publish hosted/custom/iFrame or equivalent checkout-mode comparison, PCI responsibility, supported methods, callback/webhook flow, pre-auth/capture/cancel. |
| P0 | Merchant dashboard docs | Publish module docs for payments, refunds, settlements, exports, UTR lookup, team roles, payment links, API keys, webhook setup. |
| P0 | Payouts | Publish payout product/API docs for domestic rails, bulk payouts, balance, status, scheduled update/cancel, reconciliation. |
| P1 | Affordability | Move EMI/BNPL from broad/coming-soon claims to detailed offer discovery, validation, cardless/down-payment/no-cost/brand EMI docs if supported. |
| P1 | Subscriptions | Publish recurring billing/UPI AutoPay APIs, plans, mandates, retries, notifications, pause/resume/cancel, dashboard reporting. |
| P1 | Marketplace controls | Publish split settlement APIs, sub-merchant onboarding/visibility, release/cancel controls, fee handling. |
| P1 | UPI specialization | Add public TPV and UPI Reserve Pay/SBMD docs if supported. |
| P2 | Advanced card UX | Publish native OTP, CVV-less, decoupled auth, token migration, and 3DS data consistency docs if supported. |
| P2 | Alternative payment products | Consider Pay by Points, e-challan/ECMS, brand wallets, Apple Pay, DCC/MCC if strategically relevant. |
| P2 | AI developer tooling | If Toucan wants agentic-commerce positioning, publish MCP/agent toolkit or AI API developer workflows beyond routing/fraud. |

## Final Takeaway

Plural's publicly documented advantage is breadth plus implementation depth. It has more visible product modules, API groups, developer tools, lifecycle docs, and merchant-ops workflows. Toucan's public advantage is a focused regulated gateway and cross-border collection story with strong compliance, settlement, verification, and e-FIRA positioning.

If the comparison is specifically "what is present in Plural but not present in Toucan public material," the most defensible answer is: Plural has publicly documented advanced developer tooling, checkout variants, affordability, subscriptions, UPI Reserve Pay, Pay by Points, e-challans, brand wallets, payout APIs, split-settlement controls, merchant dashboard RBAC/export workflows, and AI-native payment tooling that are not publicly evidenced in Toucan Pay's reviewed pages.
