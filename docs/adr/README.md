# AgentVerse Architecture Decision Records

Architectural decisions for AgentVerse as a **multi-vertical product platform**. Start with the foundational ADR, then the per-domain ADRs.

## Foundational

- [ADR-0001: AgentVerse as a Multi-Vertical Product Platform](0001-agentverse-multi-vertical-product-platform.md) — the 8 platform-level decisions (domains-as-data, Solution-as-SKU, API+RPA-farm connector substrate, cost-is-COGS, P0-before-scale, 4 workload shapes), the **scored monetization prioritization**, packaging/GTM, and the per-domain ADR template.

**Monetization verdict (from ADR-0001):** the profit engine is **GST & Tax + CA Firm (07 + 27)** — mandatory recurring compliance × the 3.5-lakh-CA channel (lowest CAC). The zero-connector fast-cash beachhead is **Sales/CRM (04) + Legal (06)**. Rollout is land-and-expand: beachhead → GST/CA → TAM-ordered fan-out.

## Per-Domain ADRs (`domains/`)

Each applies ADR-0001 to one vertical: market/monetization, every use case mapped to an agent or template (with connector bucket, scale pattern, HITL), connectors required, knowledge collections, compliance posture, scale/cost drivers, phasing, and KPIs. Sourced from `docs/domains/NN-*/use-cases.md`.

### Money engines & fast-cash (build first)
- [07 GST & Tax](domains/07-gst-tax.md) · [27 Accounting / CA Firm](domains/27-accounting-ca-firm.md) — **profit engine** (GST/ITR/MCA21 RPA)
- [04 Sales & CRM](domains/04-sales-crm.md) · [06 Legal](domains/06-legal.md) · [18 Customer Support](domains/18-customer-support.md) · [02 Software Eng](domains/02-software-engineering.md) · [03 DevOps](domains/03-devops.md) — **Bucket-1 PLG beachhead** (zero new connectors)

### High-TAM (build after beachhead)
- [12 Healthcare](domains/12-healthcare.md) · [20 Banking & FinTech](domains/20-banking-fintech.md) · [10 E-commerce](domains/10-ecommerce.md) · [15 Cybersecurity](domains/15-cybersecurity.md) · [24 Pharmaceutical](domains/24-pharmaceutical.md) · [25 Telecom](domains/25-telecom.md)

### Full vertical set
- Core ops: [01 HR](domains/01-hr.md) · [05 Operations](domains/05-operations.md) · [08 Invoicing/Finance](domains/08-invoicing-finance.md)
- Finance/compliance: [17 Insurance](domains/17-insurance.md) · [34 Wealth Mgmt](domains/34-wealth-management.md)
- Government/public: [09 Government Portal](domains/09-government-portal.md) · [37 Public Health](domains/37-public-health.md) · [32 Non-Profit/NGO](domains/32-nonprofit-ngo.md)
- Consumer/market: [11 Education](domains/11-education.md) · [13 Real Estate](domains/13-real-estate.md) · [21 Agriculture](domains/21-agriculture.md) · [28 Food/Restaurant](domains/28-food-restaurant.md) · [22 Hospitality/Travel](domains/22-hospitality-travel.md) · [35 Fashion/Apparel](domains/35-fashion-apparel.md)
- Industry: [16 Logistics](domains/16-logistics.md) · [19 Manufacturing](domains/19-manufacturing.md) · [26 Construction](domains/26-construction.md) · [30 Energy/Utilities](domains/30-energy-utilities.md) · [31 Automobile/EV](domains/31-automobile.md)
- Growth/services: [14 Marketing](domains/14-marketing.md) · [23 Media/Publishing](domains/23-media-publishing.md) · [29 Recruitment/Staffing](domains/29-recruitment-staffing.md) · [33 Events/MICE](domains/33-events-management.md) · [36 Architecture/Interior](domains/36-architecture-interior-design.md)

## Related plans
- Master plan: `docs/superpowers/plans/2026-07-04-agentverse-world-class-master-plan.md` (defect registry + 14 capability phases)
- Domain build-out: `docs/superpowers/plans/2026-07-04-domain-solutions-templates-marketplace.md` (connectors, 200 agents, 150 templates, 37 Solutions)

## Cross-domain build insights (from the 37 ADRs)
- **Vision/perception** is a shared net-new capability across agriculture (crop disease), food (SOP photos), fashion (catalog QC), architecture (site photos) — build once in `perception/`.
- **Cost model splits by workload:** GST/CA are RPA-session-bound; legal/finance/support are LLM-token-bound — they scale and price differently.
- **RPA maintenance is the dominant ongoing cost** for government/regulatory verticals (state-fragmented RERA, 29 SERC portals, Vahan, Passport Seva) — treat RPA scripts as versioned, monitored data.
- **Manufacturing/banking/telecom** need enterprise New-API integrations (MES/SCADA/SAP, core-banking, OSS/BSS), not RPA — different build profile and sales motion.
