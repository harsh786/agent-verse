# AgentVerse × Food & Restaurant
> From kitchen to compliance — 7.5 million food businesses running smoother with autonomous operations.

---

## Executive Summary

India's food and beverage sector is a ₹7.76 lakh crore industry encompassing 7.5 million restaurants, cloud kitchens, QSR chains, banquet halls, and food manufacturing units — growing at 9% per year while simultaneously facing margin compression from aggregator commissions (15–30%), rising raw material costs, and a regulatory environment that spans FSSAI, GST, labour law, and municipal licensing. The average independent restaurant in India operates on 5–8% net margins — a fragile equilibrium where a single unsettled aggregator dispute (₹2–5 lakh), a missed FSSAI renewal (immediate closure), or 30% food wastage can turn a profitable outlet into a loss-maker overnight. AgentVerse deploys autonomous agents that handle the full operational backend — compliance, reconciliation, inventory, scheduling, customer relations, and GST — so restaurateurs can focus on what they do best: creating extraordinary food experiences. Restaurants deploying AgentVerse across their multi-outlet operations report 18–22% reduction in food cost, 40-hour/month savings in back-office work, and GST penalty elimination — delivering breakeven on platform investment within 6 weeks of deployment.

---

## Use Cases

---

### UC-1: FSSAI Compliance Management

**The Problem**
Non-compliance with FSSAI regulations results in immediate closure orders; 52% of Indian food businesses are running with expired or incorrect licences/registrations; the renewal process for a State/Central FSSAI licence involves portal navigation (foscos.fssai.gov.in), fee payment, inspection scheduling, uploading 8–12 supporting documents, and following up with FSSAI officials — a 30–45 day cycle that most operators leave until the last week, risking lapse.

**AgentVerse Solution**
The agent maintains a compliance dashboard for every FSSAI licence, registration, and permit across all outlets, sending renewal alerts 90, 60, 30, and 7 days in advance. It auto-fills the FSSAI renewal application on the FoSCoS portal using the existing licence data, generates the required annual turnover declaration and Food Safety Management System (FSMS) checklist, schedules the inspection appointment, and sends the licence holder a step-by-step tracker. When a new regulatory notification is issued (FSSAI standards update, new additive approval), the agent cross-checks the outlet's ingredient list against the updated standards.

**Agent Workflow**
1. Maintain compliance register: all outlet FSSAIs (Registration/State/Central), licence numbers, validity dates, class of business
2. 90-day advance: email + WhatsApp alert to outlet owner/FBO (Food Business Operator) and compliance manager
3. 60-day advance: auto-fill renewal application on FoSCoS portal (foscos.fssai.gov.in) using browser RPA with existing licence data pre-populated
4. Generate required documents: annual turnover declaration (CA certificate format), Food Safety Management System (FSMS) checklist, water testing report reminder, pest control log summary — via document generator
5. Upload documents to FoSCoS portal; pay renewal fee via online payment (₹100–₹7,500 depending on licence class) via browser RPA
6. Track application status on FoSCoS daily; notify FBO when inspection is scheduled
7. Prepare inspection readiness checklist: 25-point FSSAI inspection criteria; email to outlet manager with 5-day preparation timeline
8. If FSSAI issues improvement notice or show cause: parse notice via document parser; draft response within 72 hours via document generator
9. Post-renewal: download new licence certificate; archive to document management system; update compliance register
10. Quarterly: check FSSAI Food Safety Mitra network for any new notifications affecting the outlet's product category
11. Web search: monitor FSSAI gazette notifications for regulatory standard amendments (e.g., food additives, labelling requirements)
12. Generate annual FSSAI compliance report for multi-outlet chains: all licences, renewal status, inspection outcomes — via document generator; share with management

**Tools Used:** Browser RPA (FoSCoS portal foscos.fssai.gov.in), document generation, email, WhatsApp Business, scheduler, web search, document parser

**Revenue Model:** ₹2,000/outlet/month FSSAI compliance module; multi-outlet pricing ₹1,200/outlet/month above 5 outlets

**ROI:** Avoiding 1 closure notice saves the restaurant ₹5–50 lakh in lost revenue + legal costs; FSSAI penalty for operating with expired licence: ₹1–6 lakh; module pays back in first avoided incident

**Target Customers:** QSR chains, multi-outlet restaurant groups, cloud kitchen operators, hotel F&B departments, food manufacturing SMEs

---

### UC-2: Food Delivery Aggregator Reconciliation (Swiggy / Zomato / ONDC)

**The Problem**
A restaurant doing ₹10 lakh/month in delivery sales across Swiggy, Zomato, and ONDC faces 3 different commission structures (15–30%), 3 different settlement cycles (weekly/bi-weekly), 3 different cancellation policies, and 3 different GST tax treatment interpretations; monthly reconciliation — verifying that the ₹7.2 lakh received in bank matches the ₹10 lakh in orders minus commissions and adjustments — takes a skilled accountant 15–20 hours with high error rates.

**AgentVerse Solution**
The agent downloads settlement reports from all 3 platforms daily, reconciles each order-level transaction against the restaurant's own POS data, categorises variances (commission differences, cancellations, adjustments, marketing contributions), and creates a single unified reconciliation register. GST Input Tax Credit (ITC) on platform commission charges is computed and presented ready-to-file. Any underpaid settlement or disputed cancellation claim is escalated with evidence to the platform's partner support portal via automated ticket.

**Agent Workflow**
1. Daily: download settlement statements from Swiggy Partner API, Zomato API, and ONDC buyer app reports
2. Pull matching order data from restaurant POS system (Petpooja/Posist/Rista) via POS connector for the settlement period
3. Order-level reconciliation: match each platform order ID to POS record; verify order value, cancellation, and delivery charges
4. Calculate expected settlement: order value − commission % − GST on commission − cancellation deduction − marketing campaign deduction
5. Compare expected vs. actual settlement amounts; identify discrepancies per order
6. Categorise discrepancy types: underpaid commission, missing orders, disputed cancellations, promotional deduction errors
7. For underpayments > ₹500 per order or > ₹5,000 aggregate: auto-raise dispute ticket on Swiggy/Zomato partner portal via browser RPA
8. GST computation: commission paid to platforms = taxable service; extract GSTIN of Swiggy/Zomato; verify ITC in GSTR-2B
9. Generate unified reconciliation register (Excel + PDF): all 3 platforms, order-level detail, variances, resolved/pending disputes
10. Journal entry generation: pass accounting entries in Tally/Zoho for platform income, commission expense, and GST adjustment
11. Monthly P&L by platform: revenue, commission cost, cancellation rate, effective yield per platform — for channel profitability analysis via code execution
12. Send reconciliation summary report to owner and CA via email; flag if any platform owes > ₹10,000 in unresolved disputes

**Tools Used:** Swiggy Partner API, Zomato Partner API, ONDC connector, POS connector (Petpooja/Posist), accounting connector (Tally/Zoho), browser RPA (Swiggy/Zomato partner portals), document generation, email, code execution

**Revenue Model:** ₹3,000/outlet/month reconciliation module; ONDC-specific setup ₹5,000 one-time

**ROI:** Recovering ₹15,000–₹30,000/month in underpaid settlements; 15-hour saving per month at ₹300/hour = ₹4,500/month; full ROI in 2 months

**Target Customers:** Multi-platform restaurants, cloud kitchen operators, aggregator-dependent QSR chains, dark kitchen companies with 5+ brands

---

### UC-3: Menu Engineering and Recipe Cost Optimization

**The Problem**
Food cost should ideally be 28–35% of menu price; most Indian restaurants manage food cost tracking in Excel or not at all; a 5% food cost increase (from ingredient price rise or wastage) on a ₹5 lakh/month revenue restaurant erodes margin by ₹25,000/month — ₹3 lakh/year; menu prices are set at launch and rarely revisited despite volatile commodity prices for oil, onion, tomato, and dal.

**AgentVerse Solution**
The agent builds a recipe cost database for every menu item: actual ingredient quantities from standardised recipes × current market prices (auto-updated weekly from APMC/Agmarknet data). It identifies which items have food cost > 40% (underpriced "stars" consuming margin) and which items have food cost < 20% but low sales volume (underperforming "dogs"). The menu engineering output — with recommended price adjustments, portion rationalisation, and dish retirement suggestions — is generated monthly and presented to the owner with projected P&L impact.

**Agent Workflow**
1. Build/update recipe cost database: for each menu item, input standard recipe (ingredients + quantities) from menu engineering spreadsheet
2. Web search (weekly): fetch current APMC/Agmarknet/NHB mandi prices for key commodities at the restaurant's nearest mandi (onion, tomato, potatoes, chicken, mutton, oil, dal, rice)
3. Update ingredient cost per unit in recipe database using latest mandi prices + transport margin (typically 10–15%)
4. Calculate food cost per dish: sum(ingredient qty × unit cost) / selling price × 100 = food cost %
5. Pull sales data by dish from POS connector: units sold per dish in the period
6. Create menu engineering matrix (code execution): plot each dish on Stars/Plowhorses/Puzzles/Dogs quadrant using contribution margin × popularity
7. Flag dishes with food cost > 38%: recommend price increase or portion reduction
8. Flag "Stars" (high margin, high popularity): recommend promotion and placement on menu
9. Seasonal price alert: if key ingredient price spikes > 20% in a week, alert owner via WhatsApp with affected dishes and recommended price adjustments
10. Generate batch cooking recommendations: ingredients used in multiple dishes — identify bulk purchase opportunities
11. Create monthly recipe cost optimisation report: overall food cost %, dish-level analysis, recommended actions — via document generator
12. Send report to owner/chef with P&L impact of implementing recommendations; calculate projected monthly saving via code execution

**Tools Used:** Web search (Agmarknet/APMC/NHB), POS connector, accounting connector, code execution (Python/pandas/matplotlib), document generation, email, WhatsApp Business, knowledge base (recipe database)

**Revenue Model:** ₹2,500/outlet/month menu engineering module; one-time recipe database setup ₹5,000 per outlet

**ROI:** Reducing food cost from 36% to 32% on ₹5 lakh/month revenue = ₹20,000/month margin improvement = ₹2.4 lakh/year; 8× ROI on ₹30,000/year module cost

**Target Customers:** Independent restaurants (1–5 outlets), cloud kitchens, QSR chains with standardised menus, hotel F&B departments focused on margin improvement

---

### UC-4: Inventory Management and Wastage Reduction

**The Problem**
Food wastage in Indian restaurants averages 20–30% of purchased inventory value; a ₹5 lakh/month restaurant purchasing ₹1.75 lakh in raw materials wastes ₹35,000–₹52,000/month — ₹4.2–6.2 lakh per year; FIFO (First In, First Out) compliance requires daily monitoring that no kitchen team consistently executes manually; overordering from uncertainty compounds the problem.

**AgentVerse Solution**
The agent tracks inventory levels in real-time by integrating with the POS system (depleted quantities from recipe consumption) and the purchase order log, and alerts the kitchen manager when any ingredient's stock is below par level or dangerously above maximum stock (overordering risk). It runs daily expiry checks, generates FIFO-compliant pick lists for kitchen use, and analyses wastage patterns to identify the top 5 waste culprits every week. Purchase orders for the next 3 days are auto-generated based on consumption forecasts adjusted for reservations and historical weekend/weekday patterns.

**Agent Workflow**
1. Morning trigger (06:30): calculate theoretical inventory = opening stock + purchases received − recipe consumption (from POS units sold × recipe quantities)
2. Inventory manager does physical spot-check of high-value items (meat, seafood, dairy); enters actuals into inventory app or WhatsApp bot
3. Agent computes variance: theoretical vs. physical — flag items with > 5% unexplained variance (potential pilferage or mismeasurement)
4. FIFO compliance check: identify items where older stock must be consumed first; generate "Use First" list for kitchen via document generator
5. Expiry alert: flag items expiring within 48 hours; suggest dishes to clear the ingredient; send alert to chef via WhatsApp
6. Wastage log: record items discarded (expired, spoiled, over-prepared); categorise wastage reason
7. Pull reservations from booking system for next 3 days; estimate covers by meal type
8. Demand forecast via code execution: historical consumption × reservation factor × day-of-week factor = projected consumption for each ingredient
9. Auto-generate purchase order for next 3 days: required quantity = (projected consumption + safety stock) − current stock
10. Send purchase order to preferred supplier via WhatsApp message + email; allow supplier to confirm/counter-quote
11. Weekly wastage analysis report: top 5 wasted items by value, root cause (overproduction/expiry/spoilage), recommended actions
12. Monthly inventory report: food cost %, wastage %, par levels review, stock turn ratio — via document generator; email to owner

**Tools Used:** POS connector (Petpooja/Posist/Rista), inventory management connector, email, WhatsApp Business, code execution (demand forecasting), document generation, accounting connector, booking system connector

**Revenue Model:** ₹2,000/outlet/month inventory module; ₹1,000/outlet/month above 5 outlets

**ROI:** Reducing wastage from 25% to 12% on ₹1.75 lakh/month food cost = ₹22,750/month saving = ₹2.73 lakh/year; 11× ROI on ₹24,000/year module cost

**Target Customers:** Independent restaurants, hotel F&B departments, institutional catering companies, cloud kitchen operators, QSR chains with central kitchen

---

### UC-5: Staff Scheduling Optimization

**The Problem**
Labour cost should be 25–35% of revenue; peak hours (12–2 PM, 7–10 PM) are chronically understaffed while slow hours are overstaffed — a 15-table restaurant pays 8 servers from 11 AM to 3 PM but has 2 covers in the first hour; scheduling via WhatsApp groups leads to 15% no-show rates; last-minute cancellations leave kitchens short-staffed, degrading service quality and Zomato/Swiggy ratings.

**AgentVerse Solution**
The agent builds a data-driven staff schedule by analysing historical footfall from POS data, factoring in confirmed reservations, upcoming events (weekends, local festivals, IPL match days), and staff availability and skills. The weekly schedule is published on Monday via WhatsApp, with shift reminders sent 2 hours before each shift. If a confirmed staff member marks absent, the agent immediately identifies and contacts the on-call replacement. Labour cost per shift is tracked against revenue to maintain the target labour cost percentage in real time.

**Agent Workflow**
1. Pull last 8 weeks of hourly footfall data from POS (orders/hour by day of week); identify peak hour patterns
2. Check reservations for next 7 days from booking system; flag high-cover evenings requiring extra staffing
3. Check upcoming events: local holidays, IPL/cricket matches, festival days from Google Calendar connector
4. Pull staff availability from WhatsApp responses or HR system; note leaves approved, part-time availability
5. Generate optimal shift schedule: minimum cover requirements per shift (head chef, sous chef, line cook, server, runner, cashier) via code execution (constraint-based scheduling algorithm)
6. Compute weekly labour cost projection: shifts × hours × hourly wage; compare against target (30% of projected revenue)
7. If labour cost projected > 35%: suggest schedule adjustments — split shifts, reduce slow-hour staffing
8. Publish weekly schedule via WhatsApp Group message (formatted table) and email to all staff by Monday 10 AM
9. Shift reminder: WhatsApp message 2 hours before shift starts to each assigned staff member
10. On no-show alert (staff fails to confirm arrival at shift start): identify on-call replacement; WhatsApp replacement request with ₹200 incentive for cover shift
11. Biometric attendance API: record actual in/out times; compare to scheduled; compute overtime automatically
12. Weekly labour cost report: actual vs. budgeted, overtime analysis, no-show history — via document generation; email to owner and operations manager

**Tools Used:** POS connector, scheduler, WhatsApp Business, biometric attendance API, code execution (scheduling optimisation), accounting connector, email, Google Calendar connector, booking system connector

**Revenue Model:** ₹1,500/outlet/month scheduling module; included in operations bundle

**ROI:** Reducing labour cost from 38% to 32% on ₹8 lakh/month revenue = ₹48,000/month saving; eliminating 15% no-shows saves 12 shifts/month of service degradation

**Target Customers:** Restaurants with 10+ staff, QSR chains managing 20+ part-time staff, hotel banquet operations, institutional catering with shift-based labour

---

### UC-6: Customer Review Monitoring and Response

**The Problem**
A restaurant with 1,000 Zomato/Swiggy/Google reviews receives 50–100 new reviews per month; each negative review with no owner response costs 5–10 potential new customers who read it and choose a competitor; the average restaurant response rate is < 30%; a single viral negative review (1,000+ likes on Zomato) can cost ₹2–5 lakh in monthly revenue before it scrolls off the top.

**AgentVerse Solution**
The agent monitors all review platforms (Zomato, Swiggy, Google My Business, TripAdvisor, MagicPin) every 2 hours, classifies reviews by sentiment and issue type (food quality, delivery, service, packaging), and drafts personalised responses within 30 minutes. Negative reviews trigger an internal investigation workflow: the concerned staff member or delivery issue is identified, and the owner is notified with resolution options. Aggregate review insights — top complaints, praise themes, rating trends — are reported weekly to enable operational improvements.

**Agent Workflow**
1. Every 2 hours: browser RPA scrape of Zomato restaurant partner portal, Swiggy restaurant dashboard, Google My Business API — collect all new reviews
2. Sentiment classification: classify each review as Positive/Neutral/Negative; extract key themes (food quality, delivery time, packaging, service, value, cleanliness)
3. Priority alert: if Negative review with rating ≤ 2 stars received — WhatsApp alert to owner within 5 minutes
4. Draft personalised response for each review: positive reviews get warm brand-voice acknowledgement; negative reviews get empathetic, resolution-focused response
5. For specific complaints (cold food, missing item, rude staff): generate internal investigation note; ask staff/manager for context before sending response
6. Owner/manager review via HITL: approve or edit the draft response (30-second approval workflow)
7. Post approved responses on Zomato/Google/Swiggy via browser RPA within 30 minutes of new review
8. For severe complaints (food safety, foreign object in food): escalate immediately to owner; draft compensation offer (voucher/refund) via document generation
9. Track rating trends: weekly average rating by platform; alert if Zomato rating drops below 4.0 (impacts discovery algorithm)
10. Identify serial complainers vs. genuine feedback; flag patterns (same issue repeated across 3+ reviews = systemic problem)
11. Generate weekly review intelligence report: star distribution, top 5 praise themes, top 5 complaint themes, response rate, response time avg — via document generator
12. Monthly insights report: rating trend, competitor benchmarking (nearby restaurants' ratings scraped for context), recommended operational fixes — email to owner and operations head

**Tools Used:** Browser RPA (Zomato partner portal, Swiggy partner portal, Google My Business API), web search, email, WhatsApp Business, Slack, document generation, HITL, code execution (sentiment analysis)

**Revenue Model:** ₹1,500/outlet/month review management; ₹800/outlet/month above 10 outlets

**ROI:** Improving Zomato rating from 3.8 to 4.2 = 15–20% more organic discovery clicks; recovery from 1 viral negative review saves ₹2–5 lakh in potential lost revenue

**Target Customers:** All restaurant types; particularly aggregator-dependent cloud kitchens where rating = primary acquisition channel; hotel F&B departments; QSR chains with franchisee brand reputation at stake

---

### UC-7: Supplier Payment and Vendor Management

**The Problem**
A restaurant dealing with 15–20 suppliers — fresh produce, dairy, meat, beverages, packaging, ice — each with different payment cycles (some daily cash, some weekly credit, some 30-day cheque) must manage a complex payables calendar; a missed dairy supplier payment leads to 2-day supply cut-off; a missed beverage distributor payment risks losing premium brand allocation; cash flow management requires knowing ₹3.5 lakh in payables spread across 18 vendors this week.

**AgentVerse Solution**
The agent maintains a complete vendor payment calendar with each supplier's payment terms, outstanding balance, and supply criticality ranking. It generates a weekly cash outflow forecast (which suppliers to pay, how much, by which date), initiates UPI/NEFT payments for approved vendors, and sends payment confirmations with UTR numbers via WhatsApp to the supplier. For produce vendors requiring daily cash, digital payments via PhonePe Business or Google Pay Business are tracked and reconciled. Stock-out risk from overdue payments is flagged 3 days in advance.

**Agent Workflow**
1. Maintain vendor master: 18 vendors, payment terms (daily/weekly/30-day credit), outstanding balance, supply criticality (A=must not stop, B=alternate available, C=non-critical)
2. Weekly Monday trigger: pull all outstanding purchase invoices from accounting connector by vendor
3. Identify payments due this week: invoices crossing credit period; pre-agreed payment dates
4. Cross-reference with bank balance from bank API; compute available cash after minimum operating reserve (₹1 lakh buffer)
5. Prioritise payment queue: Tier A vendors (daily produce, dairy) first; then based on credit period breach
6. Generate weekly payment plan: vendor name, amount, preferred payment mode, due date — presented to owner for HITL approval
7. On approval: initiate NEFT/IMPS via corporate bank API (HDFC/ICICI/Axis) or Razorpay Payout for each vendor
8. Send payment confirmation WhatsApp to each supplier: "₹15,000 transferred to your account. UTR: XXXXXXXXXX. Thank you."
9. For cash vendors: generate payment voucher via document generator; alert kitchen manager to hand-deliver with cash from petty cash box
10. On payment: update accounting connector (mark invoice paid); update vendor ledger
11. Monthly vendor analysis: payment delays by vendor, credit utilisation, discount opportunities (early payment discount) — code execution
12. Supplier relationship alert: if any A-category vendor overdue by > 7 days for any reason — urgent WhatsApp + email to owner

**Tools Used:** Accounting connector, bank API (HDFC/ICICI/Axis), Razorpay Payout, WhatsApp Business, document generation, email, code execution, HITL

**Revenue Model:** ₹1,000/outlet/month vendor payment module; included in operations bundle; ₹20/transaction for payment initiation above 50/month

**ROI:** Early payment discounts of 2% on ₹1.5 lakh/month payables = ₹3,000/month; eliminating 2 supply disruptions/year saves ₹40,000 in emergency purchases

**Target Customers:** Full-service restaurants (15+ vendors), hotel procurement departments, QSR chains with central purchase teams, institutional caterers

---

### UC-8: GST Reconciliation for Restaurants

**The Problem**
Restaurant GST is uniquely complex: 5% GST for turnover < ₹1.5 crore or for restaurants without AC (no ITC available); 12% for AC restaurants with alcohol; 18% for 5-star hotels; delivery orders via Swiggy/Zomato notified as 5% (operators are not eligible to collect from customer); Swiggy/Zomato collect and remit GST under Section 9(5) reverse charge; monthly reconciliation for a multi-format restaurant group with 5 outlets across these categories takes 8–10 hours and is error-prone.

**AgentVerse Solution**
The agent auto-classifies every transaction by the correct GST slab based on the outlet type, service mode (dine-in/delivery/takeaway), and customer category, reconciles GSTR-1 output with POS data, cross-checks aggregator-collected GST with the platform settlement statements, and handles the GSTR-3B net liability computation accounting for the Section 9(5) reversal. Monthly GSTR-9 annual return is 95% pre-populated from the monthly returns.

**Agent Workflow**
1. Pull all POS transactions for the month from each outlet: transaction type (dine-in/delivery/takeaway), amount, GST rate applied, customer invoice number
2. Classify each transaction by correct GST applicability: outlet-specific slab, Swiggy/Zomato delivery (5% ECO, collected by aggregator per Section 9(5))
3. Reconcile POS-reported output GST vs. expected GST by classification: flag misclassified transactions
4. Download aggregator settlement reports: verify that GST collected by Swiggy/Zomato on delivery orders equals 5% of aggregated delivery order value
5. GSTR-1 preparation: classify sales into B2B (GST invoices issued), B2C large (> ₹2.5 lakh), B2C small, exempted, ECO-operator reported sales
6. Browser RPA: login to GST portal (gst.gov.in) for each outlet GSTIN; file GSTR-1 via JSON upload
7. Download GSTR-2B: verify ITC on inputs (packaging, gas, non-food supplies) — food ingredients do not qualify for ITC under Section 17(5)
8. Prepare GSTR-3B: compute net liability = output tax − eligible ITC; separate ECO supplies for Swiggy/Zomato-collected GST
9. Cash flow impact: calculate net GST payable via Electronic Cash Ledger; prepare challan PMT-06 if credit insufficient
10. Submit GSTR-3B after CA review (HITL); retain acknowledgement
11. Quarterly ITC reconciliation: verify all ITC claimed matches GSTR-2B; identify mismatches for follow-up with vendors
12. Generate GST health report for management: outlet-wise GST liability, ITC efficiency, compliance calendar status — via document generator

**Tools Used:** GST portal (gst.gov.in) browser RPA, accounting connector (Tally/Zoho), POS connector, Swiggy/Zomato Partner API (settlement data), document generation, email, HITL, code execution

**Revenue Model:** ₹2,500/GSTIN/month; multi-outlet pricing ₹1,500/GSTIN/month above 3 GSTINs

**ROI:** Avoiding GST scrutiny notices (common in restaurants due to misclassification) which cost ₹50,000–₹2 lakh in demand + ₹25,000 in CA fees to defend; time saving: 8 hours/month recovered

**Target Customers:** Multi-outlet restaurant groups, hotel F&B departments with multiple GST registrations, franchise restaurant operators, large QSR chains

---

### UC-9: Franchise Compliance and SOP Monitoring

**The Problem**
A 30-outlet QSR chain needs consistent compliance across all outlets for food safety, portion standards, brand standards, and hygiene protocols — monitored via infrequent physical audits (once a quarter at best); between 2 audits is 90 days of potential brand-damaging non-compliance; a single food safety incident at one franchise outlet = national press coverage damaging all 30 outlets' ratings.

**AgentVerse Solution**
The agent implements a continuous digital audit programme: outlet managers submit daily compliance photos via WhatsApp (food temperature logs, equipment cleanliness, uniform compliance, portion display), and Vision LLM analyses each photo against the brand's SOP standards, scoring the outlet on 40+ criteria without human audit team involvement. Non-compliant items trigger immediate corrective action requests to the franchise owner. A monthly franchise ranking report ranks all 30 outlets, highlighting consistent underperformers for intensive support.

**Agent Workflow**
1. 08:00 daily: WhatsApp message to each outlet manager requesting 5 compliance photos: food temperature log, cleaned prep area, uniform check, entrance cleanliness, display/signage
2. Outlet manager submits photos via WhatsApp; photos automatically collected and associated with outlet ID and date
3. Vision LLM analysis: for each photo, score against SOP checklist (e.g., "Temperature log filled and visible: Yes/No", "Hair nets worn by all staff: Yes/No", "Floor clean: Score 1–5")
4. Identify non-compliance: items scoring < 3/5 or binary non-compliance; generate specific corrective action with photo evidence
5. Send corrective action request to franchise owner via WhatsApp: "Non-compliance detected: Staff member not wearing hairnet (Photo attached). Resolve and submit photo within 4 hours."
6. Track corrective action closure: outlet re-submits correction photo; Vision LLM verifies resolution
7. Daily compliance score per outlet: sum of all criteria scores / total possible score × 100 = daily compliance %
8. Weekly compliance report: outlet-wise scores, top 3 non-compliances, corrective action closure rate — via document generator; email to Franchise Operations Head
9. If outlet falls below 70% compliance for 3 consecutive days: trigger escalation email to franchisee principal + regional manager
10. Monthly franchise ranking: all 30 outlets ranked by average compliance score; bottom 5 flagged for Support Visit
11. Mystery audit simulation: agent-driven photo analysis of social media posts (Google, Zomato photos posted by customers) for visible compliance issues — web search
12. Annual compliance benchmark report: trend analysis across all outlets, corrective action efficiency, SOP update recommendations based on recurring failures — via document generator

**Tools Used:** WhatsApp Business, Vision LLM (photo analysis), web search, document generation, email, Slack, code execution (scoring/ranking), scheduler

**Revenue Model:** ₹3,000/outlet/month franchise compliance module; minimum 10-outlet licence ₹25,000/month

**ROI:** Preventing 1 food safety incident = ₹5–50 lakh in brand recovery costs; improving franchise compliance reduces audit team headcount by 3–4 FTEs (₹30–60 lakh/year savings)

**Target Customers:** QSR franchise chains (McDonald's-style operators), café chains, food courts in malls, institutional catering franchisors

---

### UC-10: Banquet and Event Booking Management

**The Problem**
A wedding venue and banquet hall receives 10–15 event inquiries per week — weddings, corporate events, birthday parties, kitty parties; each inquiry requires: availability confirmation, menu quotation (per person rate × estimated covers), advance payment collection, vendor coordination (flowers, DJ, AV, catering), and day-of timeline management; the current process is a mix of WhatsApp messages and word documents that results in double-bookings, missed deposits, and day-of chaos.

**AgentVerse Solution**
The agent manages the complete banquet booking lifecycle: it checks real-time availability from the calendar, generates customised quotations within 2 minutes of inquiry, collects advance payments via Razorpay payment links, coordinates with all 8 vendors via WhatsApp, sends the event briefing to the banquet team 3 days before, and runs a day-of checklist at hourly intervals. Inquiry-to-booking conversion rate improves from the industry average of 18% to 40%+ because of speed-of-response and professional documentation.

**Agent Workflow**
1. WhatsApp/email inquiry received: extract event type, date, approximate guest count, budget indication
2. Calendar connector check: verify venue availability for requested date + 1 hour before/after for setup/breakdown
3. If available: generate customised quotation within 2 minutes — per-person menu options, setup charges, AV costs, decoration tiers — via document generator
4. Send quotation PDF via email and WhatsApp with digital booking link (Razorpay payment page)
5. On advance payment (25% of total): confirm booking in calendar; send confirmation letter with event brief template to fill
6. 30 days before event: send detailed event brief to client; request final guest count, menu selection, dietary requirements
7. 14 days before: coordinate with vendors via WhatsApp messages — florist, DJ, AV company, photographer — share event brief and call-time
8. 7 days before: collect final guest count; generate final invoice for remaining balance; send Razorpay payment link
9. 3 days before: generate banquet captain's briefing document: event timeline, table layout, menu runsheet, special instructions, contact list — via document generator
10. Day-of: hourly WhatsApp check-in to banquet captain; escalate any deviation from timeline to event manager
11. Post-event: auto-send thank you message and feedback form to client via WhatsApp; collect NPS rating
12. Generate post-event revenue report: total billed, advance received, balance collected, vendor costs, net margin — via accounting connector + document generation

**Tools Used:** Calendar connector, WhatsApp Business, Razorpay API, document generation, email, scheduler, accounting connector

**Revenue Model:** ₹5,000/venue/month booking management module; ₹500/event above 20 events/month

**ROI:** Improving inquiry-to-booking from 18% to 38% on 50 inquiries/month = 10 additional bookings × ₹50,000 average = ₹5 lakh additional monthly revenue; module cost ₹5,000/month — 100× ROI

**Target Customers:** Banquet halls, wedding venues, hotel event departments, resort event teams, corporate event venues

---

### UC-11: Loyalty Program Management and Win-Back

**The Problem**
Average restaurant retention rate in India is 30–40%; a ₹10 lakh/month restaurant improving retention by 10% gains ₹1 lakh/month in incremental revenue from existing customers who already have lower acquisition cost; loyalty programs exist in most POS systems but are never activated — 70% of loyalty points accumulated by customers expire unredeemed, representing wasted marketing investment.

**AgentVerse Solution**
The agent segments the customer database by recency, frequency, and monetary value (RFM analysis), identifies churning customers (no visit in 45+ days), and launches personalised win-back campaigns via WhatsApp, SMS, and email with targeted offers. High-value customers (top 20% by spend) receive VIP treatment — early access to events, birthday offers, chef specials. The agent tracks campaign performance and A/B tests offer types (discount vs. complimentary item vs. loyalty points double) to optimize conversion.

**Agent Workflow**
1. Weekly: extract customer transaction history from POS connector; build RFM (Recency/Frequency/Monetary) score for each customer
2. Segment customers: Champions (high R/F/M), Loyal, At-Risk, Churned (no visit in 45+ days), New
3. Identify "At-Risk" customers: visited 5+ times previously, last visit 30–44 days ago — prime win-back targets
4. Code execution: generate personalised win-back offer per segment — ₹100 off for At-Risk, 2-for-1 dessert for Churned, birthday bonus for upcoming birthdays
5. WhatsApp campaign via WhatsApp Business: personalised message with customer name, "We miss you" message, and specific offer with redemption code
6. Email campaign via Mailchimp: visually designed menu highlight email for top-tier customers
7. POS connector: create offer/coupon code in POS with redemption tracking per customer
8. Track campaign performance: open rate, redemption rate, ROI per campaign via code execution
9. For un-redeemed offers after 14 days: send follow-up WhatsApp reminder with shorter validity (urgency creation)
10. Birthday automation: 2 days before customer birthday → WhatsApp with complimentary dessert offer; auto-generated birthday greeting card via document generator
11. Monthly loyalty report: segment migration (customers moving from "At-Risk" to "Loyal"), retention rate improvement, revenue attributed to loyalty programme — via document generator
12. A/B test results: compare redemption rates across offer types; update offer strategy for next month based on winning variant

**Tools Used:** POS connector, WhatsApp Business, Mailchimp, email, code execution (RFM analysis, A/B testing), document generation, accounting connector

**Revenue Model:** ₹2,500/outlet/month loyalty module; Mailchimp API costs passed through at cost

**ROI:** Improving retention by 8% on ₹10 lakh/month = ₹80,000/month incremental revenue; win-back campaign converting 20% of 200 churned customers = ₹2 lakh in recovered revenue

**Target Customers:** Restaurants with 500+ customer database, café chains, QSR chains with mobile app, hotel F&B with loyalty program, fine-dining restaurants

---

### UC-12: Food Safety Incident Response and Traceability

**The Problem**
A food safety incident — customer illness attributed to the restaurant — requires tracking which batch of ingredient caused the issue, which supplier provided it, which other dishes used it, and which other customers received those dishes in the same 24-hour window; without traceability, the restaurant's liability exposure is uncapped and could run into crores; FSSAI Regulation 4 requires food business operators to maintain complete traceability records.

**AgentVerse Solution**
The agent provides instant traceability when an incident is reported: it traces the suspected ingredient through purchase records, maps all recipes that included it, identifies all orders placed in the suspect time window, and generates a full incident timeline for FSSAI regulatory response and legal protection. If a product recall is needed, the agent identifies all potentially affected customers by order data and notifies them proactively — transforming legal liability into brand trust through transparency and speed.

**Agent Workflow**
1. Incident reported (customer complaint of illness): receive via WhatsApp or incident form; classify by severity (mild illness / hospitalisation / serious complaint)
2. Document incident details: customer name, date/time of visit, dishes ordered (from POS transaction record), symptoms reported
3. Query POS connector: pull all orders containing the suspected dish in the 48 hours around the incident
4. Query inventory connector: trace suspected ingredient — which supplier batch, delivery date, lot number, quantity received
5. Map all recipes containing the suspect ingredient: code execution query on recipe database
6. Identify all other orders in past 72 hours that included any recipe containing the suspect ingredient
7. Cross-reference with GSTR records and customer database to identify contact information for potentially affected customers
8. Generate incident timeline document (FSSAI Format): chronological trace from raw material receipt to service — via document generator
9. If multiple customers affected: initiate proactive customer notification via WhatsApp + email with health advisory and refund offer
10. Stop service of suspect ingredient: alert kitchen via WhatsApp; mark ingredient as quarantined in inventory connector
11. Contact supplier via email with incident details; request lot trace records; initiate supplier quality investigation
12. File FSSAI incident report (if required: FBO must report to State Food Safety Officer within 24 hours for serious incidents) via document generator + email to SFSO; prepare legal statement with incident timeline for legal counsel

**Tools Used:** Inventory connector, POS connector, document generation, email, WhatsApp Business, accounting connector, web search, code execution

**Revenue Model:** Included in Professional and Enterprise tiers; ₹1,000/incident for Starter tier incident response

**ROI:** Avoiding 1 serious food safety litigation = ₹10–50 lakh in legal + settlement costs; proactive customer notification converts a crisis into a trust-building moment, preserving ₹5–15 lakh in future revenue from affected customers' social networks

**Target Customers:** All food businesses, but particularly QSR chains (recall exposure across thousands of orders), hotel restaurants, institutional caterers

---

## Monetization Strategy

**Tier 1 — Starter | ₹5,000/outlet/month**
For independent restaurants and single-outlet food businesses.
- 3 modules: FSSAI compliance, review monitoring, inventory basics
- 500 agent-runs/month; WhatsApp up to 1,000 messages/month
- GST filing support: 1 GSTIN included
- Onboarding: ₹5,000 one-time POS integration setup
- Target: Independent restaurants, cloud kitchens, single-outlet cafes, tiffin services with 15+ staff

**Tier 2 — Growth | ₹15,000/outlet/month (min 3 outlets)**
For growing restaurant chains and multi-outlet operators.
- All Starter modules + delivery reconciliation, menu engineering, staff scheduling, supplier payment, loyalty management
- Unlimited agent-runs; full POS + accounting integration (Petpooja/Posist + Tally/Zoho)
- Multi-platform review management (Zomato/Swiggy/Google/TripAdvisor) included
- Onboarding: ₹20,000 one-time for 3-outlet deployment
- Target: 3–20 outlet restaurant chains, cloud kitchen brands with multiple concepts, QSR sub-franchisees

**Tier 3 — Enterprise | ₹8,000/outlet/month (min 20 outlets, annual contract)**
For large QSR chains, hotel F&B groups, and institutional caterers.
- All Growth modules + franchise compliance (Vision LLM photo auditing), banquet management, food safety traceability, custom ERP integration
- White-labeled customer-facing loyalty app integration, API access, custom dashboards for operations heads
- Dedicated food industry customer success manager; quarterly business review with F&B operations analytics
- Onboarding: ₹1,50,000 for 20-outlet rollout with dedicated implementation team
- Target: 20+ outlet QSR chains, hotel groups (ITC/Marriott F&B teams), institutional caterers serving 5,000+ covers/day

---

## Sample AgentManifest YAML

```yaml
apiVersion: agentverse/v1
kind: AgentManifest
metadata:
  name: delivery-reconciliation-engine
  domain: food-restaurant
  version: "1.4.0"
  tenant: spice-route-restaurants

spec:
  goal: |
    Daily: Download settlement reports from Swiggy, Zomato, and ONDC.
    Reconcile each platform's payout against POS order data.
    Identify settlement discrepancies and underpayments.
    Raise automated disputes for variances above ₹500 per order.
    Update accounting system with reconciled figures.
    Report to owner by 10 AM daily.

  triggers:
    - type: cron
      schedule: "0 7 * * *"      # 07:00 AM daily
      timezone: "Asia/Kolkata"
    - type: webhook
      source: swiggy_partner_api
      event: settlement_report_available

  tools:
    - swiggy_partner_api:
        restaurant_id: "${SWIGGY_REST_ID}"
        auth: api_key
        capabilities: [download_settlement, list_orders, raise_dispute]
    - zomato_partner_api:
        restaurant_id: "${ZOMATO_REST_ID}"
        auth: oauth2
        capabilities: [download_settlement, order_history, dispute_management]
    - ondc_connector:
        seller_app_id: "${ONDC_SELLER_APP}"
        capabilities: [order_history, payment_status]
    - pos_connector:
        provider: petpooja
        outlet_id: "${PETPOOJA_OUTLET_ID}"
        auth: "${PETPOOJA_API_KEY}"
        capabilities: [order_history, payment_records, daily_summary]
    - accounting_connector:
        provider: zoho_books
        organisation_id: "${ZOHO_ORG_ID}"
        capabilities: [create_journal_entry, update_invoice, read_accounts]
    - code_execution:
        runtime: python3.12
        packages: [pandas, numpy]
        memory_mb: 256
    - document_generation:
        engine: openpyxl_jinja2
        templates: [reconciliation_register, dispute_evidence, daily_summary_report]
    - email:
        provider: smtp
        from: "ops@spiceroute.in"
    - browser_rpa:
        target: "https://partner.swiggy.com"
        capabilities: [raise_dispute, upload_evidence]
    - whatsapp_business:
        account_id: "${WA_ACCOUNT_ID}"
        template: daily_reconciliation_summary

  memory:
    type: short_term
    keys: [dispute_history, settlement_baselines, commission_rates_by_platform]
    ttl_days: 90

  hitl:
    require_approval_for:
      - action: raise_dispute
        condition: "dispute_amount > 5000"
    approvers: ["owner@spiceroute.in"]
    timeout_hours: 12

  parallelism:
    max_concurrent_platforms: 3

  compliance:
    audit_trail: true
    data_classification: financial

  replan_on_failure: true
  max_iterations: 4
  notify_on_completion: ["owner@spiceroute.in", "accounts@spiceroute.in"]
```
